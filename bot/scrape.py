"""The one place in the bot allowed to touch linkedin_scraper.

`search_jobs_strict` is synchronous and blocks for seconds (requests +
time.sleep between pages). Awaiting it on the event loop would stall the
gateway heartbeat and get the bot disconnected, so it runs in a worker thread.

Note the two nested budgets. `asyncio.wait_for` cannot cancel a thread — it
only stops *waiting* for it, while the thread keeps hammering LinkedIn. So the
real limit is `cfg.scrape_deadline`, enforced inside the sync function; the
outer `cfg.scrape_timeout` is a strictly looser backstop that should never fire.
"""

import asyncio
import logging

import linkedin_scraper
from linkedin_scraper import Outcome, ScrapeResult, ScraperError

from .config import Config
from .db import Subscription

log = logging.getLogger(__name__)


async def scrape(
    cfg: Config,
    *,
    keyword: str,
    location: str = "",
    work_type: str = "",
    job_type: str = "",
    english_only: bool = False,
    max_results: int = 15,
) -> ScrapeResult:
    """Run a search off-loop. Never raises — failures come back as an Outcome."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                linkedin_scraper.search_jobs_strict,
                keyword=keyword,
                location=location,
                max_results=max_results,
                work_type=work_type,
                job_type=job_type,
                english_only=english_only,
                timeout=cfg.scrape_deadline,
            ),
            timeout=cfg.scrape_timeout,
        )
    except ScraperError as e:
        log.warning("scrape failed (%s): %s", e.outcome.value, e)
        return ScrapeResult(outcome=e.outcome, detail=str(e))
    except asyncio.TimeoutError:
        # The in-thread deadline should always fire first; reaching here means
        # the thread is wedged somewhere that does not check it.
        log.error("scrape exceeded the outer timeout — in-thread deadline did not fire")
        return ScrapeResult(
            outcome=Outcome.TRANSPORT_ERROR,
            detail="outer timeout exceeded",
        )


async def scrape_subscription(cfg: Config, sub: Subscription) -> ScrapeResult:
    return await scrape(
        cfg,
        keyword=sub.keyword,
        location=sub.location,
        work_type=sub.work_type,
        job_type=sub.job_type,
        english_only=sub.english_only,
        max_results=sub.max_results,
    )
