// Concept layer: keyword → concept grouping (SQL), align / gaps parsing (library_db.py), prompt context.
import { describe, expect, it } from "vitest";
import { appDir, applicationDetail, getApplicationRec, listApplications } from "../server/application";
import { all } from "../server/db";
import { alignFor, gapsFor, sotLeadsFor, parseAlignOutput, parseGapsOutput } from "../server/concepts";
import { buildPrompt, conceptContextFor } from "../server/claude";

const scout = getApplicationRec("Scout24")!;

describe("concept layer", () => {
  it("every Scout24 keyword with a concept_code appears under its concept group", () => {
    const d = applicationDetail(scout);
    const codes = new Map(
      all<{ term: string; concept_code: string | null }>("SELECT term, concept_code FROM keywords").map((r) => [r.term.toLowerCase(), r.concept_code]),
    );
    const groups = new Map(d.concepts.map((c) => [c.code, c]));
    let checked = 0;
    for (const k of d.keywords) {
      const code = codes.get(k.term.toLowerCase());
      if (!code) continue;
      checked++;
      expect(k.concept?.code, k.term).toBe(code);
      expect(groups.get(code)?.jdTerms.map((t) => t.term), k.term).toContain(k.term);
    }
    expect(checked).toBeGreaterThan(30);
    for (const c of d.concepts) {
      const w = { required: 3, preferred: 2, keyword: 1 } as const;
      expect(c.weight).toBe(Math.max(...c.jdTerms.map((t) => w[t.type])));
      for (const t of c.jdTerms) expect(t.found).toBe(d.keywords.find((k) => k.term === t.term)!.found);
    }
    const agentic = groups.get("AGENTIC")!;
    expect(agentic.evidence.find((e) => e.code === "DT1")).toMatchObject({ kind: "bullet", strength: "direct", projectCode: "DT" });
    expect(agentic.evidence.find((e) => e.code === "DT1")!.notionUrl).toMatch(/^https:\/\/www\.notion\.so\/[0-9a-f]{32}$/);
    expect(agentic.aliases.length).toBeGreaterThan(5);
  });

  it("applications carry covered / SoT / gap concept counts", () => {
    const row = listApplications().find((r) => r.jd === "Scout24")!;
    expect(row.concepts).toMatchObject({ sot: 5, gap: 0, doNotClaim: 0 });
    expect(row.concepts!.covered).toBeGreaterThan(10);
  });

  it("Scout24 align: parses the live table (rows typed, picks string, summary)", async () => {
    // Scout24's recorded picks move as the CV is revised, so assert the parse, not a particular swap
    const a = await alignFor("Scout24");
    expect(a.exitCode).toBe(0);
    expect(a.rows.length).toBeGreaterThan(0);
    for (const r of a.rows) {
      expect(["keep", "use_lead", "new_lead", "spelling", "summary"]).toContain(r.actionKind);
      if (r.actionKind === "use_lead") expect(typeof r.leadOrd).toBe("number");
      if (r.actionKind === "spelling") expect(r.variant ?? r.current).toBeTruthy();
    }
    expect(a.picks).toMatch(/^scope=/);
    expect(a.summary).toMatch(/lead swap|NEW LEAD/);
  });

  it("TOMRA align has no NEW LEAD rows (spec v3 §6.5 regression guard)", async () => {
    const a = await alignFor("TOMRA");
    expect(a.exitCode).toBe(0);
    expect(a.rows.filter((r) => r.actionKind === "new_lead")).toHaveLength(0);
    expect(a.picksUnchanged).toBe(true);
  });

  it("parses NEW LEAD, body spelling and keep rows", () => {
    const a = parseAlignOutput(
      [
        "note: bullet VI3 (card VI) placed under card PS",
        "| line | concept | JD phrase | current wording | action |",
        "|---|---|---|---|---|",
        "| PS1 | DATA_PLATFORM (w3) | DynamoDB | tools: - | spelling: write 'databases' |",
        '| DT1 | AGENTIC (w3) | LLM orchestration | tools: - | NEW LEAD "Agentic workflow orchestration (LangGraph)" → library mode |',
        "| PS2 | CICD (w1) | release | lead: x | keep lead_ord 2 |",
        "",
        "3 concept mentions · 0 lead swap row(s) · 1 NEW LEAD proposal(s)",
        "picks with lead ords applied (unchanged):",
        "scope=SCOPE;bullet=PS1:6",
      ].join("\n"),
    );
    expect(a.notes).toEqual(["note: bullet VI3 (card VI) placed under card PS"]);
    expect(a.rows.map((r) => r.actionKind)).toEqual(["spelling", "new_lead", "keep"]);
    expect(a.rows[0]).toMatchObject({ variant: "databases", weight: 3 });
    expect(a.rows[1].proposal).toBe("Agentic workflow orchestration (LangGraph)");
    expect(a.rows[2].leadOrd).toBe(2);
    expect(a).toMatchObject({ picks: "scope=SCOPE;bullet=PS1:6", picksUnchanged: true });
  });

  it("Scout24 gaps: the five SoT-supported concepts, with typed JD terms and evidence", async () => {
    const g = await gapsFor("Scout24", appDir(scout));
    expect(g.exitCode).toBe(0);
    const codes = g.groups.sotSupported.map((c) => c.code);
    for (const c of ["ASYNC_STREAM", "DATA_SECURITY", "MODEL_ROUTING", "SYSTEM_DESIGN", "RESPONSIBLE_AI"]) expect(codes).toContain(c);
    const sec = g.groups.sotSupported.find((c) => c.code === "DATA_SECURITY")!;
    expect(sec.weight).toBe(3);
    expect(sec.jdTerms).toContainEqual({ term: "access control", type: "required" });
    expect(sec.evidence.map((e) => `${e.code}:${e.strength}`)).toEqual(["DT1:implied", "PS1:implied"]);
    expect(g.groups.coveredNotClaimable[0]).toMatchObject({ code: "DOC_PROCESSING", jdTerms: [{ term: "employee services", status: "gap" }] });
    expect(Array.isArray(g.unresolved)).toBe(true);
  });

  it("Scout24 sot-leads: every picked bullet has terms, a draft and its body", async () => {
    const s = await sotLeadsFor("Scout24");
    expect(s.exitCode).toBe(0);
    const codes = Object.keys(s.bullets);
    expect(codes.length).toBeGreaterThan(0);
    for (const c of codes) {
      expect(s.bullets[c].terms.length).toBeGreaterThan(0);
      expect(s.bullets[c].draft).toBeTruthy();
      expect(s.bullets[c].body).toBeTruthy();
    }
  });

  it("parses gap / do-not-claim groups and multi-word skill evidence", () => {
    const g = parseGapsOutput(
      [
        "## SoT-supported wording (1) — x",
        "- CODING_AGENTS (w1) Coding agents & developer tooling · JD: CLI [kw]",
        "    evidence: skill Claude Code · Codex (direct) · notes: SoT: no bullet yet",
        "## gap (1) — learn / omit",
        "- EDGE (w2) Edge deployment · JD: offline [pref] · intermittent connectivity [pref]",
        "    evidence: none",
        "## do not claim (1) — never on the CV",
        "- AWS_CLOUD (w3) AWS / public cloud · JD: AWS [req]",
        "    evidence: none",
      ].join("\n"),
    );
    expect(g.groups.sotSupported[0].evidence).toEqual([{ kind: "skill", code: "Claude Code · Codex", strength: "direct", notionUrl: null }]);
    expect(g.groups.sotSupported[0].notes).toBe("SoT: no bullet yet");
    expect(g.groups.gap[0].jdTerms.map((t) => t.type)).toEqual(["preferred", "preferred"]);
    expect(g.groups.doNotClaim[0]).toMatchObject({ code: "AWS_CLOUD", weight: 3, evidence: [] });
  });

  it("the prompt for a VI1 target carries the Concept context block and the v3 rule", () => {
    const dir = appDir(getApplicationRec("TOMRA")!);
    const ctx = conceptContextFor({ kind: "bullet", code: "VI1" }, dir);
    const prompt = buildPrompt("TOMRA", "x", { kind: "bullet", code: "VI1" }, "hi", [], ctx);
    expect(prompt).toContain("Concept context");
    expect(prompt).toMatch(/VI1 proves: .*TRAIN_PIPELINE/);
    expect(prompt).toContain("never rewrite a body semantically");
    const term = conceptContextFor({ kind: "term", term: "agentic workflows", code: "DT1" }, appDir(scout));
    expect(term).toMatch(/'agentic workflows' → AGENTIC/);
    expect(term).toMatch(/evidence: .*bullet DT1 \(direct\)/);
    expect(conceptContextFor({ kind: "summary" }, dir)).toBe("");
  });
});
