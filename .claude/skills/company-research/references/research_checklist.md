# Company research checklist

Work top to bottom; stop a line when it is covered by a company-owned source. Record each page as a source the moment
you read it: `S#`, URL, title, publisher, published (from the page metadata), accessed (today), kind.

| # | Question | Where to look | Search pattern |
|---|---|---|---|
| 1 | What does the JD's team build, which teams does it work with? | the posting (`jd.md` → S1), sibling postings at the same site (SmartRecruiters / Greenhouse / Personio board, Built In / devjobs mirrors) | `<Company> <city> job software engineer machine learning` |
| 2 | What does the company / division do, size, HQ? | about page, investor "About <Company>" boilerplate in the latest results release | `<Company> about`, `<Company> quarterly report <year>` |
| 3 | How is the division doing (revenue, orders, cost programmes)? | last two results releases (company IR, Inderes / Cision / Business Wire mirrors) | `<Company> 1Q<yy> results <division>` |
| 4 | What launched or changed in the last 12 months? | company media center, press releases, trade press | `<Company> <product> news <year>`, `<Company> acquisition stake <year>` |
| 5 | Tech stack? | JD, sibling postings, engineering blog, GitHub org (`https://api.github.com/orgs/<org>/repos?sort=pushed`), conference talks | `<Company> engineering blog`, `github <company>` |
| 6 | The JD city's office? | company news on the site (openings, expansions, headcount), local press | `<Company> <city> office expansion`, German: `<Company> <Stadt> Standort Arbeitsplätze` |
| 7 | Hiring process and reported interview questions? | posting text (stages, start date, "message to manager"), careers page "how we hire", Glassdoor interviews / kununu / Indeed (kind `review-site`), Blind / Reddit / LeetCode discuss (kind `forum`), never as confirmed | `<Company> interview questions <role>`, `<Company> <role> interview glassdoor`, `site:reddit.com <Company> interview`, `<Company> Vorstellungsgespräch Fragen` (kununu), `<Company> interview process software engineer` |

Interview reports (row 7): record each reported question as `interview_reports` R# with stage, role, date and `seen`
(`snippet` when only a search snippet shows it, e.g. the Glassdoor login wall; the question is a practice signal, so a
snippet is acceptable here, unlike a figure). Paraphrase, never copy a review; drop names and handles. Prefer the
same role family and the last 36 months; another company's reports are never used. Nothing found → a risk of kind
`unknown` naming the sites searched. Rounds / take-home / duration go to `interview_process`.

Fetching: `web-fetch` agents in parallel (one per 3–4 URLs, ask for facts + URL + publication date). If an agent
returns no report, fetch with curl into a new scratchpad directory and strip HTML with a script kept elsewhere
(`python3 -I strip.py page.html page.txt`); read the text, take dates from `datePublished` / `article:published_time`.
Pages that answer 403/404 are not sources; use another outlet carrying the same press release.

Hook check before writing: (1) Is the company fact something the hiring manager owns or cares about? (2) Which JD
concept does it touch (`jd_concepts` in the skeleton)? (3) Which bullet of a picked card already proves it? If (3) has
no answer, the item is a question to ask or a `gap_note`, not a hook.
