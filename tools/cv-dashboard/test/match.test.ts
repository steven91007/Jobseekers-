import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { calculateKeywordMatch, keywordInText, termRanges } from "../shared/match";
import { getApplicationRec, listApplicationRecs, appDir } from "../server/application";

describe("keywordInText (port of text_match.keyword_in_text)", () => {
  it("is case-insensitive and whole-word", () => {
    expect(keywordInText("python", "Senior PYTHON engineer")).toBe(true);
    expect(keywordInText("python", "pythonic code")).toBe(false);
    expect(keywordInText("java", "javascript")).toBe(false);
    expect(keywordInText("CI/CD", "GitLab CI/CD pipelines")).toBe(true);
    expect(keywordInText("REST APIs", "REST API development")).toBe(false);
    expect(keywordInText("Vue.js", "Vue.js")).toBe(true);
    expect(keywordInText("C++", "C++ and Rust")).toBe(true);
    expect(keywordInText("  ", "anything")).toBe(false);
  });
  it("treats CJK as a boundary and matches CJK terms as substrings", () => {
    expect(keywordInText("Python", "使用Python開發")).toBe(true);
    expect(keywordInText("機器學習", "深度機器學習模型")).toBe(true);
    expect(keywordInText("ML", "MLflow")).toBe(false);
  });
  it("termRanges finds the same matches", () => {
    expect(termRanges("training", "LoRA training, retraining, Training")).toEqual([
      [5, 13],
      [27, 35],
    ]);
  });
});

describe("ported matcher reproduces 07_external_ats.json keyword_match.found", () => {
  const recs = listApplicationRecs();
  it("has the six recorded applications", () => {
    expect(recs.map((r) => r.jd).sort()).toEqual(["AIVenture", "Ixopay", "Mapbox", "Scout24", "TOMRA", "Taktile"]);
  });
  for (const jd of ["TOMRA", "Ixopay", "Scout24", "AIVenture", "Mapbox", "Taktile"]) {
    it(jd, () => {
      const dir = appDir(getApplicationRec(jd)!);
      const md = readFileSync(path.join(dir, "03_cv.md"), "utf-8");
      const job = JSON.parse(readFileSync(path.join(dir, "07_job_keywords.json"), "utf-8"));
      const ats = JSON.parse(readFileSync(path.join(dir, "07_external_ats.json"), "utf-8"));
      const m = calculateKeywordMatch(md, job);
      expect(m.found).toEqual(ats.keyword_match.found);
      expect(m.missing).toEqual(ats.keyword_match.missing);
      expect(Math.round(m.percent * 10) / 10).toBe(ats.keyword_match.percent);
    });
  }
});
