"""Offline checks for region presets and visa-sponsorship detection."""
import pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import linkedin_scraper as ls
import visa
from visa import VisaStatus, VisaVerdict

ok = True


def check(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {extra}")


print("\n[1] region presets")
check("北歐 expands to the five Nordic countries",
      ls._parse_locations("北歐") == ["Denmark", "Sweden", "Norway", "Finland", "Iceland"])
check("nordics is case-insensitive", ls._parse_locations("NORDICS") == ls._parse_locations("nordics"))
check("mixed list keeps plain cities and dedupes",
      ls._parse_locations("Berlin, Nordics, sweden, Berlin") == ["Berlin", "Denmark", "Sweden", "Norway", "Finland", "Iceland"])
check("dach/benelux/baltics exist", all(k in ls.REGION_PRESETS for k in ("dach", "benelux", "baltics")))
check("empty stays a single wildcard", ls._parse_locations("") == [""])
check("unknown term passes through", ls._parse_locations("Taipei") == ["Taipei"])

print("\n[2] rules: positive")
for text in [
    "We offer visa sponsorship for this position.",
    "Relocation package and help with the work permit are provided.",
    "International candidates are welcome. EU Blue Card supported.",
    "Wir unterstützen dich beim Visum und übernehmen die Umzugskosten.",
    "Vi hjälper dig med arbetstillstånd och flytt till Göteborg.",
    "Vi hjelper deg med arbeidstillatelse.",
    "Autamme työluvan kanssa.",
]:
    v = visa.classify_visa(text)
    check(text[:50], v.status is VisaStatus.SUPPORTED and v.evidence, f"(got {v.status.value})")

print("\n[3] rules: negative wins over positive")
for text in [
    "Unfortunately we cannot sponsor a visa for this role.",
    "No visa sponsorship. Must have the right to work in Denmark.",
    "Applicants must already hold a valid work permit for Norway.",
    "This role does not offer relocation support.",
    "EU citizens only.",
    "Eine gültige Arbeitserlaubnis ist erforderlich.",
    "We sponsor visas for many roles, but we are unable to sponsor for this one.",
]:
    v = visa.classify_visa(text)
    check(text[:50], v.status is VisaStatus.NOT_SUPPORTED, f"(got {v.status.value})")

print("\n[4] rules: unknown")
for text in ["We build payment systems in Rust. Free lunch.", "", "   "]:
    v = visa.classify_visa(text)
    check(repr(text[:30]), v.status is VisaStatus.UNKNOWN and v.evidence == "", f"(got {v.status.value})")

print("\n[5] evidence is the containing sentence")
v = visa.classify_visa("Great team. We offer visa sponsorship to strong candidates. Apply now.")
check("evidence isolates the sentence", v.evidence == "We offer visa sponsorship to strong candidates.", repr(v.evidence))

print("\n[6] check_visa_support orchestration")
DETAILS = {
    "1": {"description": "We offer visa sponsorship.", "criteria": {}},
    "2": {"description": "Just a normal job ad.", "criteria": {}},
    "3": {"error": "HTTP 999"},
    "4": {"description": "Another normal ad.", "criteria": {}},
}
fetched = []


def fake_fetch(job_id):
    fetched.append(job_id)
    return DETAILS[job_id]


llm_calls = []


def fake_llm(text):
    llm_calls.append(text)
    return VisaVerdict(VisaStatus.SUPPORTED, "LLM says relocation help", "llm", 0.7)


jobs = [{"job_id": i, "title": f"J{i}"} for i in ("1", "2", "3", "4")]
n = visa.check_visa_support(jobs, llm=fake_llm, fetch_detail=fake_fetch, pause=0)
check("all four fetched", n == 4 and fetched == ["1", "2", "3", "4"], f"(n={n}, fetched={fetched})")
check("rules decide job 1 without LLM", jobs[0]["visa_status"] == "supported" and jobs[0]["visa_source"] == "rules")
check("LLM only called for unknowns", len(llm_calls) == 2, f"(calls={len(llm_calls)})")
check("LLM verdict recorded", jobs[1]["visa_status"] == "supported" and jobs[1]["visa_source"] == "llm")
check("fetch error stays unchecked", jobs[2]["visa_status"] == "unchecked")
check("status_of round-trips", visa.status_of(jobs[3]) is VisaStatus.SUPPORTED)

jobs = [{"job_id": i, "title": f"J{i}"} for i in ("1", "2", "4")]
n = visa.check_visa_support(jobs, llm=None, fetch_detail=fake_fetch, pause=0, max_checks=2)
check("max_checks respected", n == 2 and jobs[2]["visa_status"] == "unchecked")
check("without LLM unknown stays unknown", jobs[1]["visa_status"] == "unknown")

n2 = visa.check_visa_support(jobs, llm=None, fetch_detail=fake_fetch, pause=0)
check("already-decided jobs are not re-fetched", n2 == 1, f"(n2={n2})")

print("\n[7] llm_from_env respects configuration")
import os
os.environ.pop("ANTHROPIC_API_KEY", None)
check("no key -> no LLM", visa.llm_from_env() is None)
os.environ["ANTHROPIC_API_KEY"] = "sk-test"
os.environ["VISA_LLM_ASSIST"] = "0"
check("assist disabled -> no LLM", visa.llm_from_env() is None)
os.environ.pop("VISA_LLM_ASSIST"); os.environ.pop("ANTHROPIC_API_KEY")

print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
raise SystemExit(0 if ok else 1)
