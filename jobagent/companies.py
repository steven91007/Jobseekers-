"""AI-company watchlist, pulled directly from each company's public job board.

Every entry was verified (first block 2026-09-24, second 2026-09-27) to
answer on its board with open roles in Germany, the Netherlands or Ireland.
Find new ones with `python -m jobagent companies detect <careers page URL>`. Re-check with
`python -m jobagent companies verify`; add new ones after the agent proposes
them (`python -m jobagent companies candidates`).

Tier decides the title filter:
  ai_native - AI is the product: AI/ML titles *and* software/backend titles kept.
  ai_heavy  - tech company with serious AI teams: only AI/ML titles kept.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    name: str
    ats: str      # greenhouse | ashby | lever | personio | recruitee | smartrecruiters | workday | teamtailor | jsonld
    slug: str     # board identifier; for workday / teamtailor / jsonld a short label
    tier: str     # ai_native | ai_heavy
    url: str = ""  # workday: public board URL; teamtailor: careers site; jsonld: careers page; personio: optional host


WATCHLIST: list[Company] = [
    # --- AI-native --------------------------------------------------------------
    Company("OpenAI", "ashby", "openai", "ai_native"),
    Company("Anthropic", "greenhouse", "anthropic", "ai_native"),
    Company("ElevenLabs", "ashby", "elevenlabs", "ai_native"),
    Company("Parloa", "ashby", "parloa", "ai_native"),
    Company("Helsing", "greenhouse", "helsing", "ai_native"),
    Company("Nebius", "greenhouse", "nebius", "ai_native"),
    Company("Cohere", "ashby", "cohere", "ai_native"),
    Company("Perplexity", "ashby", "perplexity", "ai_native"),
    Company("DeepL", "ashby", "deepl", "ai_native"),
    Company("Synthesia", "ashby", "synthesia", "ai_native"),
    Company("Harvey", "ashby", "harvey", "ai_native"),
    Company("Decagon", "ashby", "decagon", "ai_native"),
    Company("Sierra", "ashby", "sierra", "ai_native"),
    Company("Cresta", "greenhouse", "cresta", "ai_native"),
    Company("Together AI", "greenhouse", "togetherai", "ai_native"),
    Company("Wayve", "ashby", "wayve", "ai_native"),
    Company("n8n", "ashby", "n8n", "ai_native"),
    Company("Tacto", "ashby", "tacto", "ai_native"),
    Company("Aleph Alpha", "ashby", "alephalpha", "ai_native"),
    Company("Weaviate", "ashby", "weaviate", "ai_native"),
    Company("Rasa", "ashby", "rasa", "ai_native"),
    Company("Cursor", "ashby", "cursor", "ai_native"),
    Company("Intercom", "greenhouse", "intercom", "ai_native"),
    Company("Databricks", "greenhouse", "databricks", "ai_native"),
    Company("Celonis", "greenhouse", "celonis", "ai_native"),
    Company("Dataiku", "greenhouse", "dataiku", "ai_native"),
    # --- AI-heavy tech -------------------------------------------------------------
    Company("Stripe", "greenhouse", "stripe", "ai_heavy"),
    Company("Adyen", "greenhouse", "adyen", "ai_heavy"),
    Company("MongoDB", "greenhouse", "mongodb", "ai_heavy"),
    Company("HubSpot", "greenhouse", "hubspotjobs", "ai_heavy"),
    Company("N26", "greenhouse", "n26", "ai_heavy"),
    Company("Datadog", "greenhouse", "datadog", "ai_heavy"),
    Company("GetYourGuide", "greenhouse", "getyourguide", "ai_heavy"),
    Company("Snowflake", "ashby", "snowflake", "ai_heavy"),
    Company("Elastic", "greenhouse", "elastic", "ai_heavy"),
    Company("IMC Trading", "greenhouse", "imc", "ai_heavy"),
    Company("Grafana Labs", "greenhouse", "grafanalabs", "ai_heavy"),
    Company("Mollie", "ashby", "mollie", "ai_heavy"),
    Company("Flow Traders", "greenhouse", "flowtraders", "ai_heavy"),
    Company("GitLab", "greenhouse", "gitlab", "ai_heavy"),
    Company("Pigment", "lever", "pigment", "ai_heavy"),
    Company("Miro", "ashby", "miro", "ai_heavy"),
    Company("Trade Republic", "greenhouse", "traderepublicbank", "ai_heavy"),
    Company("Wayflyer", "ashby", "wayflyer", "ai_heavy"),
    Company("Tines", "greenhouse", "tines", "ai_heavy"),
    Company("Temporal", "ashby", "temporal", "ai_heavy"),
    Company("Workhuman", "ashby", "workhuman", "ai_heavy"),
    Company("Flipdish", "greenhouse", "flipdish", "ai_heavy"),
    Company("Contentful", "greenhouse", "contentful", "ai_heavy"),
    Company("Squarespace", "greenhouse", "squarespace", "ai_heavy"),
    Company("Enpal", "ashby", "enpal", "ai_heavy"),
    # --- added 2026-09-27 via `companies detect` ---------------------------------
    Company("Black Forest Labs", "ashby", "black-forest-labs", "ai_native"),
    Company("Catawiki", "greenhouse", "catawiki", "ai_heavy"),
    Company("Channable", "recruitee", "channable", "ai_native"),
    Company("Delivery Hero", "smartrecruiters", "deliveryhero", "ai_heavy"),
    Company("Evervault", "ashby", "evervault", "ai_heavy"),
    Company("HelloFresh", "greenhouse", "hellofresh", "ai_heavy"),
    Company("JetBrains", "greenhouse", "jetbrains", "ai_heavy"),
    Company("Juna.ai", "personio", "juna-ai", "ai_native", url="https://juna-ai.jobs.personio.de"),
    Company("Knowunity", "ashby", "knowunity", "ai_native"),
    Company("Langdock", "ashby", "langdock", "ai_native"),
    Company("Mastercard", "workday", "mastercard", "ai_heavy", url="https://mastercard.wd1.myworkdayjobs.com/CorporateCareers"),
    Company("Mendix", "lever", "mendix", "ai_heavy"),
    Company("Merantix", "personio", "merantix", "ai_native"),
    Company("Neura Robotics", "personio", "neura-robotics", "ai_native"),
    Company("Philips", "workday", "philips", "ai_heavy", url="https://philips.wd3.myworkdayjobs.com/jobs-and-careers"),
    Company("Prosus", "ashby", "prosus", "ai_heavy"),
    Company("Raisin", "greenhouse", "raisin", "ai_heavy"),
    Company("Scalable Capital", "smartrecruiters", "ScalableGmbH", "ai_heavy"),
    Company("SumUp", "greenhouse", "sumup", "ai_heavy"),
    Company("Toast", "greenhouse", "toast", "ai_heavy"),
    Company("Trivago", "greenhouse", "trivago", "ai_heavy"),
    Company("UbiOps", "personio", "ubiops", "ai_native"),
    Company("Workday", "workday", "workday", "ai_heavy", url="https://workday.wd5.myworkdayjobs.com/Workday"),
    Company("Zendesk", "workday", "zendesk", "ai_heavy", url="https://zendesk.wd1.myworkdayjobs.com/zendesk"),
    Company("deepset", "ashby", "deepsetai", "ai_native"),
]


def by_name(name: str) -> Company | None:
    lowered = name.strip().lower()
    return next((c for c in WATCHLIST if c.name.lower() == lowered or c.slug == lowered), None)


def tier_for_company_name(company: str) -> str:
    """Tier for a company seen on LinkedIn, matched against the watchlist by name."""
    from .normalize import company_key

    key = company_key(company)
    for c in WATCHLIST:
        if key and key == company_key(c.name):
            return c.tier
    return "other"
