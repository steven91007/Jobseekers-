"""Offline end-to-end exercise of the push cycle against fake Discord objects."""
import asyncio, os, pathlib, tempfile, sys
os.environ["DISCORD_TOKEN"] = "fake"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import discord
from linkedin_scraper import Outcome, ScrapeResult
from bot import config, db, pusher as pusher_mod
from bot.pusher import Pusher

cfg = config.load(env_file="nonexistent")
FAIL = []          # queue of exceptions for channel.send
SENT = []


class FakeChannel:
    def __init__(self, cid): self.id = cid
    async def send(self, *a, **k):
        if FAIL:
            raise FAIL.pop(0)
        SENT.append(k.get("embeds") or [k.get("embed")])


class FakeUser:
    async def send(self, *a, **k): SENT.append(["DM"])


class FakeClient:
    def is_ready(self): return True
    def get_channel(self, cid): return FakeChannel(cid)
    async def fetch_channel(self, cid): return FakeChannel(cid)
    def get_user(self, uid): return FakeUser()
    async def change_presence(self, **k): pass


def jobs(n, offset=0):
    return [{"job_id": str(i + offset), "title": f"Job {i}", "company": "ACME",
             "location": "Taipei", "work_type": "Remote", "posted_date": "2026-08-22",
             "url": f"https://www.linkedin.com/jobs/view/{i+offset}/"} for i in range(n)]


def stub(result):
    async def _s(cfg_, sub): return result
    pusher_mod.scrape_subscription = _s


def fresh():
    SENT.clear(); FAIL.clear()
    conn = db.connect(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    sid = db.add_subscription(conn, guild_id=1, channel_id=2, creator_id=3,
        keyword="Python", location="Taiwan", work_type="", job_type="",
        english_only=False, max_results=15)
    return conn, sid, Pusher(FakeClient(), conn, cfg)


def embeds_sent(): return sum(len(b) for b in SENT)


async def main():
    ok = True
    def check(label, cond, extra=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label} {extra}")

    # --- cold start: baseline only, zero job embeds ---
    print("\n[1] cold start seeding")
    conn, sid, p = fresh()
    stub(ScrapeResult(jobs=jobs(20), outcome=Outcome.OK, raw_card_count=20, parsed_count=20))
    r = await p.run_one(db.get_subscription(conn, sid))
    check("posts exactly one baseline message", len(SENT) == 1, f"(got {len(SENT)})")
    check("posts zero job embeds", embeds_sent() == 1, f"(got {embeds_sent()})")
    check("marks all 20 as seen", len(db.filter_new_job_ids(conn, sid, [str(i) for i in range(20)])) == 0)
    check("seeded flag set", db.get_subscription(conn, sid).seeded)
    check("posted_count is 0", r.posted_count == 0)

    # --- second run: nothing new, nothing posted ---
    print("\n[2] second run with no new jobs")
    SENT.clear()
    await p.run_one(db.get_subscription(conn, sid))
    check("posts nothing at all", len(SENT) == 0, f"(got {len(SENT)})")

    # --- only genuinely new jobs get posted ---
    print("\n[3] three new jobs appear")
    SENT.clear()
    conn.execute("DELETE FROM seen_jobs WHERE job_id IN ('0','1','2')"); conn.commit()
    await p.run_one(db.get_subscription(conn, sid))
    check("header + jobs posted", embeds_sent() == 4, f"(header+3, got {embeds_sent()})")
    check("re-marked as seen", len(db.filter_new_job_ids(conn, sid, ["0","1","2"])) == 0)

    # --- overflow above the per-push cap is still marked seen ---
    print("\n[4] 25 new jobs vs cap of", cfg.max_new_per_push)
    conn, sid, p = fresh()
    stub(ScrapeResult(jobs=jobs(1), outcome=Outcome.OK, raw_card_count=1, parsed_count=1))
    await p.run_one(db.get_subscription(conn, sid))          # seed
    SENT.clear()
    stub(ScrapeResult(jobs=jobs(25), outcome=Outcome.OK, raw_card_count=25, parsed_count=25))
    r = await p.run_one(db.get_subscription(conn, sid))
    check(f"posts only the cap", r.posted_count == cfg.max_new_per_push, f"(got {r.posted_count})")
    left = db.filter_new_job_ids(conn, sid, [str(i) for i in range(25)])
    check("overflow marked seen (won't re-queue)", len(left) == 0, f"(unseen: {len(left)})")

    # --- send failure must NOT mark jobs seen ---
    print("\n[5] transient send failure")
    conn, sid, p = fresh()
    stub(ScrapeResult(jobs=jobs(1), outcome=Outcome.OK, raw_card_count=1, parsed_count=1))
    await p.run_one(db.get_subscription(conn, sid))
    conn.execute("DELETE FROM seen_jobs"); conn.commit()
    SENT.clear()
    FAIL.append(discord.HTTPException(type("R", (), {"status": 500, "reason": "x"})(), "boom"))
    r = await p.run_one(db.get_subscription(conn, sid))
    check("nothing posted", r.posted_count == 0)
    check("job NOT marked seen (recoverable)", db.filter_new_job_ids(conn, sid, ["0"]) == {"0"})

    # --- hard failure surfaces instead of looking like 'no new jobs' ---
    print("\n[6] BLOCKED is surfaced, not silent")
    conn, sid, p = fresh()
    stub(ScrapeResult(outcome=Outcome.BLOCKED, detail="HTTP 999"))
    SENT.clear()
    r = await p.run_one(db.get_subscription(conn, sid))
    check("alert posted to channel + DM", len(SENT) >= 1, f"(got {len(SENT)})")
    check("outcome recorded as BLOCKED", db.get_subscription(conn, sid).last_outcome == "BLOCKED")
    check("alert_state = broken", db.get_subscription(conn, sid).alert_state == "broken")
    SENT.clear()
    await p.run_one(db.get_subscription(conn, sid))
    check("second failure does NOT re-alert", len(SENT) == 0, f"(got {len(SENT)})")

    # --- recovery via the seeding path (subscription broke on its FIRST run) ---
    print("\n[7] recovery from a failed first run")
    SENT.clear()
    stub(ScrapeResult(jobs=jobs(4), outcome=Outcome.OK, raw_card_count=4, parsed_count=4))
    await p.run_one(db.get_subscription(conn, sid))
    s7 = db.get_subscription(conn, sid)
    check("seeded on the recovery run", s7.seeded)
    check("alert_state back to healthy", s7.alert_state == "healthy", f"(is {s7.alert_state})")
    check("baseline + recovery notice posted", len(SENT) == 2, f"(got {len(SENT)})")

    # --- recovery via the normal path (already-seeded subscription) ---
    print("\n[7b] recovery on an already-seeded subscription")
    conn, sid, p = fresh()
    stub(ScrapeResult(jobs=jobs(2), outcome=Outcome.OK, raw_card_count=2, parsed_count=2))
    await p.run_one(db.get_subscription(conn, sid))              # seed
    stub(ScrapeResult(outcome=Outcome.BLOCKED, detail="HTTP 999"))
    await p.run_one(db.get_subscription(conn, sid))              # break
    check("flagged broken", db.get_subscription(conn, sid).alert_state == "broken")
    SENT.clear()
    stub(ScrapeResult(jobs=jobs(2), outcome=Outcome.OK, raw_card_count=2, parsed_count=2))
    await p.run_one(db.get_subscription(conn, sid))              # recover
    check("recovery notice posted", len(SENT) == 1, f"(got {len(SENT)})")
    check("alert_state back to healthy",
          db.get_subscription(conn, sid).alert_state == "healthy")

    # --- circuit breaker ---
    print("\n[8] circuit breaker after consecutive blocks")
    conn = db.connect(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    for i in range(6):
        db.add_subscription(conn, guild_id=1, channel_id=2+i, creator_id=3, keyword=f"K{i}",
            location="", work_type="", job_type="", english_only=False, max_results=15)
    p = Pusher(FakeClient(), conn, cfg)
    object.__setattr__(cfg, "gap_min", 0.0); object.__setattr__(cfg, "gap_max", 0.0)
    stub(ScrapeResult(outcome=Outcome.BLOCKED, detail="HTTP 999"))
    SENT.clear()
    rep = await p.run_all()
    check("cycle aborted", rep.aborted)
    check("stopped after 2 attempts", sum(1 for x in rep.results if not x.skipped) == 2,
          f"(attempted {sum(1 for x in rep.results if not x.skipped)}/6)")
    check("rest marked skipped", sum(1 for x in rep.results if x.skipped) == 4)

    # --- ticker exactly-once ---
    print("\n[9] ticker exactly-once per day")
    conn, sid, p = fresh()
    stub(ScrapeResult(jobs=jobs(3), outcome=Outcome.OK, raw_card_count=3, parsed_count=3))
    object.__setattr__(cfg, "push_time", cfg.push_time.replace(hour=0, minute=0))
    SENT.clear()
    await p.tick()
    first = len(SENT)
    await p.tick(); await p.tick()
    check("first tick runs", first > 0)
    check("later ticks are no-ops", len(SENT) == first, f"(sent grew to {len(SENT)})")

    # --- tick before push time does nothing ---
    print("\n[10] tick before push time")
    conn, sid, p = fresh()
    object.__setattr__(cfg, "push_time", cfg.push_time.replace(hour=23, minute=59))
    SENT.clear()
    await p.tick()
    check("no run before push time", len(SENT) == 0)

    # --- visa check runs only for subscriptions that opted in ---
    print("\n[11] visa check on push")
    conn, sid, p = fresh()
    sid_v = db.add_subscription(conn, guild_id=1, channel_id=9, creator_id=3,
        keyword="Rust", location="北歐", work_type="", job_type="",
        english_only=False, max_results=15, visa_check=True)
    calls = []
    async def fake_check_visa(cfg_, jobs_, max_checks=None):
        calls.append(len(jobs_))
        for j in jobs_:
            j["visa_status"] = "supported"; j["visa_evidence"] = "We offer visa sponsorship."; j["visa_source"] = "rules"
        return len(jobs_)
    pusher_mod.check_visa = fake_check_visa
    stub(ScrapeResult(jobs=jobs(2), outcome=Outcome.OK, raw_card_count=2, parsed_count=2))
    await p.run_one(db.get_subscription(conn, sid_v))              # seed
    await p.run_one(db.get_subscription(conn, sid))                # seed plain sub
    conn.execute("DELETE FROM seen_jobs"); conn.commit()
    SENT.clear(); calls.clear()
    await p.run_one(db.get_subscription(conn, sid))
    check("plain subscription never checks visas", calls == [], f"(calls={calls})")
    SENT.clear()
    await p.run_one(db.get_subscription(conn, sid_v))
    check("visa subscription checks the new jobs once", calls == [2], f"(calls={calls})")
    job_embeds = [e for batch in SENT for e in batch if e.title and e.title.startswith("Job ")]
    check("embeds carry the visa line", job_embeds and all("Visa: sponsorship mentioned" in e.description for e in job_embeds))
    check("describe() mentions the check", "visa check" in db.get_subscription(conn, sid_v).describe())

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
