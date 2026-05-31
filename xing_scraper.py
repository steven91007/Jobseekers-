import time
import re
import json
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SEARCH_URL    = "https://www.xing.com/jobs/search"
JOB_DETAIL_URL = "https://www.xing.com/jobs/show/{job_id}"

# XING employmentType 參數對應
JOB_TYPE_MAP = {
    "fulltime":   "FULL_TIME",
    "parttime":   "PART_TIME",
    "contract":   "FREELANCER",
    "internship": "INTERN",
    "temporary":  "MINI_JOB",
}

JOB_TYPE_LABEL = {
    "fulltime":   "全職",
    "parttime":   "兼職",
    "contract":   "合約/自由接案",
    "temporary":  "臨時工",
    "internship": "實習",
    "volunteer":  "志工",
}

# XING 工作型態參數
WORK_TYPE_MAP = {
    "remote": "REMOTE",
    "onsite": "OFFICE",
    "hybrid": "PARTIAL_REMOTE",
}

# XING 回傳值 → 統一顯示格式（與 LinkedIn 一致）
_WORK_TYPE_DISPLAY = {
    "REMOTE":         "Remote",
    "FULL_REMOTE":    "Remote",
    "HOME_OFFICE":    "Remote",
    "OFFICE":         "On-site",
    "PARTIAL_REMOTE": "Hybrid",
}


def _is_english(text: str) -> bool:
    if not text or text == "N/A":
        return True
    non_latin = sum(1 for c in text if ord(c) > 591)
    return (non_latin / len(text)) < 0.2


def _jobs_from_next_data(data: dict) -> list[dict]:
    """從 Next.js __NEXT_DATA__ JSON 中提取職缺列表"""
    jobs = []
    try:
        page_props = data.get("props", {}).get("pageProps", {})
        # 嘗試不同的結構路徑
        candidates = [
            page_props.get("searchResult", {}).get("items"),
            page_props.get("searchResult", {}).get("jobs"),
            page_props.get("jobs"),
            page_props.get("results"),
        ]
        items = next((c for c in candidates if c), [])

        for item in items:
            job_data = item.get("job") or item
            job_id = str(job_data.get("id", "")).strip()
            if not job_id:
                continue

            company = job_data.get("company") or {}
            if isinstance(company, dict):
                company_name = company.get("name", "N/A")
            else:
                company_name = str(company)

            raw_wt = job_data.get("remoteOption") or job_data.get("workModel", "")
            work_type_display = _WORK_TYPE_DISPLAY.get(raw_wt, raw_wt or "N/A")

            jobs.append({
                "job_id":      job_id,
                "title":       job_data.get("title", "N/A"),
                "company":     company_name,
                "location":    job_data.get("location", "N/A"),
                "work_type":   work_type_display,
                "posted_date": job_data.get("publishedAt", "N/A"),
                "url":         f"https://www.xing.com/jobs/show/{job_id}",
                "platform":    "XING",
            })
    except Exception:
        pass
    return jobs


def _jobs_from_html(soup: BeautifulSoup) -> list[dict]:
    """從 HTML 解析職缺列表（__NEXT_DATA__ 不可用時的備用方案）"""
    jobs = []

    cards = (
        soup.find_all("article", class_=re.compile(r"job", re.I)) or
        soup.find_all("div", attrs={"data-testid": re.compile(r"job", re.I)}) or
        soup.find_all("li",  class_=re.compile(r"job", re.I)) or
        soup.find_all("article")
    )

    for card in cards:
        # --- job_id ---
        job_id = ""
        link_tag = card.find("a", href=re.compile(r"/jobs/show/\d+"))
        if link_tag:
            m = re.search(r"/jobs/show/(\d+)", link_tag.get("href", ""))
            if m:
                job_id = m.group(1)
        if not job_id:
            for attr in ("data-job-id", "data-id"):
                val = card.get(attr, "")
                if val and re.match(r"^\d+$", val):
                    job_id = val
                    break
        if not job_id:
            continue

        # --- title ---
        title = "N/A"
        for sel in ({"name": "h2"}, {"name": "h3"},
                    {"name": "a", "href": re.compile(r"/jobs/show/")}):
            t = card.find(**sel)
            if t:
                title = t.get_text(strip=True)
                break

        # --- company ---
        company = "N/A"
        for cls in ("company-name", "company", "employer", "organization"):
            t = card.find(class_=re.compile(cls, re.I))
            if t:
                company = t.get_text(strip=True)
                break

        # --- location ---
        loc = "N/A"
        for cls in ("location", "city", "place", "address"):
            t = card.find(class_=re.compile(cls, re.I))
            if t:
                loc = t.get_text(strip=True)
                break

        # --- work_type ---
        wt = "N/A"
        for cls in ("remote", "work-model", "employment-type", "work-type"):
            t = card.find(class_=re.compile(cls, re.I))
            if t:
                raw = t.get_text(strip=True)
                wt = _WORK_TYPE_DISPLAY.get(raw, raw)
                break

        # --- date ---
        date_tag = card.find("time")
        posted = date_tag.get("datetime", "N/A") if date_tag else "N/A"

        jobs.append({
            "job_id":      job_id,
            "title":       title,
            "company":     company,
            "location":    loc,
            "work_type":   wt,
            "posted_date": posted,
            "url":         f"https://www.xing.com/jobs/show/{job_id}",
            "platform":    "XING",
        })

    return jobs


def search_jobs(
    keyword: str,
    location: str = "",
    max_results: int = 10,
    work_type: str = "",
    job_type: str = "",
    english_only: bool = False,
) -> list[dict]:
    """搜尋 XING 職缺，回傳職缺列表"""
    jobs: list[dict] = []
    page = 1
    fetch_limit = max_results * 3 if english_only else max_results

    while len(jobs) < fetch_limit:
        params: dict = {"keywords": keyword, "page": page}
        if location:
            params["location"] = location
        if job_type in JOB_TYPE_MAP:
            params["employmentType"] = JOB_TYPE_MAP[job_type]
        if work_type in WORK_TYPE_MAP:
            params["remoteOption"] = WORK_TYPE_MAP[work_type]

        try:
            resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"XING 搜尋請求失敗: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        page_jobs: list[dict] = []

        # 優先嘗試 __NEXT_DATA__
        next_data_tag = soup.find("script", id="__NEXT_DATA__")
        if next_data_tag:
            try:
                page_jobs = _jobs_from_next_data(json.loads(next_data_tag.string or "{}"))
            except (json.JSONDecodeError, Exception):
                pass

        # 備用：HTML 解析
        if not page_jobs:
            page_jobs = _jobs_from_html(soup)

        if not page_jobs:
            break

        for job in page_jobs:
            if len(jobs) >= fetch_limit:
                break
            if english_only and not (_is_english(job["title"]) and _is_english(job["company"])):
                continue
            jobs.append(job)

        if len(page_jobs) < 5:   # 已到最後一頁
            break

        page += 1
        if page > 50:
            break
        time.sleep(1)

    return jobs[:max_results]


def get_job_detail(job_id: str) -> dict:
    """取得 XING 單一職缺的詳細內容"""
    url = JOB_DETAIL_URL.format(job_id=job_id)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"error": str(e)}

    soup = BeautifulSoup(resp.text, "html.parser")

    # 優先從 __NEXT_DATA__ 解析
    next_data_tag = soup.find("script", id="__NEXT_DATA__")
    if next_data_tag:
        try:
            next_data = json.loads(next_data_tag.string or "{}")
            page_props = next_data.get("props", {}).get("pageProps", {})
            job_data = (
                page_props.get("job") or
                page_props.get("jobPosting") or
                page_props.get("jobDetails") or {}
            )
            if job_data:
                description = (
                    job_data.get("description") or
                    job_data.get("body") or
                    job_data.get("content") or ""
                )
                # 若 description 為 HTML，去除標籤
                if description and "<" in description:
                    desc_soup = BeautifulSoup(description, "html.parser")
                    for tag in desc_soup.find_all(["br", "p", "li"]):
                        tag.replace_with("\n" + tag.get_text() + "\n")
                    description = re.sub(r"\n{3,}", "\n\n",
                                         desc_soup.get_text(strip=False).strip())

                criteria: dict = {}
                if job_data.get("employmentType"):
                    criteria["工作類型"] = job_data["employmentType"]
                if job_data.get("location"):
                    criteria["地點"] = job_data["location"]
                if job_data.get("salary"):
                    criteria["薪資"] = str(job_data["salary"])
                if job_data.get("remoteOption"):
                    criteria["工作型態"] = _WORK_TYPE_DISPLAY.get(
                        job_data["remoteOption"], job_data["remoteOption"]
                    )

                return {
                    "description": description or "（無法取得職缺描述）",
                    "criteria":    criteria,
                }
        except (json.JSONDecodeError, Exception):
            pass

    # 備用：HTML 解析
    criteria: dict = {}
    description_tag = None
    for cls in ("job-description", "description__text", "job-body",
                "posting-description", "content"):
        description_tag = soup.find(class_=re.compile(cls, re.I))
        if description_tag:
            break
    if not description_tag:
        description_tag = soup.find("article")

    description_text = ""
    if description_tag:
        for tag in description_tag.find_all(["br", "p", "li"]):
            tag.replace_with("\n" + tag.get_text() + "\n")
        description_text = description_tag.get_text(separator="", strip=False).strip()
        description_text = re.sub(r"\n{3,}", "\n\n", description_text)

    return {
        "description": description_text or "（無法取得職缺描述）",
        "criteria":    criteria,
    }