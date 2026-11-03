// Read-only access to cv/profile/experience.sqlite through node:sqlite.
import { createRequire } from "node:module";
import { existsSync, statSync } from "node:fs";
import { DB_PATH } from "./config.js";

// Loaded through require so bundlers/test runners that predate `node:sqlite` leave it alone.
const require = createRequire(import.meta.url);

type Row = Record<string, unknown>;
interface Stmt {
  all(...params: unknown[]): Row[];
  get(...params: unknown[]): Row | undefined;
}
interface Db {
  prepare(sql: string): Stmt;
  close(): void;
}

let db: Db | null = null;
let openedMtime = 0;

function open(): Db {
  if (!existsSync(DB_PATH)) throw new Error(`Library DB not found: ${DB_PATH}`);
  // `library_db.py init` (run by Claude) rebuilds the file; reopen when it changes.
  const mtime = statSync(DB_PATH).mtimeMs;
  if (db && mtime === openedMtime) return db;
  db?.close();
  openedMtime = mtime;
  const { DatabaseSync } = require("node:sqlite") as { DatabaseSync: new (p: string, o?: object) => Db };
  // Runs write the DB (library_db.py init / set-*) while the page reads it: wait instead of SQLITE_BUSY.
  db = new DatabaseSync(DB_PATH, { readOnly: true, timeout: 10_000 });
  return db;
}

export function all<T = Row>(sql: string, ...params: unknown[]): T[] {
  return open().prepare(sql).all(...params) as T[];
}

export function get<T = Row>(sql: string, ...params: unknown[]): T | undefined {
  return open().prepare(sql).get(...params) as T | undefined;
}

export interface AppRec {
  jd: string;
  company: string;
  title: string;
  dir: string;
  created: string;
  headline: string;
  country: string;
}

export interface PickRec {
  jd: string;
  kind: string;
  code: string;
  variant_ord: number | null;
  context_ord: number | null;
  lead: string | null;
  items: string | null;
  position: number;
}

export interface KeywordRec {
  term: string;
  type: string | null;
  coverage: string | null;
  notes: string | null;
}
