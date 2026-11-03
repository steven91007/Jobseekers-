"""The daily push cycle.

Scheduling is a fast ticker plus a database claim, not a once-a-day timer.
`tasks.loop(time=09:00)` fires exactly once and silently loses the day if the
machine was asleep at 09:00 — which, on a Windows desktop, it will be. Here the
ticker asks every few minutes "is it past push time, and is today unclaimed?",
and the UNIQUE constraint on run_marker.run_date is what makes it exactly-once
across sleeps, restarts and gateway reconnects.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import discord

from linkedin_scraper import Outcome

from . import db, embeds
from .config import Config
from .db import Subscription
from .scrape import check_visa, scrape_subscription

log = logging.getLogger(__name__)

HARD_FAILURES = {Outcome.BLOCKED, Outcome.RATE_LIMITED, Outcome.TRANSPORT_ERROR}
ALERT_OUTCOMES = HARD_FAILURES | {Outcome.PARSE_DRIFT}

# Two blocks in a row means the IP is in trouble; the rest of the cycle is
# abandoned rather than spent digging deeper.
CIRCUIT_BREAK_AFTER = 2
EMPTY_STREAK_ALERT = 3
PLATFORM_FAILURE_RATIO = 0.8
STALE_RUN_DAYS = 3


@dataclass
class SubResult:
    sub_id: int
    outcome: Outcome
    raw_count: int = 0
    new_count: int = 0
    posted_count: int = 0
    error: str | None = None
    skipped: bool = False


@dataclass
class RunReport:
    results: list[SubResult] = field(default_factory=list)
    aborted: bool = False

    @property
    def posted(self) -> int:
        return sum(r.posted_count for r in self.results)

    def summary(self) -> str:
        if not self.results:
            return "沒有啟用中的訂閱"
        failed = sum(1 for r in self.results if r.outcome in ALERT_OUTCOMES)
        parts = [f"{len(self.results)} 個訂閱", f"{self.posted} 筆新職缺"]
        if failed:
            parts.append(f"{failed} 個失敗")
        if self.aborted:
            parts.append("（因連續封鎖而中止）")
        return "、".join(parts)


class Pusher:
    def __init__(self, client: discord.Client, conn, cfg: Config):
        self.client = client
        self.conn = conn
        self.cfg = cfg
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    # --- scheduling -------------------------------------------------------

    async def tick(self) -> None:
        """Ticker body. Must never raise — the loop dies permanently if it does."""
        if not self.client.is_ready():
            return
        if self.busy:
            return

        now = datetime.now(self.cfg.tz)
        # Compare wall-clock components: a bare tz-aware `time` has no usable
        # utcoffset, so `<` on them silently degrades to a naive comparison.
        if (now.hour, now.minute) < (self.cfg.push_time.hour, self.cfg.push_time.minute):
            return

        run_date = now.date().isoformat()
        stale = self._stale_days(now.date())
        if not db.claim_run(self.conn, run_date):
            return

        log.info("daily cycle claimed for %s", run_date)
        report = await self.run_all(stale_days=stale)
        db.finish_marker(self.conn, run_date, report.summary())
        await self._update_presence(report)

    def _stale_days(self, today: date) -> int | None:
        """How long since the last completed cycle, if that gap was unusual."""
        row = db.last_finished_run(self.conn)
        if row is None:
            return None
        try:
            gap = (today - date.fromisoformat(row["run_date"])).days
        except ValueError:
            return None
        return gap if gap > STALE_RUN_DAYS else None

    # --- the cycle --------------------------------------------------------

    async def run_all(
        self, *, only_id: int | None = None, stale_days: int | None = None
    ) -> RunReport:
        async with self._lock:
            return await self._run_all_locked(only_id=only_id, stale_days=stale_days)

    async def _run_all_locked(
        self, *, only_id: int | None, stale_days: int | None
    ) -> RunReport:
        if only_id is not None:
            sub = db.get_subscription(self.conn, only_id)
            subs = [sub] if sub else []
        else:
            subs = db.due_subscriptions(self.conn)

        report = RunReport()
        consecutive_blocks = 0

        for index, sub in enumerate(subs):
            if index:
                await asyncio.sleep(random.uniform(self.cfg.gap_min, self.cfg.gap_max))

            try:
                result = await self.run_one(sub, stale_days=stale_days)
            except Exception:
                # One bad subscription must never abort the others.
                log.exception("subscription #%s raised", sub.id)
                result = SubResult(sub.id, Outcome.TRANSPORT_ERROR, error="unhandled")
                db.finish_run(
                    self.conn, sub.id, outcome="INTERNAL_ERROR",
                    raw_count=0, new_count=0, failed=True,
                )
            report.results.append(result)

            if result.outcome is Outcome.BLOCKED:
                consecutive_blocks += 1
                if consecutive_blocks >= CIRCUIT_BREAK_AFTER:
                    log.error("circuit breaker: %d consecutive blocks, aborting cycle",
                              consecutive_blocks)
                    report.aborted = True
                    for skipped in subs[index + 1:]:
                        report.results.append(
                            SubResult(skipped.id, Outcome.BLOCKED, skipped=True)
                        )
                    break
            else:
                consecutive_blocks = 0

        await self._check_platform_failure(report)
        db.prune_seen(self.conn, self.cfg.seen_retention_days)
        db.prune_run_log(self.conn)
        return report

    async def run_one(
        self, sub: Subscription, *, stale_days: int | None = None
    ) -> SubResult:
        started = time.monotonic()

        channel = await self._resolve_channel(sub)
        if channel is None:
            return SubResult(sub.id, Outcome.TRANSPORT_ERROR, error="channel unavailable")

        result = await scrape_subscription(self.cfg, sub)
        raw = result.raw_card_count
        duration_ms = int((time.monotonic() - started) * 1000)

        if result.outcome in ALERT_OUTCOMES:
            db.finish_run(
                self.conn, sub.id, outcome=result.outcome.value,
                raw_count=raw, new_count=0, failed=True,
            )
            db.log_run(
                self.conn, subscription_id=sub.id, outcome=result.outcome.value,
                raw_count=raw, duration_ms=duration_ms, error=result.detail,
            )
            await self._alert_subscription(sub, channel, result.outcome, result.detail)
            return SubResult(sub.id, result.outcome, raw_count=raw, error=result.detail)

        ids = [job["job_id"] for job in result.jobs]

        # Cold start: record everything as already-seen and post nothing, so a
        # brand-new subscription does not dump 50 existing listings at once.
        if not sub.seeded:
            db.mark_seen(self.conn, sub.id, ids)
            db.finish_run(
                self.conn, sub.id, outcome=result.outcome.value,
                raw_count=raw, new_count=0, seeded=True,
            )
            db.log_run(
                self.conn, subscription_id=sub.id, outcome=result.outcome.value,
                raw_count=raw, duration_ms=duration_ms,
            )
            await self._send(channel, sub, [embeds.seeded_embed(
                sub, len(ids), self.cfg.push_time.strftime("%H:%M"))])
            # A subscription whose *first* run failed is still flagged broken;
            # clear it here too, or it can never recover from the seeding path.
            await self._clear_alert(sub, channel, result.outcome)
            return SubResult(sub.id, result.outcome, raw_count=raw)

        new_ids = db.filter_new_job_ids(self.conn, sub.id, ids)
        new_jobs = [job for job in result.jobs if job["job_id"] in new_ids]

        posted = 0
        if new_jobs and sub.visa_check:
            # Only the jobs that will be shown; each check is one more request.
            await check_visa(self.cfg, new_jobs[: self.cfg.max_new_per_push])
        if new_jobs:
            posted = await self._post_jobs(channel, sub, new_jobs, stale_days)

        empty = raw == 0
        db.finish_run(
            self.conn, sub.id, outcome=result.outcome.value,
            raw_count=raw, new_count=len(new_jobs), empty=empty,
        )
        db.log_run(
            self.conn, subscription_id=sub.id, outcome=result.outcome.value,
            raw_count=raw, new_count=len(new_jobs), posted_count=posted,
            duration_ms=duration_ms,
        )

        await self._check_empty_streak(sub, channel, raw)
        await self._clear_alert(sub, channel, result.outcome)

        log.info(
            "sub=%s kw=%r raw=%d parsed=%d new=%d posted=%d outcome=%s %dms",
            sub.id, sub.keyword, raw, result.parsed_count, len(new_jobs),
            posted, result.outcome.value, duration_ms,
        )
        return SubResult(
            sub.id, result.outcome, raw_count=raw,
            new_count=len(new_jobs), posted_count=posted,
        )

    async def _post_jobs(
        self, channel, sub: Subscription, new_jobs: list[dict], stale_days: int | None
    ) -> int:
        shown = new_jobs[: self.cfg.max_new_per_push]
        overflow = new_jobs[self.cfg.max_new_per_push:]

        header = embeds.header_embed(sub, len(new_jobs), len(shown), stale_days)
        if not await self._send(channel, sub, [header]):
            return 0

        posted = 0
        for batch in embeds.batch_embeds([embeds.job_embed(j, sub) for j in shown]):
            if not await self._send(channel, sub, batch):
                break
            # Mark seen only after the send lands. Marking first would lose
            # these jobs permanently on a transient failure.
            db.mark_seen(
                self.conn, sub.id,
                [j["job_id"] for j in shown[posted: posted + len(batch)]],
            )
            posted += len(batch)
            await asyncio.sleep(1)

        # Anything over the per-push cap is still marked seen, or it would
        # re-queue every day and permanently block newer postings.
        if overflow:
            db.mark_seen(self.conn, sub.id, [j["job_id"] for j in overflow])
        return posted

    # --- Discord plumbing -------------------------------------------------

    async def _resolve_channel(self, sub: Subscription):
        channel = self.client.get_channel(sub.channel_id)
        if channel is not None:
            return channel
        try:
            return await self.client.fetch_channel(sub.channel_id)
        except discord.NotFound:
            log.warning("sub #%s: channel %s is gone, deactivating",
                        sub.id, sub.channel_id)
            db.set_active(self.conn, sub.id, False, "channel_deleted")
            return None
        except discord.Forbidden:
            # Permissions get fixed; do not deactivate on the first sighting.
            log.warning("sub #%s: no access to channel %s", sub.id, sub.channel_id)
            db.finish_run(
                self.conn, sub.id, outcome="NO_ACCESS",
                raw_count=0, new_count=0, failed=True,
            )
            fresh = db.get_subscription(self.conn, sub.id)
            if fresh and fresh.consecutive_failures >= 3:
                db.set_active(self.conn, sub.id, False, "no_channel_access")
            return None
        except discord.HTTPException:
            log.exception("sub #%s: could not resolve channel", sub.id)
            return None

    async def _send(self, channel, sub: Subscription, batch: list[discord.Embed]) -> bool:
        try:
            await channel.send(embeds=batch)
            return True
        except discord.NotFound:
            db.set_active(self.conn, sub.id, False, "channel_deleted")
        except discord.Forbidden:
            log.warning("sub #%s: send forbidden in %s", sub.id, sub.channel_id)
        except discord.HTTPException:
            log.exception("sub #%s: send failed (transient)", sub.id)
        return False

    async def _update_presence(self, report: RunReport) -> None:
        broken = sum(1 for r in report.results if r.outcome in ALERT_OUTCOMES)
        stamp = datetime.now(self.cfg.tz).strftime("%H:%M")
        text = (f"⚠️ {broken} 個訂閱失敗" if broken
                else f"✅ {stamp} · {report.posted} 筆新職缺")
        try:
            await self.client.change_presence(activity=discord.Game(name=text[:128]))
        except discord.HTTPException:
            log.debug("could not update presence", exc_info=True)

    # --- alerting (transition-only, so a 5-minute ticker cannot spam) ------

    async def _alert_subscription(self, sub, channel, outcome: Outcome, detail: str):
        if sub.alert_state == "broken":
            return
        db.set_alert_state(self.conn, sub.id, "broken")
        embed = embeds.problem_embed(sub, outcome, detail)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.debug("could not post alert to channel", exc_info=True)
        await self._dm_owner(embed)

    async def _clear_alert(self, sub: Subscription, channel, outcome: Outcome) -> None:
        if sub.alert_state != "broken":
            return
        db.set_alert_state(self.conn, sub.id, "healthy")
        try:
            await channel.send(embed=discord.Embed(
                title="✅ 已恢復",
                description=f"訂閱 #{sub.id}（{sub.describe()}）的爬取已恢復正常。",
                color=discord.Color.green(),
            ))
        except discord.HTTPException:
            log.debug("could not post recovery notice", exc_info=True)

    async def _check_empty_streak(self, sub: Subscription, channel, raw: int) -> None:
        if raw > 0 or sub.last_ok_at is None:
            return
        fresh = db.get_subscription(self.conn, sub.id)
        if fresh and fresh.consecutive_empty_runs >= EMPTY_STREAK_ALERT:
            await self._alert_subscription(
                fresh, channel, Outcome.EMPTY_SUSPICIOUS,
                f"連續 {fresh.consecutive_empty_runs} 次抓到 0 筆，但此訂閱過去曾有結果。",
            )

    async def _check_platform_failure(self, report: RunReport) -> None:
        """N subscriptions all returning nothing is one platform failure, not N."""
        live = [r for r in report.results if not r.skipped]
        if len(live) < 2:
            return
        broken = sum(1 for r in live if r.raw_count == 0)
        if broken / len(live) < PLATFORM_FAILURE_RATIO:
            return
        log.error("platform-level failure: %d/%d subscriptions returned nothing",
                  broken, len(live))
        await self._dm_owner(discord.Embed(
            title="⚠️ 可能是平台層級的失效",
            description=(
                f"本次 cycle 有 {broken}/{len(live)} 個訂閱抓到 0 筆。\n"
                "這通常代表被 LinkedIn 封鎖，或爬蟲的 selector 已經失效，"
                "而不是真的沒有職缺。"
            ),
            color=discord.Color.red(),
        ))

    async def _dm_owner(self, embed: discord.Embed) -> None:
        if not self.cfg.owner_id:
            return
        try:
            owner = self.client.get_user(self.cfg.owner_id) or \
                await self.client.fetch_user(self.cfg.owner_id)
            await owner.send(embed=embed)
        except (discord.HTTPException, discord.NotFound):
            log.debug("could not DM owner", exc_info=True)
