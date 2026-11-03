import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { REPO_ROOT } from "../server/config";
import { applicationDetail, getApplicationRec, listApplications } from "../server/application";
import { parseCv } from "../shared/cvparse";
import { lineChanges } from "../shared/diff";
import { diffPicks } from "../server/libcli";
import { reviewSummary } from "../server/artifacts";

describe("applications list", () => {
  it("ATS overall equals each 07_external_ats.json", () => {
    // the files change whenever a CV is revised, so compare against them instead of fixed numbers
    const rows = listApplications();
    expect(rows.map((r) => r.jd).sort()).toEqual(["AIVenture", "Ixopay", "Mapbox", "Scout24", "TOMRA", "Taktile"]);
    for (const r of rows) {
      const ats = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, r.dir, "07_external_ats.json"), "utf8"));
      expect(r.atsOverall, r.jd).toBe(ats.ats_score.overall_score);
    }
  });
});

describe("TOMRA CV parse", () => {
  const d = applicationDetail(getApplicationRec("TOMRA")!);
  const lines = d.cv.lines;
  const byId = new Map(lines.map((l) => [l.id, l]));

  it("has 6 Delta bullets with the picked codes", () => {
    const bullets = lines.filter((l) => l.kind === "bullet");
    expect(bullets.map((b) => b.pick?.code)).toEqual(["VI1", "VI2", "VI5", "PS1", "PS2", "PS4"]);
    expect(bullets.every((b) => b.role?.includes("Delta Electronics"))).toBe(true);
    expect(bullets.map((b) => b.pick?.variant)).toEqual([0, 1, 0, 1, 1, 0]);
  });
  it("has 6 Skills rows and 2 cards", () => {
    expect(lines.filter((l) => l.kind === "skill_row")).toHaveLength(6);
    const cards = lines.filter((l) => l.kind === "card");
    expect(cards.map((c) => c.pick?.code)).toEqual(["VI", "PS"]);
    expect(cards[1].pick?.contextOrd).toBe(1);
  });
  it("tags scope, headline, summary and the Mirle bullets", () => {
    expect(lines.find((l) => l.kind === "scope")?.pick?.code).toBe("SCOPE-ATS");
    expect(lines.find((l) => l.kind === "headline")?.scanned).toBe(false);
    expect(lines.find((l) => l.kind === "summary")?.scanned).toBe(true);
    expect(lines.filter((l) => l.kind === "other_bullet").map((l) => l.pick?.code)).toEqual(["MIRLE1", "MIRLE2"]);
  });
  it("maps terms to items", () => {
    const dm = d.hits.byTerm["data modelling"].map((id) => byId.get(id)!);
    expect(dm.map((i) => [i.kind, i.label])).toEqual([["skill_row", "Data & UI"]]);
    const pm = d.hits.byTerm["project management"].map((id) => byId.get(id)!);
    expect(pm.map((i) => i.pick?.code)).toEqual(["PS4"]);
  });
  it("groups keywords like ats_check.library_status", () => {
    const missing = d.keywords.filter((k) => !k.found);
    expect(missing).toHaveLength(17);
    expect(missing.filter((k) => k.library.group === "do not claim").map((k) => k.term)).toEqual(["cloud", "x64"]);
    expect(missing.filter((k) => k.library.group === "gap")).toHaveLength(15);
    expect(d.keywords.find((k) => k.term === "training")?.jdCount).toBe(4);
  });
  it("reads layout and review", () => {
    expect(d.layout.status).toBe("PASS");
    expect(d.layout.utilisation).toBe("96%");
    expect([d.review.hr, d.review.hm, d.review.verdict]).toEqual([82, 79, "REVISE"]);
    expect(d.review.edits).toHaveLength(5);
  });
});

describe("pure helpers", () => {
  it("parseCv handles CRLF and headings with inline content", () => {
    const md = "**N**\r\n**H**\r\nC\r\n**PROFESSIONAL PROFILE**\r\nSum\r\n**LANGUAGES**  English C1\r\n";
    const items = parseCv(md, ["English"]);
    expect(items.map((i) => i.kind)).toEqual(["name", "headline", "contact", "section", "summary", "section", "other"]);
  });
  it("lineChanges pairs old → new", () => {
    expect(lineChanges("a\nb\nc\n", "a\nB\nc\nd\n")).toEqual([
      { old: "b", new: "B" },
      { old: null, new: "d" },
    ]);
    expect(lineChanges("x\r\ny", "x\ny")).toEqual([]);
  });
  it("diffPicks aligns on kind=code", () => {
    const rows = diffPicks("scope=SCOPE-ATS;bullet=VI1:0;bullet=VI2:1", "scope=SCOPE;bullet=VI1:1;bullet=VI3:1");
    expect(rows.map((r) => r.state)).toEqual(["current only", "changed", "current only", "suggested only", "suggested only"]);
  });
  it("reviewSummary reads the first HR/HM and verdict", () => {
    const r = reviewSummary("# x\nHR 70 · HM 65 · VERDICT: SUBMIT\nHR 10\n| # | Edit |\n|---|---|\n| 1 | a |\n");
    expect([r.hr, r.hm, r.verdict, r.edits.length]).toEqual([70, 65, "SUBMIT", 1]);
  });
});
