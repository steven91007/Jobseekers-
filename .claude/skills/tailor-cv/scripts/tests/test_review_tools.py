"""Unit tests for review_tools.py (stdlib only).

    python3 -I -m unittest discover -s .claude/skills/tailor-cv/scripts/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import review_tools as rt  # noqa: E402

CV = """**PROFESSIONAL PROFILE**
**Senior AI Engineer** profile text.
**PROFESSIONAL EXPERIENCE**
**Senior AI Engineer**  |  Delta Electronics\tJan 2022 – Present
- **A —** one.
- **B —** two.
- **C —** three.
**AI Algorithm Engineer**  |  Mirle\tJan 2021 – Jan 2022
- **X —** other role.
"""


def review(points=(22, 18, 17, 12, 9, 8), weights=(25, 20, 20, 15, 10, 10), stated=None,
           verdict="PASS", claims=(), oos="none", writing="none", hard="MET"):
    total = sum(points) if stated is None else stated
    cats = list(rt.CATEGORIES)
    rows = "\n".join(f"| {c} | {w} | {p} | e | w | i |" for c, w, p in zip(cats, weights, points))
    base = [("ok claim", "Verified", "low"), ("A — one", "Verified", "low"), ("B — two", "Verified", "low"), ("C — three", "Verified", "low")]
    crow = "\n".join(f"| {i} | {c} | {s} | {sev} | n |" for i, (c, s, sev) in enumerate(base + list(claims), 1))
    return f"""JOB FIT SCORE: {total}/100
VERDICT: {verdict}
HARD REQUIREMENTS: {hard}

## Weighted job fit
| Category | Weight | Points | Evidence | Weaknesses | Improvement |
|---|---|---|---|---|---|
{rows}

## Claim audit
| # | Claim | Status | Severity | Note |
|---|---|---|---|---|
{crow}

## Writing issues
{writing}

## Out-of-scope edits
{oos}
"""


class GateTests(unittest.TestCase):
    def test_pass(self):
        g = rt.gate(review(), CV, 85, 5)
        self.assertEqual(g["score"], 86)
        self.assertEqual(g["derived_verdict"], "PASS")
        self.assertEqual(g["delta_bullets"], 3)
        self.assertFalse(g["verdict_mismatch"])

    def test_below_threshold(self):
        g = rt.gate(review(points=(20, 15, 15, 10, 8, 7)), CV, 85, 5)
        self.assertEqual(g["derived_verdict"], "REVISE")
        self.assertTrue(g["verdict_mismatch"])  # reviewer said PASS

    def test_weights_must_sum_100(self):
        g = rt.gate(review(weights=(25, 20, 20, 15, 10, 5), points=(22, 18, 17, 12, 9, 5)), CV, 0, 5)
        self.assertIn("weights sum to 95", " ".join(g["reasons"]))
        self.assertEqual(g["derived_verdict"], "REVISE")

    def test_points_cannot_exceed_weight(self):
        g = rt.gate(review(points=(26, 18, 17, 12, 9, 8)), CV, 0, 5)
        self.assertTrue(any("outside 0..25" in r for r in g["reasons"]))

    def test_stated_score_mismatch(self):
        g = rt.gate(review(stated=95), CV, 85, 5)
        self.assertTrue(any("!= recomputed" in r for r in g["reasons"]))

    def test_contradicted_claim_blocks(self):
        g = rt.gate(review(claims=[("6x faster", "Contradicted", "medium")]), CV, 85, 5)
        self.assertEqual(g["derived_verdict"], "NEEDS EVIDENCE")
        self.assertEqual(g["unverifiable"], 1)

    def test_missing_evidence_blocks_at_any_severity(self):
        for sev in ("high", "low"):
            g = rt.gate(review(claims=[("prod deploy", "Missing Evidence", sev)]), CV, 85, 5)
            self.assertEqual(g["derived_verdict"], "NEEDS EVIDENCE", sev)

    def test_inferred_blocks_unless_low(self):
        self.assertEqual(rt.gate(review(claims=[("x", "Inferred", "medium")]), CV, 85, 5)["derived_verdict"], "NEEDS EVIDENCE")
        self.assertEqual(rt.gate(review(claims=[("x", "Inferred", "low")]), CV, 85, 5)["derived_verdict"], "PASS")

    def test_user_reported_does_not_block(self):
        g = rt.gate(review(claims=[("set up MinIO", "User-reported", "high")]), CV, 85, 5)
        self.assertEqual(g["derived_verdict"], "PASS")

    def test_out_of_scope_and_bullets(self):
        g = rt.gate(review(oos="- Mirle bullet changed"), CV, 85, 5)
        self.assertIn("out-of-scope edits present", g["reasons"])
        g2 = rt.gate(review(), CV, 85, 2)
        self.assertTrue(any("Delta bullets > max 2" in r for r in g2["reasons"]))

    def test_high_writing_issue_blocks(self):
        g = rt.gate(review(writing='- [high] "x" — wrong claim → rewrite: "y"'), CV, 85, 5)
        self.assertEqual(g["derived_verdict"], "REVISE")


class OtherTests(unittest.TestCase):
    def test_unaudited_bullet_fails(self):
        rv = review().replace("| C — three |", "| something else |")
        g = rt.gate(rv, CV, 85, 5)
        self.assertTrue(any("not audited" in r for r in g["reasons"]))

    def test_template_status_counts_but_does_not_block(self):
        g = rt.gate(review(claims=[("10x throughput", "Template", "high")]), CV, 85, 5)
        self.assertEqual(g["derived_verdict"], "PASS")
        self.assertEqual(g["template_only"], 1)

    def test_ledger_cv_number_crosscheck(self):
        ev = """## Claim ledger
| ID | Claim | Status | Source | Measurement scope | Notes |
|---|---|---|---|---|---|
| C1 | accuracy 60% → 96% | Verified | n | 11 docs | |
| C2 | old 433s → 147s | Contradicted | n | s | (removed) |
"""
        cv = "**PROFESSIONAL PROFILE**\nProfile with 60% and 147s.\n**Senior AI Engineer**  |  Delta Electronics\tJan 2022 – Present\n- **Lead —** rose to 96% on 11 docs.\n"
        out = rt.ledger(ev, cv)
        self.assertEqual(out["cv_numbers_missing_from_ledger"], ["147"])  # only in a retired row
        self.assertTrue(out["errors"])

    def test_grouped_bullet_coverage_fails(self):
        rv = review().replace("| A — one |", "| A — one B — two C — three |").replace("| B — two |", "| unrelated |").replace("| C — three |", "| unrelated too |")
        g = rt.gate(rv, CV, 85, 5)
        self.assertTrue(any("covers 3 bullets" in r for r in g["reasons"]), g["reasons"])

    def test_ledger_number_only_in_source_is_missing(self):
        ev = """## Claim ledger
| ID | Claim | Status | Source | Measurement scope | Notes |
|---|---|---|---|---|---|
| C1 | latency 5s | Verified | https://notion.so/147 | n=3 | |
"""
        cv = "**PROFESSIONAL PROFILE**\nLatency 147s.\n**Senior AI Engineer**  |  Delta Electronics\tJan 2022 – Present\n- **Lead —** 5s.\n"
        self.assertEqual(rt.ledger(ev, cv)["cv_numbers_missing_from_ledger"], ["147"])

    def test_ledger(self):
        ev = """## Claim ledger
| ID | Claim (as it appears in the CV) | Status | Source | Measurement scope | Notes |
|---|---|---|---|---|---|
| C1 | a | Verified | n | s | |
| C2 | b | User-reported | user | s | |
| C3 | c | Contradicted | n | s | |
| C4 | d | weird | n | s | |
"""
        out = rt.ledger(ev + "| C5 | e | Contradicted | n | s | (removed) in r10 |\n")
        self.assertEqual(out["retired"]["Contradicted"], 1)
        self.assertEqual(out["live_unverifiable"], 1)
        self.assertEqual(out["counts"]["Verified"], 1)
        self.assertEqual(out["counts"]["Contradicted"], 1)
        self.assertEqual(len(out["unknown_status_rows"]), 1)

    def test_compare(self):
        out = rt.compare(review(points=(20, 15, 15, 10, 8, 7)), review())
        self.assertEqual(out["before"], 75)
        self.assertEqual(out["after"], 86)
        self.assertEqual(out["delta"], 11)

    def test_recommend(self):
        ok = {"status": "PASS"}
        rec = lambda rv, lay=ok: rt.recommend(rv, CV, lay, 85, 5)["recommendation"]
        self.assertEqual(rec(review()), "Ready to Apply")
        self.assertEqual(rec(review(claims=[("x", "Contradicted", "high")])), "Needs Evidence")
        self.assertEqual(rec(review(hard="AT RISK")), "Needs Evidence")
        self.assertEqual(rec(review(), {"status": "WARN"}), "Needs Evidence")
        self.assertEqual(rec(review(), {}), "Needs Evidence")            # layout unverified
        self.assertEqual(rec(review(points=(20, 15, 15, 10, 8, 7))), "Major Revision Required")  # below threshold
        self.assertEqual(rec(review(), {"status": "FAIL"}), "Major Revision Required")
        self.assertEqual(rec(review(hard="NOT MET")), "Major Revision Required")
        self.assertEqual(rec(review(oos="- Mirle bullet changed")), "Major Revision Required")

    def test_recommend_exit_code(self):
        import json, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("r.md", review(hard="AT RISK")), ("cv.md", CV), ("l.json", json.dumps({"status": "PASS"}))):
                Path(d, name).write_text(text, encoding="utf-8")
            code = rt.main(["recommend", "--review", os.path.join(d, "r.md"), "--cv", os.path.join(d, "cv.md"), "--layout", os.path.join(d, "l.json")])
            self.assertEqual(code, 1)


class FormatValidationTests(unittest.TestCase):
    """A malformed review must never pass silently."""

    def test_missing_claim_audit(self):
        rv = review().replace("## Claim audit", "## Claims")
        g = rt.gate(rv, CV, 85, 5)
        self.assertEqual(g["derived_verdict"], "REVISE")
        self.assertTrue(any("Claim audit' missing" in r for r in g["reasons"]))

    def test_heading_with_trailing_findings(self):
        rv = review().replace("## Out-of-scope edits\nnone", "## Out-of-scope edits: Education changed\nnone")
        g = rt.gate(rv, CV, 85, 5)
        self.assertEqual(g["derived_verdict"], "REVISE")

    def test_none_followed_by_violation(self):
        g = rt.gate(review(oos="none\n- Education reordered"), CV, 85, 5)
        self.assertIn("out-of-scope edits present", g["reasons"])

    def test_invented_category(self):
        rv = review().replace("Core technical competencies", "Vibes")
        self.assertEqual(rt.gate(rv, CV, 0, 5)["derived_verdict"], "REVISE")

    def test_unknown_status_and_severity(self):
        self.assertEqual(rt.gate(review(claims=[("x", "Probably fine", "low")]), CV, 85, 5)["derived_verdict"], "REVISE")
        self.assertEqual(rt.gate(review(claims=[("x", "Verified", "critical")]), CV, 85, 5)["derived_verdict"], "REVISE")

    def test_extra_pipe_in_cell(self):
        rv = review().replace("| 1 | ok claim |", "| 1 | ok | claim |")
        self.assertEqual(rt.gate(rv, CV, 85, 5)["derived_verdict"], "REVISE")

    def test_escaped_pipe_in_cell_is_one_cell(self):
        rv = review().replace("| 1 | ok claim |", "| 1 | Senior AI Engineer \\| Delta |")
        g = rt.gate(rv, CV, 85, 5)
        self.assertEqual(g["derived_verdict"], "PASS", g["reasons"])

    def test_untagged_writing_issue(self):
        self.assertEqual(rt.gate(review(writing='- "x" is vague'), CV, 85, 5)["derived_verdict"], "REVISE")

    def test_delta_not_found(self):
        g = rt.gate(review(), CV.replace("Delta Electronics", "Acme"), 85, 5)
        self.assertIn("Delta role heading not found in the CV", g["reasons"])

    def test_missing_header_lines(self):
        rv = review().replace("HARD REQUIREMENTS: MET\n", "")
        self.assertEqual(rt.gate(rv, CV, 85, 5)["derived_verdict"], "REVISE")

    def test_bullet_count_ignores_other_roles(self):
        self.assertEqual(rt.count_delta_bullets(CV), 3)
        self.assertEqual(rt.count_delta_bullets(CV.replace("Delta Electronics", "Acme")), -1)


if __name__ == "__main__":
    unittest.main()
