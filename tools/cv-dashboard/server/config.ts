import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** tools/cv-dashboard */
export const DASHBOARD_DIR = path.resolve(here, "..");
/** Repo root (two levels up from the dashboard), overridable with CV_REPO_ROOT. */
export const REPO_ROOT = path.resolve(process.env.CV_REPO_ROOT ?? path.join(DASHBOARD_DIR, "..", ".."));
export const DB_PATH = path.join(REPO_ROOT, "cv", "profile", "experience.sqlite");
export const SKILL_SCRIPTS = ".claude/skills/tailor-cv/scripts";
export const LIBRARY_DB_PY = `${SKILL_SCRIPTS}/library_db.py`;
export const DATA_DIR = path.resolve(process.env.CV_DASHBOARD_DATA ?? path.join(DASHBOARD_DIR, "data"));
export const RUNS_FILE = path.join(DATA_DIR, "runs.json");
export const BOARD_FILE = path.join(DATA_DIR, "run_board.json");
export const SHARED_LOCK_PY = "tools/cv-dashboard/scripts/shared_lock.py";
export const DIST_DIR = path.join(DASHBOARD_DIR, "dist");

export const HOST = "127.0.0.1";
export const API_PORT = Number(process.env.CV_DASHBOARD_PORT ?? 5174);
export const PYTHON = process.env.CV_PYTHON ?? "python3";
export const CLAUDE_BIN = process.env.CLAUDE_BIN ?? "claude";
export const CLAUDE_TIMEOUT_MS = 15 * 60 * 1000;
export const MAX_CONCURRENT_RUNS = Math.max(1, Number(process.env.MAX_CONCURRENT_RUNS ?? 3) || 3);

/**
 * applications.dir may be absolute (Linux or Windows style) or relative to the repo root.
 * Anything from `cv/applications/…` on is re-rooted under REPO_ROOT so CV_REPO_ROOT works.
 */
export function resolveAppDir(dir: string): string {
  const norm = dir.replace(/\\/g, "/");
  const at = norm.indexOf("cv/applications/");
  if (at >= 0) return path.join(REPO_ROOT, norm.slice(at));
  return path.isAbsolute(norm) ? norm : path.join(REPO_ROOT, norm);
}

export function relToRepo(abs: string): string {
  return path.relative(REPO_ROOT, abs).split(path.sep).join("/");
}
