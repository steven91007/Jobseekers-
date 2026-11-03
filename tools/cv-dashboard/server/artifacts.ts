// Readers for the per-application files written by the tailor-cv skill.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import type { AtsSummary, LayoutSummary, ReviewSummary } from "../shared/types.js";

export function readText(file: string): string | null {
  return existsSync(file) ? readFileSync(file, "utf-8").replace(/^﻿/, "") : null;
}

export function readJson<T = any>(file: string): T | null {
  const t = readText(file);
  if (t == null) return null;
  try {
    return JSON.parse(t) as T;
  } catch {
    return null;
  }
}

export function mtime(file: string): number {
  return existsSync(file) ? statSync(file).mtimeMs : 0;
}

export function findPdf(dir: string): string | null {
  if (!existsSync(dir)) return null;
  const pdf = readdirSync(dir).filter((f) => /^CV_.*\.pdf$/i.test(f)).sort()[0];
  return pdf ? path.join(dir, pdf) : null;
}

export interface AtsRaw {
  keyword_match?: { percent: number; found: string[]; missing: string[]; n_terms: number };
  ats_score?: { overall_score: number; sub_scores: Record<string, number>; recommendations?: string[] };
  parse_checks?: {
    file: string;
    extraction_layout_score: number;
    content_score: number;
    checks: { id: string; severity: string; status: string; params: unknown }[];
  }[];
  repeated_terms?: AtsSummary["repeated"];
}

export function atsSummary(raw: AtsRaw | null): AtsSummary {
  const km = raw?.keyword_match;
  return {
    overall: raw?.ats_score?.overall_score ?? null,
    subScores: raw?.ats_score?.sub_scores ?? {},
    keywordPercent: km?.percent ?? null,
    found: km?.found.length ?? 0,
    nTerms: km?.n_terms ?? 0,
    parseChecks: (raw?.parse_checks ?? []).map((p) => ({
      file: p.file,
      extractionLayout: p.extraction_layout_score,
      content: p.content_score,
      flagged: p.checks
        .filter((c) => c.status !== "pass")
        .map((c) => `${c.id} ${c.status.toUpperCase()} (${c.severity})`),
    })),
    repeated: raw?.repeated_terms ?? [],
    recommendations: raw?.ats_score?.recommendations ?? [],
  };
}

interface LayoutRaw {
  status?: string;
  checks?: { check: string; status: string; detail: unknown }[];
}

export function layoutSummary(raw: LayoutRaw | null): LayoutSummary {
  const checks = (raw?.checks ?? []).map((c) => ({
    check: c.check,
    status: c.status,
    detail: typeof c.detail === "string" ? c.detail : JSON.stringify(c.detail),
  }));
  const util = checks.find((c) => c.check === "page_utilization")?.detail;
  return {
    status: raw?.status ?? null,
    utilisation: util ? (/(\d+(?:\.\d+)?%)/.exec(util)?.[1] ?? null) : null,
    checks,
  };
}

/** 04_review.md: first `HR nn` / `HM nn`, VERDICT, and the edits table (header starts with `| #`). */
export function reviewSummary(md: string | null): ReviewSummary {
  const out: ReviewSummary = { hr: null, hm: null, verdict: null, headline: null, edits: [], editsHeader: [] };
  if (!md) return out;
  const hr = /\bHR\s+(\d+)/.exec(md);
  const hm = /\bHM\s+(\d+)/.exec(md);
  const v = /VERDICT:\s*([A-Z][A-Z_ -]*[A-Z])/.exec(md);
  out.hr = hr ? Number(hr[1]) : null;
  out.hm = hm ? Number(hm[1]) : null;
  out.verdict = v ? v[1].trim() : null;
  const lines = md.split(/\r?\n/);
  out.headline = lines.find((l) => /\bHR\s+\d+/.test(l))?.trim() ?? null;
  const cells = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const start = lines.findIndex((l) => /^\|\s*#\s*\|/.test(l));
  if (start >= 0) {
    out.editsHeader = cells(lines[start]);
    for (let i = start + 2; i < lines.length && lines[i].trim().startsWith("|"); i++) {
      out.edits.push({ cells: cells(lines[i]) });
    }
  }
  return out;
}
