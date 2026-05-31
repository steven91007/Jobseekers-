import time
import re
import json
from bs4 import BeautifulSoup

try:
    import cloudscraper
    _HAS_CLOUDSCRAPER = True
except ImportError:
    import requests
    _HAS_CLOUDSCRAPER = False

# cloudscraper 不需要手動設 User-Agent；requests 備用時才用
_FALLBACK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def _make_session():
    """建立 cloudscraper session（自動繞過 Cloudflare），若未安裝則退回 requests"""
    if _HAS_CLOUDSCRAPER:
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    s = __import__("requests").Session()
    s.headers.update(_FALLBACK_HEADERS)
    return s

BASE_URL      = "https://de.indeed.com"
SEARCH_URL    = "https://de.indeed.com/jobs"
JOB_DETAIL_URL = "https://de.indeed.com/viewjob?jk={job_id}"

# Indeed jt= 參數對應
JOB_TYPE_MAP = {
    "fulltime":   "fulltime",
    "parttime":   "parttime",
    "contract":   "contract",
    "internship": "internship",
    "temporary":  "temporary",
}

JOB_TYPE_LABEL = {
    "fulltime":   "全職",
    "parttime":   "兼職",
    "contract":   "合約/自由接案",
    "temporary":  "臨時工",
    "internship": "實習",
    "volunteer":  "志工",
}

# Indeed 遠端/工作型態關鍵詞（英文 + 德文）
_REMOTE_KEYWORDS  = {"remote", "homeoffice", "home office", "from home", "von zuhause",
                     "aus dem homeoffice", "remote work", "telework"}
_HYBRID_KEYWORDS  = {"hybrid", "teilweise remote", "partially remote", "hybridarbeit"}
_ONSITE_KEYWORDS  = {"vor ort", "on-site", "onsite", "in-office", "im büro"}

# JSON-LD employmentType → 統一 key
_EMPLOYMENT_TYPE_MAP = {
    "FULL_TIME":  "fulltime",
    "PART_TIME":  "parttime",
    "CONTRACTOR": "contract",
    "INTERN":     "internship",
    "TEMPORARY":  "temporary",
    "VOLUNTEER":  "volunteer",
}


def _is_english(text: str) -> bool:
    if not text or text == "N/A":
        return True
    non_latin = sum(1 for c in text if ord(c) > 591)
    return (non_latin / len(text)) < 0.2


def _normalize_work_type(raw: str) -> str:
    """將 Indeed 的工作型態文字正規化為 Remote / Hybrid / On-site / N/A"""
    lower = raw.lower().strip()
    if any(k in lower for k in _REMOTE_KEYWORDS):
        return "Remote"
    if any(k in lower for k in _HYBRID_KEYWORDS):
        return "Hybrid"
    if any(k in lower for k in _ONSITE_KEYWORDS):
        return "On-site"
    return raw or "N/A"


def _extract_job_id(tag) -> str:
    """從 tag 的各種屬性中取出 job_id"""
    for attr in ("data-jk", "data-job-key", "id"):
        val = tag.get(attr, "")
        if val and re.match(r"^[a-f0-9]{16}$", val):
            return val
    href = tag.get("href", "")
    m = re.search(r"jk=([a-f0-9]{16})", href)
    return m.group(1) if m else ""


def _parse_cards(soup: BeautifulSoup) -> list[dict]:
    """從搜尋結果頁 HTML 解析職缺卡片"""
    jobs: list[dict] = []

    # 每個職缺卡的容器
    cards = soup.find_all("div", class_=re.compile(r"job_seen_beacon|resultContent", re.I))
    if not cards:
        cards = soup.find_all("div", attrs={"data-jk": True})

    for card in cards:
        # --- job_id ---
        job_id = card.get("data-jk", "")
        if not job_id:
            a = card.find("a", attrs={"data-jk": True})
            if a:
                job_id = a.get("data-jk", "")
        if not job_id:
            a = card.find("a", class_=re.compile(r"JobTitle|jobTitle", re.I))
            if a:
                job_id = _extract_job_id(a)
        if not job_id:
            continue

        # --- title ---
        title = "N/A"
        h2 = card.find("h2", class_=re.compile(r"jobTitle", re.I))
        if h2:
            span = h2.find("span", attrs={"title": True})
            title = span.get("title") if span else h2.get_text(strip=True)
        if title == "N/A":
            a = card.find("a", class_=re.compile(r"JobTitle|jobTitle", re.I))
            if a:
                title = a.get_text(strip=True)

        # --- company ---
        company = "N/A"
        for sel in (
            {"name": "span", "class_": re.compile(r"companyName", re.I)},
            {"name": "div",  "class_": re.compile(r"company",     re.I)},
        ):
            t = card.find(**sel)
            if t:
                company = t.get_text(strip=True)
                break

        # --- location ---
        loc = "N/A"
        for sel in (
            {"name": "div",  "class_": re.compile(r"companyLocation", re.I)},
            {"name": "span", "class_": re.compile(r"location",        re.I)},
        ):
            t = card.find(**sel)
            if t:
                loc = t.get_text(strip=True)
                break

        # --- work_type (attribute_snippet: Remote / Hybrid / ...) ---
        work_type = "N/A"
        for snippet in card.find_all(class_=re.compile(r"attribute_snippet|metadata", re.I)):
            text = snippet.get_text(strip=True)
            normalized = _normalize_work_type(text)
            if normalized in ("Remote", "Hybrid", "On-site"):
                work_type = normalized
                break

        # --- posted_date ---
        posted = "N/A"
        date_tag = card.find(class_=re.compile(r"\bdate\b", re.I))
        if date_tag:
            posted = date_tag.get_text(strip=True)

        jobs.append({
            "job_id":      job_id,
            "title":       title,
            "company":     company,
            "location":    loc,
            "work_type":   work_type,
            "posted_date": posted,
            "url":         f"{BASE_URL}/viewjob?jk={job_id}",
            "platform":    "Indeed DE",
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
    """搜尋 de.indeed.com 職缺，回傳職缺列表"""
    jobs: list[dict] = []
    start = 0
    fetch_limit = max_results * 3 if english_only else max_results
    session = _make_session()

    # 先訪問首頁取得 Cookie / 通過 JS Challenge
    try:
        session.get(BASE_URL, timeout=15)
        time.sleep(1.5)
    except Exception:
        pass

    while len(jobs) < fetch_limit:
        params: dict = {
            "q":     keyword,
            "l":     location,
            "start": start,
            "limit": 15,
        }
        if job_type in JOB_TYPE_MAP:
            params["jt"] = JOB_TYPE_MAP[job_type]
        if work_type == "remote":
            params["remotejob"] = "1"

        try:
            resp = session.get(SEARCH_URL, params=params, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"Indeed 搜尋請求失敗: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # 檢查是否被 CAPTCHA 擋住
        if "captcha" in resp.url.lower() or soup.find(id="captcha-box"):
            print("Indeed 偵測到爬蟲，請稍後再試或改用其他平台。")
            break

        page_jobs = _parse_cards(soup)
        if not page_jobs:
            break

        for job in page_jobs:
            if len(jobs) >= fetch_limit:
                break
            if english_only and not (_is_english(job["title"]) and _is_english(job["company"])):
                continue
            # hybrid 篩選（Indeed 無直接參數，後端過濾）
            if work_type == "hybrid" and job.get("work_type") != "Hybrid":
                continue
            if work_type == "onsite" and job.get("work_type") not in ("On-site", "N/A"):
                continue
            jobs.append(job)

        # 取得下一頁連結
        next_link = soup.find("a", attrs={"aria-label": re.compile(r"next|weiter", re.I)})
        if not next_link:
            break

        start += 15
        if start >= 300:
            break
        time.sleep(1.5)

    return jobs[:max_results]


def get_job_detail(job_id: str) -> dict:
    """取得 Indeed 單一職缺詳情"""
    url = JOB_DETAIL_URL.format(job_id=job_id)
    session = _make_session()
    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        return {"error": str(e)}

    soup = BeautifulSoup(resp.text, "html.parser")

    criteria: dict = {}

    # --- 優先解析 JSON-LD（結構化資料）---
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            # 可能是 list 或 dict
            if isinstance(data, list):
                data = next((d for d in data if d.get("@type") == "JobPosting"), {})
            if data.get("@type") == "JobPosting":
                description_html = data.get("description", "")
                if description_html:
                    desc_soup = BeautifulSoup(description_html, "html.parser")
                    for tag in desc_soup.find_all(["br", "p", "li"]):
                        tag.replace_with("\n" + tag.get_text() + "\n")
                    description = re.sub(
                        r"\n{3,}", "\n\n",
                        desc_soup.get_text(strip=False).strip()
                    )
                else:
                    description = ""

                # 薪資
                salary = data.get("baseSalary") or data.get("estimatedSalary")
                if salary and isinstance(salary, dict):
                    val = salary.get("value", {})
                    if isinstance(val, dict):
                        mn = val.get("minValue", "")
                        mx = val.get("maxValue", "")
                        unit = val.get("unitText", "")
                        currency = salary.get("currency", "")
                        salary_str = f"{mn}–{mx} {currency}/{unit}".strip("–/ ")
                        if salary_str:
                            criteria["薪資"] = salary_str

                # 工作類型
                emp_type = data.get("employmentType", "")
                if emp_type:
                    # 可能是 list
                    if isinstance(emp_type, list):
                        emp_type = ", ".join(emp_type)
                    key = _EMPLOYMENT_TYPE_MAP.get(emp_type, "")
                    criteria["工作類型"] = JOB_TYPE_LABEL.get(key, emp_type)

                # 工作地點
                job_loc = data.get("jobLocation") or data.get("applicantLocationRequirements")
                if job_loc:
                    if isinstance(job_loc, list) and job_loc:
                        job_loc = job_loc[0]
                    if isinstance(job_loc, dict):
                        addr = job_loc.get("address") or {}
                        if isinstance(addr, dict):
                            loc_parts = filter(None, [
                                addr.get("streetAddress"),
                                addr.get("addressLocality"),
                                addr.get("addressCountry"),
                            ])
                            loc_str = ", ".join(loc_parts)
                            if loc_str:
                                criteria["地點"] = loc_str

                # 遠端
                remote = data.get("jobLocationType", "")
                if remote == "TELECOMMUTE":
                    criteria["工作型態"] = "Remote"

                return {
                    "description": description or "（無法取得職缺描述）",
                    "criteria":    criteria,
                }
        except (json.JSONDecodeError, Exception):
            continue

    # --- 備用：HTML 解析 ---
    description_tag = None
    for cls in (
        "jobsearch-jobDescriptionText",
        "jobDescription",
        "job-description",
        "description",
    ):
        description_tag = soup.find(id=cls) or soup.find(class_=re.compile(cls, re.I))
        if description_tag:
            break

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
