import re
import time
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

WORK_TYPE_MAP = {
    "onsite": "1",
    "remote": "2",
    "hybrid": "3",
}

JOB_TYPE_MAP = {
    "fulltime": "F",
    "parttime": "P",
    "contract": "C",
    "temporary": "T",
    "internship": "I",
    "volunteer": "V",
}

JOB_TYPE_LABEL = {
    "fulltime": "Full-time",
    "parttime": "Part-time",
    "contract": "Contract",
    "temporary": "Temporary",
    "internship": "Internship",
    "volunteer": "Volunteer",
}

JOB_VIEW_RE = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:[/?#]|$)")


def _is_english(text: str) -> bool:
    if not text or text == "N/A":
        return True
    non_latin = sum(1 for c in text if ord(c) > 591)
    return (non_latin / len(text)) < 0.2


def _build_job_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def _extract_job_id_from_href(href: str) -> str:
    if not href:
        return ""

    parsed = urlparse(href)
    query_job_id = parse_qs(parsed.query).get("currentJobId", [""])[0]
    if query_job_id.isdigit():
        return query_job_id

    match = JOB_VIEW_RE.search(parsed.path)
    if match:
        return match.group(1)

    return ""


def _extract_job_id(card) -> str:
    job_id_tag = card.find("div", {"data-entity-urn": True})
    if job_id_tag:
        urn = job_id_tag.get("data-entity-urn", "")
        match = re.search(r":(\d+)$", urn)
        if match:
            return match.group(1)

    link_tag = card.find("a", class_="base-card__full-link")
    if link_tag:
        return _extract_job_id_from_href(link_tag.get("href", ""))

    return ""


def search_jobs(
    keyword: str,
    location: str = "",
    max_results: int = 10,
    work_type: str = "",
    job_type: str = "",
    english_only: bool = False,
) -> list[dict]:
    jobs = []
    start = 0
    fetch_limit = max_results * 3 if english_only else max_results

    while len(jobs) < max_results and start < fetch_limit:
        params: dict = {
            "keywords": keyword,
            "location": location,
            "start": start,
        }
        if work_type in WORK_TYPE_MAP:
            params["f_WT"] = WORK_TYPE_MAP[work_type]
        if job_type in JOB_TYPE_MAP:
            params["f_JT"] = JOB_TYPE_MAP[job_type]

        try:
            resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"LinkedIn search failed: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("li")

        if not cards:
            break

        for card in cards:
            if len(jobs) >= max_results:
                break

            job_id = _extract_job_id(card)
            if not job_id.isdigit():
                continue

            title_tag = card.find("h3", class_="base-search-card__title")
            company_tag = card.find("h4", class_="base-search-card__subtitle")
            location_tag = card.find("span", class_="job-search-card__location")
            date_tag = card.find("time")
            wtype_tag = card.find("span", class_="job-search-card__workplace-type")

            title = title_tag.get_text(strip=True) if title_tag else "N/A"
            company = company_tag.get_text(strip=True) if company_tag else "N/A"

            if english_only and not (_is_english(title) and _is_english(company)):
                continue

            jobs.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location_tag.get_text(strip=True) if location_tag else "N/A",
                "work_type": wtype_tag.get_text(strip=True) if wtype_tag else "N/A",
                "posted_date": date_tag.get("datetime", "N/A") if date_tag else "N/A",
                "url": _build_job_url(job_id),
            })

        start += len(cards)
        if start >= 1000:
            break
        time.sleep(1)

    return jobs


def get_job_detail(job_id: str) -> dict:
    if not job_id.isdigit():
        return {"error": f"Invalid LinkedIn job id: {job_id}"}

    url = JOB_DETAIL_URL.format(job_id=job_id)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"error": str(e)}

    soup = BeautifulSoup(resp.text, "html.parser")

    description_tag = soup.find("div", class_="show-more-less-html__markup")
    if not description_tag:
        description_tag = soup.find("div", class_="description__text")

    criteria: dict = {}
    for item in soup.find_all("li", class_="description__job-criteria-item"):
        label = item.find("h3")
        value = item.find("span")
        if label and value:
            criteria[label.get_text(strip=True)] = value.get_text(strip=True)

    description_text = ""
    if description_tag:
        for tag in description_tag.find_all(["br", "p", "li"]):
            tag.replace_with("\n" + tag.get_text() + "\n")
        description_text = description_tag.get_text(separator="", strip=False).strip()
        description_text = re.sub(r"\n{3,}", "\n\n", description_text)

    return {
        "description": description_text or "No description found.",
        "criteria": criteria,
    }
