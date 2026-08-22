"""Rendering jobs as Discord embeds, within Discord's limits.

The limit people miss is the 6000-character cap, which applies to the *sum*
across all embeds in one message rather than to each embed. It only surfaces
under load, as an opaque 400. `discord.Embed.__len__` returns an embed's total
character count, so batching measures rather than guesses.
"""

from typing import Iterator

import discord

from linkedin_scraper import JOB_TYPE_LABEL, Outcome

from .db import Subscription

MAX_EMBEDS_PER_MESSAGE = 10
MAX_CHARS_PER_MESSAGE = 6000

# Deliberately under the hard caps so a long title can never tip a batch over.
BATCH_EMBEDS = 5
BATCH_CHARS = 5500

TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_VALUE_LIMIT = 1024

# Mirrors main.py's WORK_TYPE_STYLE so the CLI and Discord agree.
WORK_TYPE_COLOR = {
    "Remote": discord.Color.green(),
    "On-site": discord.Color.blue(),
    "Hybrid": discord.Color.magenta(),
}
DEFAULT_COLOR = discord.Color.blurple()

OUTCOME_BLURB = {
    Outcome.BLOCKED: "LinkedIn 擋下了這次請求（authwall / 403 / 999）。",
    Outcome.RATE_LIMITED: "被 LinkedIn 限流（429），重試已用盡。",
    Outcome.TRANSPORT_ERROR: "連線失敗（逾時或網路問題）。",
    Outcome.PARSE_DRIFT: "抓到了網頁但解析不出任何職缺 —— LinkedIn 很可能改版了，爬蟲需要更新。",
    Outcome.EMPTY_SUSPICIOUS: "LinkedIn 回了一個異常空的頁面，可能是軟性封鎖。",
    Outcome.EMPTY_OK: "這次搜尋沒有符合條件的職缺。",
}


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def job_embed(job: dict, sub: Subscription | None = None) -> discord.Embed:
    work_type = job.get("work_type", "N/A")
    lines = [f"**{truncate(job.get('company', 'N/A'), 200)}**"]

    place = job.get("location", "N/A")
    lines.append(f"{place} · {work_type}" if work_type != "N/A" else place)

    posted = job.get("posted_date", "N/A")
    if posted != "N/A":
        lines.append(f"Posted {posted}")

    embed = discord.Embed(
        title=truncate(job.get("title", "N/A"), TITLE_LIMIT),
        url=job.get("url"),
        description=truncate("\n".join(lines), DESCRIPTION_LIMIT),
        color=WORK_TYPE_COLOR.get(work_type, DEFAULT_COLOR),
    )
    if sub is not None:
        embed.set_footer(text=truncate(f"#{sub.id} · {sub.keyword}", 2048))
    return embed


def batch_embeds(embeds: list[discord.Embed]) -> Iterator[list[discord.Embed]]:
    """Split embeds into messages that respect both the count and character caps."""
    batch: list[discord.Embed] = []
    chars = 0
    for embed in embeds:
        size = len(embed)
        if batch and (len(batch) >= BATCH_EMBEDS or chars + size > BATCH_CHARS):
            yield batch
            batch, chars = [], 0
        batch.append(embed)
        chars += size
    if batch:
        yield batch


def header_embed(
    sub: Subscription, new_count: int, shown: int, stale_days: int | None = None
) -> discord.Embed:
    title = f"{new_count} 筆新職缺" if new_count != shown else f"{shown} 筆新職缺"
    embed = discord.Embed(
        title=title,
        description=sub.describe(),
        color=DEFAULT_COLOR,
    )
    if shown < new_count:
        embed.add_field(
            name="顯示筆數",
            value=f"本次顯示 {shown} / 共 {new_count} 筆（其餘已標記為看過，不會重複排隊）",
            inline=False,
        )
    if stale_days:
        embed.add_field(
            name="注意",
            value=f"bot 離線約 {stale_days} 天，較舊的職缺可能已經下架。",
            inline=False,
        )
    return embed


def seeded_embed(sub: Subscription, baselined: int, push_time: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"訂閱 #{sub.id} 已建立",
        description=sub.describe(),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="基準線",
        value=(
            f"已記錄目前 {baselined} 筆既有職缺，**這次不會推播**。\n"
            f"之後每天 {push_time} 只推新增的職缺。"
        ),
        inline=False,
    )
    embed.set_footer(text=f"頻道 #{sub.channel_id} · 用 /jobs run 可立即測試")
    return embed


def problem_embed(sub: Subscription | None, outcome: Outcome, detail: str = "") -> discord.Embed:
    embed = discord.Embed(
        title="⚠️ 爬取失敗",
        description=OUTCOME_BLURB.get(outcome, f"未知狀態：{outcome.value}"),
        color=discord.Color.red(),
    )
    if sub is not None:
        embed.add_field(name="訂閱", value=f"#{sub.id} · {sub.describe()}", inline=False)
    if detail:
        embed.add_field(name="細節", value=truncate(detail, FIELD_VALUE_LIMIT), inline=False)
    embed.set_footer(text=outcome.value)
    return embed


def subscriptions_embeds(subs: list[Subscription]) -> list[discord.Embed]:
    """One field per subscription, paginated at 25 fields per embed."""
    if not subs:
        return [discord.Embed(
            title="目前沒有任何訂閱",
            description="用 `/jobs subscribe` 建立第一個。",
            color=DEFAULT_COLOR,
        )]

    pages: list[discord.Embed] = []
    for start in range(0, len(subs), 25):
        embed = discord.Embed(title="職缺訂閱", color=DEFAULT_COLOR)
        for sub in subs[start:start + 25]:
            status = "🟢 啟用中" if sub.active else f"⏸️ 已停用（{sub.deactivated_reason or '手動'}）"
            if sub.active and sub.last_outcome and sub.last_outcome not in ("OK", "EMPTY_OK"):
                status = f"⚠️ {sub.last_outcome}"
            value = (
                f"{sub.describe()}\n"
                f"<#{sub.channel_id}> · {status}\n"
                f"上次執行：{sub.last_run_at or '尚未執行'}"
            )
            embed.add_field(
                name=f"#{sub.id} {truncate(sub.keyword, 100)}",
                value=truncate(value, FIELD_VALUE_LIMIT),
                inline=False,
            )
        pages.append(embed)
    return pages


def preview_embeds(jobs: list[dict], result_note: str) -> list[discord.Embed]:
    embeds = [discord.Embed(
        title=f"預覽：{len(jobs)} 筆職缺",
        description=result_note,
        color=DEFAULT_COLOR,
    )]
    embeds.extend(job_embed(job) for job in jobs)
    return embeds


def detail_embed(job: dict, detail: dict) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(job.get("title", "職缺詳情"), TITLE_LIMIT),
        url=job.get("url"),
        description=truncate(detail.get("description", ""), DESCRIPTION_LIMIT),
        color=DEFAULT_COLOR,
    )
    for name, value in list(detail.get("criteria", {}).items())[:6]:
        embed.add_field(
            name=truncate(name, TITLE_LIMIT),
            value=truncate(value, FIELD_VALUE_LIMIT),
            inline=True,
        )
    return embed


def job_type_label(key: str) -> str:
    return JOB_TYPE_LABEL.get(key, key or "Any")
