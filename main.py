import sys
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

import linkedin_scraper

console = Console()

JOB_TYPE_LABEL = {
    "fulltime":   "全職",
    "parttime":   "兼職",
    "contract":   "合約/自由接案",
    "temporary":  "臨時工",
    "internship": "實習",
    "volunteer":  "志工",
}

WORK_TYPE_STYLE = {
    "Remote":  "bold green",
    "On-site": "bold blue",
    "Hybrid":  "bold magenta",
}

JOB_TYPE_STYLE = {
    "fulltime":   "bold cyan",
    "parttime":   "bold yellow",
    "contract":   "bold orange3",
    "temporary":  "dim white",
    "internship": "bold purple",
    "volunteer":  "dim green",
}

PLATFORM_STYLE = {
    "LinkedIn": "bold cyan",
}


def _styled(text: str, style_map: dict, key: str) -> Text:
    t = Text(text)
    t.stylize(style_map.get(key, "white"))
    return t


def _ask_choice(prompt: str, options: dict[str, str], allow_empty: bool = True) -> str:
    """顯示選項並回傳使用者選擇的 key，留空回傳 ''"""
    console.print(prompt)
    for k, v in options.items():
        console.print(f"  [bold]{k}[/bold] = {v}")
    if allow_empty:
        console.print("  [dim]留空 = 不限[/dim]")
    raw = console.input("  請輸入選項: ").strip().lower()
    return raw if raw in options else ""


def display_jobs_table(jobs: list[dict]) -> None:
    table = Table(
        title="職缺搜尋結果",
        box=box.ROUNDED,
        show_lines=True,
        header_style="bold cyan",
    )
    table.add_column("#",        style="dim",        width=4,    justify="center")
    table.add_column("平台",     min_width=10,                   justify="center")
    table.add_column("職稱",     style="bold white",  min_width=20)
    table.add_column("公司",     style="yellow",      min_width=18)
    table.add_column("地點",     style="green",       min_width=15)
    table.add_column("工作型態",  min_width=10,                   justify="center")
    table.add_column("工作類型",  min_width=12,                   justify="center")
    table.add_column("發布日期",  style="dim",         width=12)

    for i, job in enumerate(jobs, 1):
        platform     = job.get("platform", "")
        plat_text    = Text(platform)
        plat_text.stylize(PLATFORM_STYLE.get(platform, "white"))

        wt_label     = job.get("work_type", "N/A")
        wt_text      = Text(wt_label)
        wt_text.stylize(WORK_TYPE_STYLE.get(wt_label, "white"))

        jt_key       = job.get("job_type_key", "")
        jt_label     = JOB_TYPE_LABEL.get(jt_key, job.get("job_type_display", "N/A"))
        jt_text      = Text(jt_label)
        jt_text.stylize(JOB_TYPE_STYLE.get(jt_key, "white"))

        table.add_row(
            str(i),
            plat_text,
            job["title"],
            job["company"],
            job["location"],
            wt_text,
            jt_text,
            job["posted_date"],
        )

    console.print(table)


def display_job_detail(job: dict, detail: dict) -> None:
    platform    = job.get("platform", "")
    wt          = job.get("work_type", "N/A")
    wt_style    = WORK_TYPE_STYLE.get(wt, "white")

    jt_key      = job.get("job_type_key", "")
    jt_label    = JOB_TYPE_LABEL.get(jt_key, job.get("job_type_display", ""))
    jt_style    = JOB_TYPE_STYLE.get(jt_key, "white")

    plat_style  = PLATFORM_STYLE.get(platform, "white")

    header = Text()
    header.append(f"[{platform}] ", style=plat_style)
    header.append(f"{job['title']}\n", style="bold white")
    header.append(f"{job['company']}  |  {job['location']}  |  ", style="yellow")
    header.append(wt, style=wt_style)
    if jt_label:
        header.append("  |  ", style="yellow")
        header.append(jt_label, style=jt_style)

    console.print(Panel(header, title="[bold cyan]職缺詳情[/bold cyan]", border_style="cyan"))
    console.print(f"[dim]連結:[/dim] {job['url']}\n")

    if detail.get("criteria"):
        crit_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        crit_table.add_column("項目", style="bold green")
        crit_table.add_column("內容", style="white")
        for k, v in detail["criteria"].items():
            crit_table.add_row(k, v)
        console.print(crit_table)

    if detail.get("error"):
        console.print(f"[red]取得詳情失敗: {detail['error']}[/red]")
    else:
        console.print(Panel(
            detail.get("description", ""),
            title="[bold]職缺描述[/bold]",
            border_style="dim",
            padding=(1, 2),
        ))


def main() -> None:
    console.print("[bold cyan]職缺搜尋爬蟲（LinkedIn）[/bold cyan]\n")

    # --- 關鍵字 ---
    keyword = console.input("\n[bold]請輸入搜尋關鍵字[/bold] (例如: Python, Data Engineer): ").strip()
    if not keyword:
        console.print("[red]關鍵字不可為空[/red]")
        sys.exit(1)

    # --- 地點 ---
    location = console.input("[bold]地點[/bold] (留空則不限, 例如: Taiwan, Germany): ").strip()

    # --- 工作型態 ---
    work_type = _ask_choice(
        "\n[bold]工作型態[/bold]",
        {"1": "[green]現場 (On-site)[/green]",
         "2": "[bold green]遠端 (Remote)[/bold green]",
         "3": "[magenta]混合 (Hybrid)[/magenta]"},
    )
    work_type_key = {"1": "onsite", "2": "remote", "3": "hybrid"}.get(work_type, "")

    # --- 工作類型 ---
    job_type = _ask_choice(
        "\n[bold]工作類型[/bold]",
        {
            "f": "[cyan]全職 (Full-time)[/cyan]",
            "p": "[yellow]兼職 (Part-time)[/yellow]",
            "c": "[orange3]合約/自由接案 (Contract)[/orange3]",
            "t": "臨時工 (Temporary)",
            "i": "[purple]實習 (Internship)[/purple]",
        },
    )
    job_type_key = {
        "f": "fulltime", "p": "parttime", "c": "contract",
        "t": "temporary", "i": "internship",
    }.get(job_type, "")

    # --- 英文 JD ---
    eng_input    = console.input("\n[bold]只顯示英文 JD？[/bold] (y/N): ").strip().lower()
    english_only = eng_input in ("y", "yes")

    # --- 筆數 ---
    max_str    = console.input("\n[bold]最多顯示幾筆結果[/bold] (預設 10): ").strip()
    max_results = int(max_str) if max_str.isdigit() else 10

    # --- 篩選摘要 ---
    filters = []
    if work_type_key:
        filters.append({"onsite": "現場", "remote": "遠端", "hybrid": "混合"}[work_type_key])
    if job_type_key:
        filters.append(JOB_TYPE_LABEL[job_type_key])
    if english_only:
        filters.append("英文 JD")
    filter_str = "、".join(filters) if filters else "不限"

    console.print(
        f"\n[dim]正在於 LinkedIn 搜尋「{keyword}」（篩選：{filter_str}）...[/dim]\n"
    )

    # --- 搜尋 ---
    search_kwargs = dict(
        keyword=keyword,
        location=location,
        max_results=max_results,
        work_type=work_type_key,
        job_type=job_type_key,
        english_only=english_only,
    )

    all_jobs = linkedin_scraper.search_jobs(**search_kwargs)
    for job in all_jobs:
        job.setdefault("platform", "LinkedIn")
    console.print(f"[dim]LinkedIn 找到 {len(all_jobs)} 筆[/dim]")

    # 補充 job_type 資訊（供顯示用）
    for job in all_jobs:
        job["job_type_key"]     = job_type_key
        job["job_type_display"] = JOB_TYPE_LABEL.get(job_type_key, "N/A")

    if not all_jobs:
        console.print("[red]找不到職缺，請嘗試其他關鍵字或調整篩選條件。[/red]")
        sys.exit(0)

    display_jobs_table(all_jobs)

    # --- 詳情互動 ---
    while True:
        choice = console.input(
            f"\n[bold]輸入編號查看詳情[/bold] (1~{len(all_jobs)}，或 q 離開): "
        ).strip()

        if choice.lower() in ("q", "quit", "exit", ""):
            console.print("[dim]已離開[/dim]")
            break

        if not choice.isdigit() or not (1 <= int(choice) <= len(all_jobs)):
            console.print(f"[red]請輸入 1 到 {len(all_jobs)} 之間的數字[/red]")
            continue

        job = all_jobs[int(choice) - 1]
        console.print("\n[dim]正在載入職缺詳情...[/dim]")

        detail = linkedin_scraper.get_job_detail(job["job_id"])

        display_job_detail(job, detail)


if __name__ == "__main__":
    main()