// Notion links + stored variants attached to the application payload (read-only SQL).
import { describe, expect, it } from "vitest";
import { applicationDetail, getApplicationRec, listApplicationRecs } from "../server/application";
import { all, get } from "../server/db";
import { NOTION_ROOT_URL, notionEvidence, notionUrl } from "../server/library";
import { buildPrompt } from "../server/claude";

const url = (id: string | null | undefined) => (id ? `https://www.notion.so/${id.replace(/-/g, "")}` : null);

describe("library links and variants", () => {
  const d = applicationDetail(getApplicationRec("TOMRA")!);

  it("VI1 lists every stored lead variant, exactly one current, and the Notion URL from bullets.notion_id", () => {
    const vi1 = d.cv.lines.find((l) => l.pick?.code === "VI1")!;
    const n = get<{ n: number }>("SELECT COUNT(*) AS n FROM bullet_leads WHERE bullet_code = 'VI1'")!.n;
    expect(vi1.library?.variants).toHaveLength(n);
    expect(vi1.library!.variants.every((v) => v.kind === "lead")).toBe(true);
    expect(vi1.library!.variants.filter((v) => v.current)).toHaveLength(1);
    expect(vi1.library!.variants.find((v) => v.current)!.ord).toBe(vi1.pick!.variant);
    const b = get<{ notion_id: string | null; project_code: string }>("SELECT notion_id, project_code FROM bullets WHERE code = 'VI1'")!;
    expect(vi1.library!.notionUrl).toBe(url(b.notion_id));
    const p = get<{ notion_id: string | null }>("SELECT notion_id FROM projects WHERE code = ?", b.project_code)!;
    expect(vi1.library!.projectNotionUrl).toBe(url(p.notion_id));
    expect(vi1.library!.bodyPreview).toBeTruthy();
  });

  it("cards carry title + context variants and the scope line lists every scope row", () => {
    const card = d.cv.lines.find((l) => l.kind === "card" && l.pick?.code === "VI")!;
    const kinds = new Set(card.library!.variants.map((v) => v.kind));
    expect(kinds).toEqual(new Set(["title", "context"]));
    expect(card.library!.variants.filter((v) => v.kind === "title" && v.current)).toHaveLength(1);
    const scope = d.cv.lines.find((l) => l.kind === "scope")!;
    const n = get<{ n: number }>("SELECT COUNT(*) AS n FROM scope_lines")!.n;
    expect(scope.library!.variants).toHaveLength(n);
    expect(scope.library!.variants.find((v) => v.current)?.code).toBe(scope.pick!.code);
  });

  it("skill rows link items to skills.notion_id", () => {
    const row = d.cv.lines.find((l) => l.kind === "skill_row" && l.pick?.code === "Languages & backend")!;
    const py = row.library!.skillItems!.find((s) => s.item === "Python")!;
    expect(py.notionUrl).toBe(url(get<{ notion_id: string }>("SELECT notion_id FROM skills WHERE name = 'Python'")!.notion_id));
  });

  it("header links: application page and Personal experiences root", () => {
    const id = get<{ notion_id: string | null }>("SELECT notion_id FROM applications WHERE jd = 'TOMRA'")!.notion_id;
    expect(d.notion).toEqual({ applicationUrl: url(id), rootUrl: NOTION_ROOT_URL });
  });

  const notionRe = /^https:\/\/www\.notion\.so\/[0-9a-f]{32}$/;
  const details = listApplicationRecs().map((r) => (r.jd === "TOMRA" ? d : applicationDetail(r)));

  it("a keyword covered by PS4 lists PS4 with a Notion URL and its leads (missing first, else any)", () => {
    // Today every PS4-covered JD term is already found on its CV, so fall back to a found one.
    const rows = details.flatMap((det) => det.keywords.filter((k) => k.library.bulletRefs.some((b) => b.code === "PS4")));
    expect(rows.length, "some JD term is covered by PS4").toBeGreaterThan(0);
    const k = rows.find((x) => !x.found) ?? rows[0];
    const ps4 = k.library.bulletRefs.find((b) => b.code === "PS4")!;
    expect(ps4.notionUrl ?? ps4.projectNotionUrl).toMatch(notionRe);
    expect(ps4.leads.length).toBe(get<{ n: number }>("SELECT COUNT(*) AS n FROM bullet_leads WHERE bullet_code = 'PS4'")!.n);
    const kw = get<{ notion_id: string | null }>("SELECT notion_id FROM keywords WHERE lower(term) = lower(?)", k.term);
    expect(k.library.notionUrl).toBe(url(kw?.notion_id));
  });

  it("a missing keyword lists its covering bullets with links", () => {
    const missing = details.flatMap((det) => det.keywords.filter((k) => !k.found && k.library.bulletRefs.length));
    expect(missing.length, "some application misses a library-covered term").toBeGreaterThan(0);
    for (const k of missing) {
      expect(k.library.bulletRefs.map((b) => b.code)).toEqual(k.library.bullets);
      for (const b of k.library.bulletRefs) expect(b.notionUrl ?? b.projectNotionUrl, `${k.term} ${b.code}`).toMatch(notionRe);
    }
  });

  it("every keyword row carries notionUrl", () => {
    const ids = new Map(all<{ term: string; notion_id: string | null }>("SELECT term, notion_id FROM keywords").map((r) => [r.term.toLowerCase(), r.notion_id]));
    for (const k of d.keywords) expect(k.library.notionUrl, k.term).toBe(url(ids.get(k.term.toLowerCase())));
  });

  it("the prompt names the Notion evidence pages of the target", () => {
    const ev = notionEvidence({ kind: "bullet", code: "VI1" });
    const vi = get<{ notion_id: string }>("SELECT notion_id FROM projects WHERE code = 'VI'")!.notion_id;
    expect(ev).toContain(notionUrl(vi));
    const prompt = buildPrompt("TOMRA", "x", { kind: "bullet", code: "VI1" }, "hi", ev);
    expect(prompt).toContain(`Notion evidence pages for this target: ${ev.join(", ")}`);
    expect(buildPrompt("TOMRA", "x", { kind: "summary" }, "hi", [])).not.toContain("Notion evidence pages");
  });
});
