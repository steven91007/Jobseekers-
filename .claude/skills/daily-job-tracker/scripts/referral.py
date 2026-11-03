"""Referral candidates (public web search only) + the candidate's referral message template.

No LinkedIn login, no automation on the candidate's account, no fetching of linkedin.com: the headless search run
gets WebSearch only, and every candidate must be one of the returned search-result URLs (checked here).

    python3 referral.py prompt "<Company>" "<Job title>" [City]     # the search prompt (for inspection)
    python3 referral.py messages people.json "<Company>" "<Job title>" <cv link>
"""
import json
import re
import sys

SENDER, SENDER_TITLE, SENDER_COMPANY = "Amo Hsu", "Senior AI Engineer", "Delta Electronics"
NOTE_LIMIT = 280
GROUP_ORDER = {"team": 0, "manager": 1, "taiwanese": 2}
GROUP_LABEL = {"team": "同團隊工程師", "manager": "招募/工程主管", "taiwanese": "台灣同事"}

# The candidate's template, wording kept verbatim; only the <…> blanks are filled.
TEMPLATE = """Hi {name},
My name is Amo Hsu and I am a Senior AI Engineer at Delta Electronics.

The reason why I'm writing to you is I saw you are working at {company} as a {their_title}. I'm very interested in joining {company} myself as well. So I was wondering if you would be so kind to refer my candidacy.

If you don't feel comfortable doing that, it's also totally understandable. If you want to know more about me, here is my CV.
{cv_link}

I'm also happy to schedule a chat with you so you can get to know me more. As well as learning how {job_title} work at {company}.

Thank you for your time. I hope to hear back from you soon.

Best regards,
Amo Hsu"""

PROFILE = re.compile(r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/?#\s]+)/?", re.I)


def first_name(name):
    return (name or "").split()[0] if name else "there"


def full_message(person, company, job_title, cv_link):
    return TEMPLATE.format(name=first_name(person["name"]), company=company, their_title=person.get("title") or "colleague",
                           job_title=job_title, cv_link=cv_link or "(CV link)")


def connect_note(person, company, job_title, limit=NOTE_LIMIT):
    """Connection-request note: ask to connect + the role + referral, ≤ 280 characters."""
    name = first_name(person["name"])
    def build(title):
        return (f"Hi {name}, I'm {SENDER}, {SENDER_TITLE} at {SENDER_COMPANY}. I'm applying for the {title} role at "
                f"{company} and would be grateful if you could consider referring me. Would you like to connect?")
    note = build(job_title)
    if len(note) > limit:  # shorten the job title, never drop the role or the ask
        room = len(job_title) - (len(note) - limit) - 1
        note = build(job_title[:max(room, 10)].rstrip() + "…")
    if len(note) > limit:
        note = (f"Hi {name}, I'm {SENDER} ({SENDER_TITLE}, {SENDER_COMPANY}), applying to {company}. "
                f"Could you consider referring me? Would you like to connect?")[:limit]
    return note


def norm_url(u):
    m = PROFILE.match(str(u or "").strip())
    return f"https://www.linkedin.com/in/{m.group(1).lower()}" if m else None


def parse_candidates(obj, limit=4):
    """Validate the search run's JSON: keep only people whose profile URL is one of the returned search results;
    rank same-team engineers → hiring/engineering manager → Taiwanese employees; at most `limit`."""
    results = {norm_url(r.get("url")) for r in (obj.get("results") or [])} - {None}
    out, seen = [], set()
    for c in obj.get("candidates") or []:
        u = norm_url(c.get("url"))
        name = str(c.get("name") or "").strip()
        if not u or u not in results or u in seen or not name or len(name) > 80:
            continue
        group = c.get("group") if c.get("group") in GROUP_ORDER else "team"
        seen.add(u)
        out.append({"name": name, "title": str(c.get("title") or "").strip(), "url": u, "group": group,
                    "why": str(c.get("why") or GROUP_LABEL[group]).strip()})
    out.sort(key=lambda c: GROUP_ORDER[c["group"]])  # stable: keeps the run's order inside a group
    return out[:limit]


def with_messages(people, company, job_title, cv_link):
    return [{**p, "full": full_message(p, company, job_title, cv_link), "note": connect_note(p, company, job_title)} for p in people]


def notion_markdown(people):
    if not people:
        return "## 內推訊息\n\n公開搜尋沒有找到合適的內推候選人。"
    parts = ["## 內推訊息"]
    for i, p in enumerate(people, 1):
        parts += [f"### {i}. {p['name']} — {p['title']}{'(推薦)' if i == 1 else ''}", f"{p['url']} · {p['why']}",
                  "**完整訊息(連線後 / InMail)**", "```\n" + p["full"] + "\n```",
                  f"**邀請附言({len(p['note'])} 字元)**", "```\n" + p["note"] + "\n```"]
    return "\n\n".join(parts)


def search_prompt(company, job_title, city=""):
    return f"""Find 2–4 people at {company} who could refer a candidate for the role "{job_title}"{f' ({city})' if city else ''}.
Use ONLY the WebSearch tool (public search results). Do not open, fetch or scrape linkedin.com pages, do not log in
anywhere, and do not use any other tool. Run queries such as:
- site:linkedin.com/in "{company}" (AI engineer OR machine learning engineer OR engineering manager)
- site:linkedin.com/in "{company}" "{job_title}"
- site:linkedin.com/in "{company}" Taiwan
Rank: engineers in the same team/area as the role ("team") → the role's hiring or engineering manager ("manager") →
Taiwanese employees ("taiwanese"). Only people whose result shows they currently work at {company}. Never invent a
person: every candidate's url must be exactly one of the search-result URLs you list in "results". If nothing fits,
return an empty "candidates" list.
End with ONE fenced ```json block:
{{"results": [{{"url": "", "title": "<result title>", "snippet": "", "query": ""}}],
 "candidates": [{{"name": "", "title": "<their job title>", "url": "<one of results[].url>", "group": "team|manager|taiwanese",
 "why": "<短理由,繁體中文>"}}]}}"""


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["prompt"] and len(a) >= 3:
        print(search_prompt(a[1], a[2], a[3] if len(a) > 3 else ""))
    elif a[:1] == ["messages"] and len(a) == 5:
        people = json.load(open(a[1], encoding="utf-8"))
        print(json.dumps(with_messages(people, a[2], a[3], a[4]), ensure_ascii=False, indent=2))
    else:
        sys.exit(__doc__)
