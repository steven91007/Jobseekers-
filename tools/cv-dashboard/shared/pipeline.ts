// Pipeline page: rows of the Google Sheet tabs 每日職缺 / 興趣職缺 / 投遞表單 as the Python backend
// returns them (English keys, `row` = sheet row, `<key>_url` = HYPERLINK target of a link column).

export type JobType = "AI agent" | "ML learning" | "automation" | "";
export type Decision = "未確認" | "是" | "否";
export const DECISIONS: Decision[] = ["未確認", "是", "否"];

interface SheetRowBase {
  row: number;
  date: string;
  url: string;
  company: string;
  title: string;
  type: JobType | string;
  city: string;
  country: string;
  mode: string;
  /** 1–10, or "" when not scored yet. */
  score: number | "";
  reasons: string;
  note: string;
  submission_row: string;
  submission_row_url?: string;
  notion: string;
  notion_url?: string;
}

/** 每日職缺 */
export interface DailyRow extends SheetRowBase {
  decision: Decision | "";
  /** 待決定 | 已產 CV | 已投遞 | 略過 | 失敗 */
  state: string;
}

/** 興趣職缺; state "" = not processed by the daily run yet. */
export interface InterestRow extends SheetRowBase {
  state: string;
}

/** 投遞表單 */
export interface SubmissionRow {
  row: number;
  date: string;
  company: string;
  title: string;
  url: string;
  type: string;
  source: string;
  city: string;
  country: string;
  mode: string;
  ats: string | number;
  cv: string;
  cv_url?: string;
  cover_letter: string;
  cover_letter_url?: string;
  status: string;
  app_dir: string;
  notion: string;
  notion_url?: string;
  /** Dashboard application key resolved from app_dir (server side); null = no CV view to link. */
  jd: string | null;
  // 求職追蹤表 columns copied into 投遞表單 (absent on an older deployment)
  seq?: string;
  industry?: string;
  scale?: string;
  conditions?: string;
  /** "City, Country / Mode" */
  location?: string;
  /** 求職過程: dated lines such as "- 11/02 CV + cover letter 產生 (ATS 75.8)". */
  process?: string;
  /** 內推人: lines "name (title) — URL". */
  referrers?: string;
  /** 內推狀態, e.g. "⚠️等待回覆" / "✅同意" / "❌拒絕" / "". */
  referral_status?: string;
  /** 履歷版本 (+ link to the Notion 投遞紀錄 row). */
  resume_version?: string;
  resume_version_url?: string;
  referral_message?: string;
  /** Parsed 內推資料; the first entry is the recommended one. */
  referrals?: Referral[];
}

export type ReferralGroup = "team" | "manager" | "taiwanese";

export interface Referral {
  name: string;
  title?: string;
  /** LinkedIn profile. */
  url?: string;
  why?: string;
  group?: ReferralGroup | string;
  /** The long referral message. */
  full?: string;
  /** The connection-request note (≤ 280 chars). */
  note?: string;
}

export interface PipelineList<T> {
  rows: T[];
  /** The spreadsheet itself (from the config file; never the token). */
  sheetUrl: string | null;
}

export interface InterestAppendResult {
  added: { url: string; row: number }[];
  skipped: { url: string; reason: "duplicate" | "no url" | string; tab?: string }[];
}
