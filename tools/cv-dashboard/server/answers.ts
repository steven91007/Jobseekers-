// 表單問答 (form answers): the candidate pastes an application form's questions; a headless Claude run
// with the form-answers skill writes a paste-ready answer for each into $D/08_form_answers.json.
// It never opens, fills or submits a form (replaces the Auto apply fill run since 2026-11-03).
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { FormAnswer, FormAnswersInfo } from "../shared/types.js";
import type { AppRec } from "./db.js";
import { REPO_ROOT } from "./config.js";
import { readJson, readText } from "./artifacts.js";

export const ANSWERS_TIMEOUT_MS = 10 * 60 * 1000;
export const MAX_QUESTIONS_CHARS = 20000;
const LIBRARY_DB = path.join(REPO_ROOT, ".claude/skills/tailor-cv/scripts/library_db.py");
const SKILL_MD = path.join(REPO_ROOT, ".claude/skills/form-answers/SKILL.md");

export const answersFile = (dir: string) => path.join(dir, "08_form_answers.json");
export const questionsFile = (dir: string) => path.join(dir, "08_form_questions.txt");

export function validQuestions(raw: unknown): string {
  if (typeof raw !== "string" || !raw.trim()) throw new Error("請貼上至少一個問題");
  if (raw.length > MAX_QUESTIONS_CHARS) throw new Error(`問題太長(最多 ${MAX_QUESTIONS_CHARS} 字元)`);
  return raw.replace(/\r\n/g, "\n").trim();
}

export function answersInfo(rec: AppRec, dir: string): Omit<FormAnswersInfo, "runId" | "runStatus"> {
  const data = readJson<{ answers?: FormAnswer[]; skipped?: string[]; generatedAt?: string }>(answersFile(dir));
  const answers = Array.isArray(data?.answers)
    ? data!.answers.filter((a) => a && typeof a.question === "string").map((a) => ({ ...a, answer: String(a.answer ?? "") }))
    : null;
  return {
    jd: rec.jd,
    questions: readText(questionsFile(dir)),
    answers,
    skipped: Array.isArray(data?.skipped) ? data!.skipped.map(String) : [],
    generatedAt: data?.generatedAt ?? null,
  };
}

export function saveQuestions(dir: string, questions: string): void {
  mkdirSync(dir, { recursive: true });
  writeFileSync(questionsFile(dir), questions + "\n");
}

export function answersPrompt(rec: AppRec, dir: string): string {
  return `/form-answers

Unattended CV-dashboard run (no human can answer mid-run; the candidate reads the result on the dashboard, maybe on a phone).
Read ${SKILL_MD} and follow it.
- Job: ${rec.company} · ${rec.title}. $D = ${dir} (use this dir; do not search for another).
- Questions: ${questionsFile(dir)} (Read it).
- Write ${answersFile(dir)} and ${path.join(dir, "08_form_answers.md")} exactly as the skill says. Write nothing else anywhere.
- Never open a browser or a form. Finish with one line: how many answers, how many need the candidate.`;
}

// Writes are allowed by acceptEdits only inside the run's cwd (= $D), so no Write rule is needed.
export function answersArgv(prompt: string): string[] {
  const tools = [
    "Read",
    "Glob",
    "Grep",
    `Bash(python3 ${LIBRARY_DB} cards)`,
    `Bash(python3 ${LIBRARY_DB} card *)`,
    `Bash(python3 ${LIBRARY_DB} bullet *)`,
  ];
  return [
    "-p",
    prompt,
    "--output-format",
    "stream-json",
    "--verbose",
    "--model",
    process.env.ANSWERS_MODEL ?? "opus",
    "--permission-mode",
    "acceptEdits",
    `--allowedTools=${tools.join(",")}`,
    "--disallowedTools=WebFetch,WebSearch,mcp__playwright__*",
  ];
}
