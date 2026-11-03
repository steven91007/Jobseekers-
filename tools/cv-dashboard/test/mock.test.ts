// 模擬面試: the server flow with a fake model (no real Claude call) and the real interview-prep mock.py on the
// TOMRA prep file; sessions go to a temp CV_MOCK_DIR, never into cv/.
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterAll, describe, expect, it } from "vitest";

const mockRoot = mkdtempSync(path.join(tmpdir(), "cvd-mock-"));
process.env.CV_MOCK_DIR = mockRoot;

const { answerMock, llmArgv, retryMock, sessionView, setLlm, startMock, turnFromChat, validId } = await import("../server/mock");
const { getApplicationRec } = await import("../server/application");

afterAll(() => {
  setLlm(null);
  rmSync(mockRoot, { recursive: true, force: true });
});

describe("mock interview helpers", () => {
  it("splits a transcript into answer + follow-ups", () => {
    const t = turnFromChat([
      { role: "interviewer", text: "Q?" },
      { role: "candidate", text: "part one" },
      { role: "candidate", text: "part two" },
      { role: "interviewer", text: "Why?" },
      { role: "candidate", text: "because" },
      { role: "interviewer", text: "Unanswered?" },
    ]);
    expect(t).toEqual({ answer: "part one part two", followups: [{ q: "Why?", a: "because" }] });
  });
  it("model calls have no tools, no MCP and no settings", () => {
    const a = llmArgv("sys", "p", { type: "object" }, "sonnet");
    expect(a[a.indexOf("--tools") + 1]).toBe("");
    expect(a).toContain("--strict-mcp-config");
    expect(a[a.indexOf("--setting-sources") + 1]).toBe("");
  });
  it("rejects path-like session ids", () => {
    expect(() => validId("../../etc/passwd")).toThrow();
    expect(validId("mock_20261103_185844")).toBe("mock_20261103_185844");
  });
});

const rec = getApplicationRec("TOMRA");
describe.skipIf(!rec || !existsSync(path.join(process.env.CV_REPO_ROOT ?? path.resolve(__dirname, "../../.."), "cv")))("mock interview flow (TOMRA)", () => {
  it("follow-up, refused score retried, reference only after scoring, retry keeps history", async () => {
    const calls: string[] = [];
    let scoreCalls = 0;
    const pad = " I owned the work end to end and reviewed the results with the engineers every week on site.";
    setLlm(async (system, prompt, _schema, model) => {
      if (system.startsWith("You are the interviewer")) {
        calls.push(`followup:${model}`);
        return prompt.includes("Follow-ups asked so far: 0") ? { action: "followup", say: "What exactly was your part?" } : { action: "done" };
      }
      scoreCalls++;
      calls.push(`score:${model}`);
      const better = scoreCalls === 1 ? "I improved delivery speed by 45% on that plan." + pad.repeat(3) : "I split the request into a phased plan and kept stakeholders aligned with demos." + pad.repeat(3);
      const m = /Answer frameworks allowed \(default first\):\n- (\w+) = [^:]+: (.+)/.exec(prompt);
      expect(m).toBeTruthy();
      const labels = m![2].split(" → ").map((x) => x.replace(/ \(.*$/, ""));
      const per = better.split(" ");
      const size = Math.ceil(per.length / labels.length);
      const chunks = labels.map((label, i) => ({ label, yours: "沒有講到", tip: "先講結論", better: per.slice(i * size, (i + 1) * size).join(" ") }));
      return { scores: { structure: 3, evidence: 3, relevance: 3, honesty: 3, clarity: 3, followups: 3 }, feedback: "• 先講結論", framework: m![1], chunks };
    });
    let v = await startMock(rec!, { stage: "mixed", n: 2, followups: 1 });
    expect(v.items).toHaveLength(2);
    expect(v.items.every((it) => it.reference === null)).toBe(true);
    const q = v.items[0].n;
    v = await answerMock(rec!, v.id, { q, text: "We planned it in phases and delivered the first phase on time." + pad });
    const it1 = v.items.find((x) => x.n === q)!;
    expect(it1.status).toBe("answering");
    expect(it1.chat.at(-1)).toEqual({ role: "interviewer", text: "What exactly was your part?" });
    expect(it1.reference).toBeNull();
    v = await answerMock(rec!, v.id, { q, text: "I wrote the plan and ran the demos myself." });
    const it2 = v.items.find((x) => x.n === q)!;
    expect(it2.status).toBe("scored");
    expect(scoreCalls).toBe(2); // the invented 45% was refused by mock.py score, then fixed
    expect(it2.better).not.toContain("45%");
    expect(it2.reference).toBeTruthy();
    expect(it2.framework).toBeTruthy();
    expect(it2.chunks?.length).toBe(it2.frameworks.find((f) => f.key === it2.framework)!.chunks.length);
    expect(v.items[0].frameworks.length).toBeGreaterThan(0);
    expect(calls[0]).toBe("followup:sonnet");
    v = await retryMock(rec!, v.id, { q });
    expect(v.items.find((x) => x.n === q)!.status).toBe("open");
    v = await answerMock(rec!, v.id, { q, text: "I split the work into phases and aligned people with demos." + pad, finish: true });
    expect(v.items.find((x) => x.n === q)!.status).toBe("scored");
    expect(sessionView(rec!, v.id).items.find((x) => x.n === q)!.overall).toBe(3);
  }, 120_000);
});
