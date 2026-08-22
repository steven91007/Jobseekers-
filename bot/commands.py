"""Slash commands, all under /jobs.

Anything that scrapes must defer first: Discord kills an interaction that is not
acknowledged within 3 seconds, and a scrape takes far longer. After deferring
there is a 15-minute window to follow up.
"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.app_commands import Choice

from linkedin_scraper import JOB_TYPE_LABEL, Outcome

from . import db, embeds
from .config import Config
from .pusher import Pusher
from .scrape import scrape

log = logging.getLogger(__name__)

WORK_TYPE_CHOICES = [
    Choice(name="On-site", value="onsite"),
    Choice(name="Remote", value="remote"),
    Choice(name="Hybrid", value="hybrid"),
]
# Derived from the scraper's own vocabulary so the two can never drift apart.
JOB_TYPE_CHOICES = [
    Choice(name=label, value=key) for key, label in JOB_TYPE_LABEL.items()
]


def _value(choice: Choice | None) -> str:
    return choice.value if choice else ""


class JobsGroup(app_commands.Group):
    def __init__(self, conn, cfg: Config, pusher: Pusher):
        super().__init__(
            name="jobs",
            description="LinkedIn 職缺訂閱與每日推播",
            guild_only=True,
        )
        self.conn = conn
        self.cfg = cfg
        self.pusher = pusher

    # --- helpers ----------------------------------------------------------

    def _may_manage(self, interaction: discord.Interaction, sub) -> bool:
        perms = interaction.user.guild_permissions
        return perms.manage_guild or sub.creator_id == interaction.user.id

    async def _subscription_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[int]]:
        subs = db.list_subscriptions(self.conn, guild_id=interaction.guild_id)
        current = current.lower()
        out = []
        for sub in subs:
            label = f"#{sub.id} {sub.keyword} @ {sub.location or 'anywhere'}"
            if current and current not in label.lower():
                continue
            out.append(Choice(name=label[:100], value=sub.id))
        return out[:25]

    # --- commands ---------------------------------------------------------

    @app_commands.command(description="在這個頻道訂閱職缺，每天自動推播新的")
    @app_commands.describe(
        keyword="搜尋關鍵字，例如 Python、Data Engineer",
        location="地點，留空為不限",
        work_type="工作型態",
        job_type="工作類型",
        english_only="只要英文的職缺標題／公司名",
        max_results="每次搜尋抓幾筆（5-50）",
        channel="推播到哪個頻道，預設為目前頻道",
    )
    @app_commands.choices(work_type=WORK_TYPE_CHOICES, job_type=JOB_TYPE_CHOICES)
    @app_commands.default_permissions(manage_guild=True)
    async def subscribe(
        self,
        interaction: discord.Interaction,
        keyword: app_commands.Range[str, 2, 100],
        location: str = "",
        work_type: Choice[str] | None = None,
        job_type: Choice[str] | None = None,
        english_only: bool = False,
        max_results: app_commands.Range[int, 5, 50] = 15,
        channel: discord.TextChannel | None = None,
    ) -> None:
        target = channel or interaction.channel
        perms = target.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"我沒辦法在 {target.mention} 發言或發送 embed，請先調整權限。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        sub_id = db.add_subscription(
            self.conn,
            guild_id=interaction.guild_id,
            channel_id=target.id,
            creator_id=interaction.user.id,
            keyword=keyword,
            location=location,
            work_type=_value(work_type),
            job_type=_value(job_type),
            english_only=english_only,
            max_results=max_results,
        )
        if sub_id is None:
            await interaction.followup.send(
                f"{target.mention} 已經有一組完全相同條件的訂閱了。用 `/jobs list` 查看。",
                ephemeral=True,
            )
            return

        # If a cycle is already running, don't queue behind it — the lock could
        # be held for longer than the 15-minute followup window, and a second
        # concurrent scrape is exactly what gets the IP blocked.
        if self.pusher.busy:
            await interaction.followup.send(
                f"訂閱 #{sub_id} 已建立。目前有推播正在進行，基準線會在下次執行時建立"
                f"（或稍後用 `/jobs run` 手動觸發）。"
            )
            return

        # Seed immediately so the user gets confirmation now rather than
        # tomorrow, and so today's existing listings become the baseline.
        # run_all() rather than run_one() because it takes the scrape lock.
        report = await self.pusher.run_all(only_id=sub_id)
        result = report.results[0] if report.results else None

        if result is not None and result.outcome in (Outcome.OK, Outcome.EMPTY_OK):
            await interaction.followup.send(
                f"訂閱 #{sub_id} 已建立，基準線已寫入 {target.mention}。"
            )
        else:
            outcome = result.outcome.value if result else "UNKNOWN"
            await interaction.followup.send(
                f"訂閱 #{sub_id} 已建立，但初次抓取失敗（`{outcome}`）。"
                f"明天的排程會再試一次，或用 `/jobs run` 立即重試。"
            )

    @app_commands.command(name="list", description="列出這個伺服器的所有訂閱")
    async def list_subs(self, interaction: discord.Interaction) -> None:
        subs = db.list_subscriptions(self.conn, guild_id=interaction.guild_id)
        await interaction.response.send_message(
            embeds=embeds.subscriptions_embeds(subs), ephemeral=True
        )

    @app_commands.command(description="刪除一個訂閱（連同它的已推播紀錄）")
    @app_commands.describe(subscription_id="要刪除的訂閱")
    @app_commands.default_permissions(manage_guild=True)
    async def remove(
        self, interaction: discord.Interaction, subscription_id: int
    ) -> None:
        sub = db.get_subscription(self.conn, subscription_id)
        if sub is None or sub.guild_id != interaction.guild_id:
            await interaction.response.send_message(
                f"找不到訂閱 #{subscription_id}。", ephemeral=True
            )
            return
        if not self._may_manage(interaction, sub):
            await interaction.response.send_message(
                "你只能刪除自己建立的訂閱。", ephemeral=True
            )
            return
        db.remove_subscription(self.conn, subscription_id)
        await interaction.response.send_message(
            f"已刪除訂閱 #{subscription_id}（{sub.describe()}）。", ephemeral=True
        )

    @app_commands.command(description="暫停或恢復一個訂閱")
    @app_commands.describe(subscription_id="要調整的訂閱", enabled="True 恢復，False 暫停")
    @app_commands.default_permissions(manage_guild=True)
    async def toggle(
        self, interaction: discord.Interaction, subscription_id: int, enabled: bool
    ) -> None:
        sub = db.get_subscription(self.conn, subscription_id)
        if sub is None or sub.guild_id != interaction.guild_id:
            await interaction.response.send_message(
                f"找不到訂閱 #{subscription_id}。", ephemeral=True
            )
            return
        if not self._may_manage(interaction, sub):
            await interaction.response.send_message(
                "你只能調整自己建立的訂閱。", ephemeral=True
            )
            return
        db.set_active(self.conn, subscription_id, enabled, "manual")
        await interaction.response.send_message(
            f"訂閱 #{subscription_id} 已{'恢復' if enabled else '暫停'}。", ephemeral=True
        )

    @app_commands.command(description="立即試搜，不建立訂閱、不影響去重紀錄")
    @app_commands.describe(
        keyword="搜尋關鍵字",
        location="地點，留空為不限",
        work_type="工作型態",
        job_type="工作類型",
        english_only="只要英文的職缺標題／公司名",
        max_results="抓幾筆（1-25）",
    )
    @app_commands.choices(work_type=WORK_TYPE_CHOICES, job_type=JOB_TYPE_CHOICES)
    async def preview(
        self,
        interaction: discord.Interaction,
        keyword: app_commands.Range[str, 2, 100],
        location: str = "",
        work_type: Choice[str] | None = None,
        job_type: Choice[str] | None = None,
        english_only: bool = False,
        max_results: app_commands.Range[int, 1, 25] = 5,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        result = await scrape(
            self.cfg,
            keyword=keyword,
            location=location,
            work_type=_value(work_type),
            job_type=_value(job_type),
            english_only=english_only,
            max_results=max_results,
        )

        if result.outcome not in (Outcome.OK, Outcome.EMPTY_OK):
            await interaction.followup.send(
                embed=embeds.problem_embed(None, result.outcome, result.detail),
                ephemeral=True,
            )
            return

        note = f"抓到 {result.raw_card_count} 張卡片，解析出 {result.parsed_count} 筆。"
        batches = list(embeds.batch_embeds(
            embeds.preview_embeds(result.jobs, note)
        ))
        await interaction.followup.send(embeds=batches[0], ephemeral=True)
        for batch in batches[1:]:
            await interaction.followup.send(embeds=batch, ephemeral=True)

    @app_commands.command(description="立刻執行一次推播（真的會去重與發文）")
    @app_commands.describe(subscription_id="只跑這一個訂閱，留空則跑全部")
    @app_commands.default_permissions(manage_guild=True)
    async def run(
        self, interaction: discord.Interaction, subscription_id: int | None = None
    ) -> None:
        if self.pusher.busy:
            await interaction.response.send_message(
                "已經有一次推播正在進行中，請稍候。", ephemeral=True
            )
            return

        if subscription_id is not None:
            sub = db.get_subscription(self.conn, subscription_id)
            if sub is None or sub.guild_id != interaction.guild_id:
                await interaction.response.send_message(
                    f"找不到訂閱 #{subscription_id}。", ephemeral=True
                )
                return

        await interaction.response.defer(thinking=True, ephemeral=True)
        # A full run can outlive the 15-minute followup window, so acknowledge
        # now and report into the channel when it finishes.
        await interaction.followup.send("已開始推播，完成後會回報。", ephemeral=True)

        async def _run() -> None:
            try:
                report = await self.pusher.run_all(only_id=subscription_id)
                await interaction.channel.send(f"推播完成：{report.summary()}")
            except Exception:
                log.exception("manual run failed")
                try:
                    await interaction.channel.send("推播過程發生未預期錯誤，請查看 log。")
                except discord.HTTPException:
                    pass

        asyncio.create_task(_run())

    @app_commands.command(description="查看排程與每個訂閱的健康狀態")
    async def status(self, interaction: discord.Interaction) -> None:
        subs = db.list_subscriptions(self.conn, guild_id=interaction.guild_id)
        last = db.last_finished_run(self.conn)

        embed = discord.Embed(title="Bot 狀態", color=embeds.DEFAULT_COLOR)
        embed.add_field(
            name="每日推播時間",
            value=f"{self.cfg.push_time.strftime('%H:%M')} ({self.cfg.tz})",
            inline=True,
        )
        embed.add_field(
            name="目前狀態",
            value="推播進行中" if self.pusher.busy else "待命",
            inline=True,
        )
        embed.add_field(
            name="上次完成的 cycle",
            value=(f"{last['run_date']} — {last['outcome_summary']}"
                   if last else "尚未執行過"),
            inline=False,
        )

        if subs:
            lines = []
            for sub in subs:
                mark = "🟢" if sub.active else "⏸️"
                if sub.last_outcome and sub.last_outcome not in ("OK", "EMPTY_OK"):
                    mark = "⚠️"
                lines.append(
                    f"{mark} `#{sub.id}` {sub.keyword} — "
                    f"raw {sub.last_raw_count} / new {sub.last_new_count} / "
                    f"{sub.last_outcome or '尚未執行'}"
                )
            embed.add_field(
                name=f"訂閱（{len(subs)}）",
                value=embeds.truncate("\n".join(lines), embeds.FIELD_VALUE_LIMIT),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


def setup(tree: app_commands.CommandTree, conn, cfg: Config, pusher: Pusher) -> JobsGroup:
    group = JobsGroup(conn, cfg, pusher)
    for command in (group.remove, group.toggle, group.run):
        command.autocomplete("subscription_id")(group._subscription_autocomplete)
    tree.add_command(group)
    return group
