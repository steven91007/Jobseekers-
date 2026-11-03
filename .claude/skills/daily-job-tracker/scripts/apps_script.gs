// AI Job Tracker — Apps Script web app,綁在使用者的 Google Sheet 上。
// 每日職缺寫入「求職追蹤表_AI 投遞」分頁(欄位與「求職追蹤表」相同);去重時兩個分頁都比對。
// 原則:只在最後一筆資料下方新增列;使用者自己的列、格式、公式一律不動。
// 修改後要「部署 → 管理部署作業 → 編輯 → 新版本」才會生效。

const MAIN_GID = 2074323844;           // 求職追蹤表 分頁的 gid
const MAIN_NAME = '求職追蹤表';
const AUTO_NAME = '求職追蹤表_AI 投遞';
const END_DATE_HEADER = '結束追蹤日期';
const AUTO_TAG = '每日職缺追蹤';
const CLOSED_STATUS = /拒|不考慮|放棄|結束/;  // 狀態改成這些時自動填結束追蹤日期

function setup() {
  const main = mainSheet_();
  const h = headers_(main);
  if (!h.cols[END_DATE_HEADER]) {
    main.getRange(h.row, h.lastCol + 1).setValue(END_DATE_HEADER)
      .setFontWeight(main.getRange(h.row, h.lastCol).getFontWeight())
      .setBackground(main.getRange(h.row, h.lastCol).getBackground());
  }
  autoSheet_();
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('TOKEN')) props.setProperty('TOKEN', Utilities.getUuid());
  Logger.log('TOKEN = ' + props.getProperty('TOKEN'));
}

// 手動把「狀態」改成被拒絕等,自動填入今天為結束追蹤日期(已有日期則不覆蓋)
function onEdit(e) {
  const sh = e.range.getSheet();
  if (sh.getSheetId() !== MAIN_GID && sh.getName() !== AUTO_NAME) return;
  const h = headers_(sh);
  const statusCol = h.cols['狀態'], endCol = h.cols[END_DATE_HEADER];
  if (!statusCol || !endCol || e.range.getColumn() > statusCol || e.range.getLastColumn() < statusCol) return;
  for (let r = Math.max(e.range.getRow(), h.row + 1); r <= e.range.getLastRow(); r++) {
    const status = String(sh.getRange(r, statusCol).getValue());
    const endCell = sh.getRange(r, endCol);
    if (CLOSED_STATUS.test(status) && endCell.isBlank()) {
      endCell.setValue(new Date()).setNumberFormat('yyyy-mm-dd');
    }
  }
}

function doPost(e) {
  let body;
  try { body = JSON.parse(e.postData.contents); } catch (err) { return json_({ ok: false, error: 'bad json' }); }
  if (!body || body.token !== PropertiesService.getScriptProperties().getProperty('TOKEN')) {
    return json_({ ok: false, error: 'bad token' });
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const main = mainSheet_(), mh = headers_(main);
    for (const need of ['公司', 'Position', '狀態']) {
      if (!mh.cols[need]) return json_({ ok: false, error: 'header not found: ' + need });
    }
    const auto = autoSheet_(), ah = headers_(auto);

    switch (body.action) {
      case 'statuses':
        return json_({ ok: true, options: statusOptions_(main, mh), used: usedStatuses_(auto, ah), default: defaultStatus_() });
      case 'set_default_status':
        if (!body.status) return json_({ ok: false, error: 'missing status' });
        PropertiesService.getScriptProperties().setProperty('DEFAULT_STATUS', String(body.status));
        return json_({ ok: true, default: String(body.status) });
      case 'list':
        return json_({ ok: true, urls: Array.from(existingUrls_(main, mh)).concat(Array.from(existingUrls_(auto, ah))) });
      case 'migrate':
        return json_(Object.assign({ ok: true }, migrate_(main, mh, auto, body.exclude || [])));
      case 'fix_dropdown':
        return json_({ ok: true, fixed: fillDropdown_(auto, ah, ah.row + 1, lastDataRow_(auto, ah) - ah.row, main, mh) });
      case 'delete_rows':
        return json_({ ok: true, deleted: deleteAutoRows_(auto, ah, body.urls || []) });
      case 'set_status': {
        const r = setStatus_(main, mh, auto, ah, body.rows || []);
        r.submissions_updated = setSubmissionStatus_(main, mh, body.rows || []);
        // 只在投遞表單的列(每日流程產生的)不算 not_found,呼叫端才不會把它 append 到「求職追蹤表_AI 投遞」
        const inSub = new Set(r.submissions_updated.map(x => urlKey_(x.url)));
        r.not_found = r.not_found.filter(j => !(j.url && inSub.has(urlKey_(j.url))));
        return json_(Object.assign({ ok: true }, r));
      }
      case 'append':
      case undefined:
      case null:
      case '':
        return json_(Object.assign({ ok: true }, append_(main, mh, auto, ah, body.rows || [])));
      default: {
        const handler = PIPELINE_ACTIONS[body.action];
        if (!handler) return json_({ ok: false, error: 'unknown action: ' + body.action });
        return json_(Object.assign({ ok: true }, handler(body, { main: main, mh: mh, auto: auto, ah: ah })));
      }
    }
  } finally {
    lock.releaseLock();
  }
}

// ---- append ----------------------------------------------------------------

function append_(main, mh, auto, ah, input) {
  const seen = new Set();
  [[main, mh], [auto, ah]].forEach(([sh, h]) => {
    existingUrls_(sh, h).forEach(u => seen.add(urlKey_(u)));
    existingPairs_(sh, h).forEach(k => seen.add(k));
  });
  const jobs = input.filter(j => {
    const u = String(j.url || '').trim();
    if (!u) return false;
    const keys = [urlKey_(u), pairKey_(j.company, j.title)];
    if (keys.some(k => seen.has(k))) return false;
    keys.forEach(k => seen.add(k));
    return true;
  });
  if (!jobs.length) return { added: 0, skipped: input.length };

  const lastData = lastDataRow_(auto, ah);
  const width = Math.max(ah.lastCol, auto.getLastColumn());
  const start = lastData + 1;
  const need = start + jobs.length - 1 - auto.getMaxRows();
  if (need > 0) auto.insertRowsAfter(auto.getMaxRows(), need);

  const block = auto.getRange(start, 1, jobs.length, width);
  const values = block.getValues();
  const formulas = block.getFormulas();
  let seq = maxSeq_(auto, ah, lastData);
  const allowed = new Set(statusOptions_(main, mh));

  jobs.forEach((j, i) => {
    const fresh = {
      '順序': ++seq,
      '狀態': j.status && (!allowed.size || allowed.has(String(j.status))) ? String(j.status) : defaultStatus_(),
      '公司': j.company,
      'Position': j.title,
      '應徵條件': [
        j.tech && '技術:' + j.tech,
        j.lang && '語言:' + j.lang,
        j.visa && '簽證:' + j.visa,
        j.salary && '薪資:' + j.salary,
        j.mode && '工作模式:' + j.mode,
      ].filter(Boolean).join('\n'),
      '地點': [j.city, j.country].filter(Boolean).join(', '),
      '求職過程': [
        (j.added || '') + ' 新增(' + AUTO_TAG + ')',
        j.posted && '刊登 ' + j.posted,
        j.category && '類別 ' + j.category,
        j.priority && 'Priority ' + j.priority,
      ].filter(Boolean).join(' · '),
    };
    Object.keys(fresh).forEach(name => {
      const c = ah.cols[name];
      if (!c) return;
      if (values[i][c - 1] === '' || values[i][c - 1] === null) values[i][c - 1] = text_(fresh[name]);
    });
    formulas[i].forEach((f, c) => { if (f) values[i][c] = f; });  // 保留預填的公式
  });
  block.setValues(values);

  const rich = jobs.map(j => [SpreadsheetApp.newRichTextValue().setText(String(j.title || j.url)).setLinkUrl(j.url).build()]);
  auto.getRange(start, ah.cols['Position'], jobs.length, 1).setRichTextValues(rich);
  fillDropdown_(auto, ah, start, jobs.length, main, mh);
  return { added: jobs.length, skipped: input.length - jobs.length };
}

// ---- set_status: 已存在的列改狀態並在「求職過程」加一筆;找不到的回傳給呼叫端去 append ----

function setStatus_(main, mh, auto, ah, input) {
  const allowed = new Set(statusOptions_(main, mh));
  const index = [];  // [sheet, headers, row, keys]
  [[auto, ah], [main, mh]].forEach(([sh, h]) => {
    const n = lastDataRow_(sh, h) - h.row;
    if (n < 1) return;
    const pos = sh.getRange(h.row + 1, h.cols['Position'], n, 1).getRichTextValues();
    const co = sh.getRange(h.row + 1, h.cols['公司'], n, 1).getDisplayValues();
    for (let i = 0; i < n; i++) {
      const link = pos[i][0] && pos[i][0].getLinkUrl();
      const keys = [pairKey_(co[i][0], pos[i][0] ? pos[i][0].getText() : '')];
      if (link) keys.push(urlKey_(link));
      index.push({ sh: sh, h: h, row: h.row + 1 + i, keys: keys });
    }
  });
  const updated = [], notFound = [], rejected = [];
  input.forEach(j => {
    const status = String(j.status || '');
    if (!status || (allowed.size && !allowed.has(status))) { rejected.push({ url: j.url, status: status }); return; }
    const want = [j.url && urlKey_(j.url), pairKey_(j.company, j.title)].filter(k => k && k !== 'ct:|');
    const hit = index.find(x => x.keys.some(k => want.includes(k)));
    if (!hit) { notFound.push(j); return; }
    hit.sh.getRange(hit.row, hit.h.cols['狀態']).setValue(status);
    const pc = hit.h.cols['求職過程'];
    if (pc) {
      const cell = hit.sh.getRange(hit.row, pc);
      const prev = String(cell.getDisplayValue() || '');
      const line = (j.added || Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd')) + ' ' + status + (j.note ? '(' + j.note + ')' : '');
      cell.setValue(text_(prev ? prev + '\n' + line : line));
    }
    updated.push({ url: j.url, sheet: hit.sh.getName(), row: hit.row });
  });
  return { updated: updated, not_found: notFound, rejected: rejected };
}

// ---- one-time migration: 把先前自動加進「求職追蹤表」的列搬到新分頁 ----------------

function migrate_(main, mh, auto, exclude) {
  const drop = new Set(exclude.map(urlKey_));
  const lastData = lastDataRow_(main, mh);
  const n = lastData - mh.row;
  if (n < 1) return { moved: 0, deleted: 0 };
  const proc = main.getRange(mh.row + 1, mh.cols['求職過程'], n, 1).getDisplayValues();
  const pos = main.getRange(mh.row + 1, mh.cols['Position'], n, 1).getRichTextValues();
  const width = Math.max(mh.lastCol, main.getLastColumn());
  const ah = headers_(auto);
  let dest = lastDataRow_(auto, ah) + 1;
  const rows = [];
  for (let i = 0; i < n; i++) {
    if (String(proc[i][0]).includes(AUTO_TAG)) {
      const link = pos[i][0] && pos[i][0].getLinkUrl();
      rows.push({ row: mh.row + 1 + i, drop: !!link && drop.has(urlKey_(link)) });
    }
  }
  let moved = 0, deleted = 0;
  rows.forEach(r => {
    if (r.drop) return;
    if (dest > auto.getMaxRows()) auto.insertRowsAfter(auto.getMaxRows(), 1);
    main.getRange(r.row, 1, 1, width).copyTo(auto.getRange(dest, 1));  // 連同格式、連結、下拉規則
    dest++;
    moved++;
  });
  rows.slice().reverse().forEach(r => { main.deleteRow(r.row); if (r.drop) deleted++; });  // 由下往上刪
  const dropdowns = fillDropdown_(auto, ah, ah.row + 1, lastDataRow_(auto, ah) - ah.row, main, mh);
  return { moved: moved, deleted: deleted, dropdowns_fixed: dropdowns };
}

function deleteAutoRows_(sh, h, urls) {
  const want = new Set(urls.map(urlKey_));
  const n = lastDataRow_(sh, h) - h.row;
  if (!want.size || n < 1) return [];
  const pos = sh.getRange(h.row + 1, h.cols['Position'], n, 1).getRichTextValues();
  const hit = [];
  for (let i = 0; i < n; i++) {
    const link = pos[i][0] && pos[i][0].getLinkUrl();
    if (link && want.has(urlKey_(link))) hit.push({ row: h.row + 1 + i, title: pos[i][0].getText() });
  }
  hit.slice().reverse().forEach(x => sh.deleteRow(x.row));
  return hit.map(x => x.title);
}

// ---- sheets & headers ------------------------------------------------------

function mainSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheets().find(s => s.getSheetId() === MAIN_GID) || ss.getSheetByName(MAIN_NAME);
  if (!sh) throw new Error('找不到分頁:' + MAIN_NAME);
  return sh;
}

// 新分頁不存在就建立:複製「求職追蹤表」的標題列(含格式)與欄寬
function autoSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(AUTO_NAME);
  if (sh) return sh;
  const main = mainSheet_(), mh = headers_(main);
  sh = ss.insertSheet(AUTO_NAME, main.getIndex());
  const width = Math.max(mh.lastCol, main.getLastColumn());
  main.getRange(mh.row, 1, 1, width).copyTo(sh.getRange(1, 1));
  for (let c = 1; c <= width; c++) sh.setColumnWidth(c, main.getColumnWidth(c));
  sh.setFrozenRows(1);
  return sh;
}

// 在前 10 列找同時含「公司」與「Position」的標題列
function headers_(sh) {
  const top = sh.getRange(1, 1, Math.min(10, sh.getMaxRows()), sh.getMaxColumns()).getDisplayValues();
  for (let r = 0; r < top.length; r++) {
    const row = top[r].map(v => String(v).trim());
    if (row.includes('公司') && row.includes('Position')) {
      const cols = {};
      let lastCol = 0;
      row.forEach((v, i) => { if (v) { if (!cols[v]) cols[v] = i + 1; lastCol = i + 1; } });
      return { row: r + 1, cols: cols, lastCol: lastCol };
    }
  }
  throw new Error('找不到含「公司」與「Position」的標題列:' + sh.getName());
}

// 最後一筆有公司或 Position 的列(忽略只預填了順序或格式的空列)
function lastDataRow_(sh, h) {
  const n = sh.getLastRow() - h.row;
  if (n < 1) return h.row;
  const c1 = sh.getRange(h.row + 1, h.cols['公司'], n, 1).getDisplayValues();
  const c2 = sh.getRange(h.row + 1, h.cols['Position'], n, 1).getDisplayValues();
  for (let i = n - 1; i >= 0; i--) {
    if (String(c1[i][0]).trim() || String(c2[i][0]).trim()) return h.row + 1 + i;
  }
  return h.row;
}

// ---- dedupe ----------------------------------------------------------------

function existingUrls_(sh, h) {
  const urls = new Set();
  const n = lastDataRow_(sh, h) - h.row;
  if (n < 1) return urls;
  const rng = sh.getRange(h.row + 1, 1, n, Math.max(h.lastCol, sh.getLastColumn()));
  rng.getRichTextValues().forEach(row => row.forEach(rt => {
    if (!rt) return;
    rt.getRuns().forEach(run => { const u = run.getLinkUrl(); if (u) urls.add(u.trim()); });
  }));
  rng.getDisplayValues().forEach(row => row.forEach(v => {
    (String(v).match(/https?:\/\/[^\s,;)]+/g) || []).forEach(u => urls.add(u.trim()));
  }));
  return urls;
}

function existingPairs_(sh, h) {
  const n = lastDataRow_(sh, h) - h.row;
  if (n < 1) return [];
  const co = sh.getRange(h.row + 1, h.cols['公司'], n, 1).getDisplayValues();
  const ti = sh.getRange(h.row + 1, h.cols['Position'], n, 1).getDisplayValues();
  return co.map((r, i) => pairKey_(r[0], ti[i][0])).filter(k => k !== 'ct:|');
}

function urlKey_(u) {
  const s = String(u).trim();
  const li = s.match(/linkedin\.com\/jobs\/view\/(?:[^\/?#]*-)?(\d+)/) || s.match(/linkedin\.com\/jobs\/.*[?&]currentJobId=(\d+)/);
  if (li) return 'li:' + li[1];
  return s.split('#')[0].split('?')[0].replace(/^https?:\/\/(www\.)?/i, '').replace(/\/+$/, '').toLowerCase();
}

function pairKey_(company, title) {
  const norm = v => String(v || '').toLowerCase().replace(/\(.*?\)/g, '').replace(/[^a-z0-9一-鿿]+/g, '');
  return 'ct:' + norm(company) + '|' + norm(title);
}

// ---- status ----------------------------------------------------------------

function defaultStatus_() {
  return PropertiesService.getScriptProperties().getProperty('DEFAULT_STATUS') || '準備中';
}

function statusRule_(sh, h) {
  const n = Math.max(sh.getLastRow() - h.row, 1);
  const found = sh.getRange(h.row + 1, h.cols['狀態'], n, 1).getDataValidations().find(([r]) => r &&
    (r.getCriteriaType() === SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST ||
     r.getCriteriaType() === SpreadsheetApp.DataValidationCriteria.VALUE_IN_RANGE));
  return found ? found[0] : null;
}

function statusOptions_(sh, h) {
  const rule = statusRule_(sh, h);
  if (!rule) return [];
  const vals = rule.getCriteriaValues();
  if (rule.getCriteriaType() === SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST) return vals[0].map(String);
  return vals[0].getDisplayValues().flat().map(String).filter(Boolean);
}

function usedStatuses_(sh, h) {
  const n = lastDataRow_(sh, h) - h.row;
  if (n < 1) return {};
  const count = {};
  sh.getRange(h.row + 1, h.cols['狀態'], n, 1).getDisplayValues().forEach(([v]) => { if (v) count[v] = (count[v] || 0) + 1; });
  return count;
}

// 把「求職追蹤表」狀態欄的下拉規則複製到 sh 的 [row, row+n) 中沒有規則的格子
function fillDropdown_(sh, h, row, n, src, srcH) {
  if (n < 1) return 0;
  const rule = statusRule_(src, srcH);
  if (!rule) return 0;
  const target = sh.getRange(row, h.cols['狀態'], n, 1);
  let fixed = 0;
  const next = target.getDataValidations().map(([r]) => { if (r) return [r]; fixed++; return [rule]; });
  if (fixed) target.setDataValidations(next);
  return fixed;
}

function maxSeq_(sh, h, lastData) {
  const c = h.cols['順序'];
  const n = lastData - h.row;
  if (!c || n < 1) return 0;
  return sh.getRange(h.row + 1, c, n, 1).getValues()
    .map(r => Number(r[0])).filter(v => isFinite(v)).reduce((a, b) => Math.max(a, b), 0);
}

// 避免以 = + - @ 開頭的文字被當成公式
function text_(v) {
  const s = v === undefined || v === null ? '' : String(v);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// ============================================================================
// 每日流程(daily pipeline):每日職缺 · 興趣職缺 · 投遞表單 + 兩個「依日期」檢視分頁
// 這些分頁的第 1 列是標題列;欄位以標題名稱定位,使用者可以調整欄位順序或加欄。
// ============================================================================

const PIPELINE_VERSION = 'pipeline-1';
const PIPE_NAME = '每日職缺';
const INTEREST_NAME = '興趣職缺';
const SUBMIT_NAME = '投遞表單';
const TZ = 'Asia/Taipei';

const TYPE_OPTIONS = ['AI agent', 'ML learning', 'automation'];
const MODE_OPTIONS = ['Hybrid', 'Remote', 'On-site', '未註明'];
const DECISION_OPTIONS = ['未確認', '是', '否'];
const PIPE_STATE_OPTIONS = ['待決定', '已產 CV', '已投遞', '略過', '失敗'];
const INTEREST_STATE_OPTIONS = ['待產 CV', '已產 CV', '已投遞', '略過', '失敗'];
const SOURCE_OPTIONS = ['AI 找的', '興趣'];

// [key, 標題, 欄寬, 選項]
const PIPE_COLS = [
  ['date', '日期', 95], ['company', '公司', 150], ['title', '職位', 240], ['url', '連結', 220],
  ['type', '類型', 105, TYPE_OPTIONS], ['city', '城市', 100], ['country', '國家', 80],
  ['mode', '工作型態', 90, MODE_OPTIONS], ['score', '評分', 55, 'score'], ['reasons', '評分理由', 320],
  ['decision', '決定', 75, DECISION_OPTIONS], ['note', '備註（人工）', 220],
  ['state', '處理狀態', 90, PIPE_STATE_OPTIONS], ['submission_row', '投遞表單列', 90], ['notion', 'Notion', 80],
];
const INTEREST_COLS = [
  ['date', '日期', 95], ['url', '連結', 240], ['note', '備註（人工）', 220], ['company', '公司', 150],
  ['title', '職位', 240], ['type', '類型', 105, TYPE_OPTIONS], ['city', '城市', 100], ['country', '國家', 80],
  ['mode', '工作型態', 90, MODE_OPTIONS], ['score', '評分', 55, 'score'], ['reasons', '評分理由', 320],
  ['state', '處理狀態', 90, INTEREST_STATE_OPTIONS], ['submission_row', '投遞表單列', 90], ['notion', 'Notion', 80],
];
// 投遞表單 = 「求職追蹤表」的標題列原樣複製(使用者自己的欄位),後面接管線專用欄位
const SUBMIT_EXTRA = [
  ['date', '日期', 95], ['url', '連結', 220], ['type', '類型', 105, TYPE_OPTIONS], ['source', '來源', 80, SOURCE_OPTIONS],
  ['city', '城市', 100], ['country', '國家', 80], ['mode', '工作型態', 90, MODE_OPTIONS], ['ats', 'ATS', 60], ['app_dir', '應用資料夾', 260], ['notion', 'Notion', 80], ['cv', 'CV 連結', 110],
  ['cover_letter', 'Cover letter 連結', 110], ['referral_message', '內推訊息', 320], ['referral_data', '內推資料', 120],
];
const REFERRAL_SENT = '⚠️等待回覆';
const TABS = {};
TABS[PIPE_NAME] = { cols: PIPE_COLS, byDate: PIPE_NAME + '_依日期' };
TABS[INTEREST_NAME] = { cols: INTEREST_COLS, byDate: INTEREST_NAME + '_依日期' };
const LINK_KEYS = { submission_row: 1, notion: 1, cv: 1, cover_letter: 1, resume_version: 1 };  // 寫成 HYPERLINK 的欄位

const PIPELINE_ACTIONS = {
  version: () => ({ version: PIPELINE_VERSION }),
  setup_pipeline: (b, ctx) => setupPipeline_(ctx),
  pipeline_append: (b, ctx) => pipelineAppend_(b.rows || [], ctx),
  pipeline_list: (b) => pipelineList_(!!b.all),
  pipeline_update: (b, ctx) => updateByUrl_(PIPE_NAME, b.rows || [], ctx),
  pipeline_backfill: (b, ctx) => pipelineBackfill_(b.status || '準備中', ctx),
  interest_list: (b) => interestList_(!!b.all),
  interest_append: (b, ctx) => interestAppend_(b.rows || [], ctx),
  interest_update: (b, ctx) => updateByUrl_(INTEREST_NAME, b.rows || [], ctx),
  submissions_list: (b, ctx) => ({ rows: readSubmissions_(submitSheet_(ctx)).rows }),
  submissions_append: (b, ctx) => submissionsAppend_(b.rows || [], ctx),
  submissions_update: (b, ctx) => submissionsUpdate_(b.rows || [], ctx),
  referral_sent: (b, ctx) => referralSent_(b.rows || [], ctx),
};

// ---- setup -----------------------------------------------------------------

function setupPipeline_(ctx) {
  const done = [];
  Object.keys(TABS).forEach(name => {
    const sh = tab_(name, ctx);
    formatTab_(sh, TABS[name].cols, ctx);
    done.push(name);
    if (TABS[name].byDate) { byDateTab_(TABS[name].byDate, name, TABS[name].cols.length); done.push(TABS[name].byDate); }
  });
  submitSheet_(ctx);
  done.push(SUBMIT_NAME);
  return { tabs: done, version: PIPELINE_VERSION };
}

// 取得分頁;不存在就建立(放在最後)並寫標題列。既有分頁缺的標題補在最右邊。
function tab_(name, ctx) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const cols = TABS[name].cols;
  let sh = ss.getSheetByName(name);
  const created = !sh;
  if (!sh) sh = ss.insertSheet(name, ss.getSheets().length);
  const lastCol = Math.max(sh.getLastColumn(), 1);
  const head = sh.getRange(1, 1, 1, lastCol).getDisplayValues()[0].map(v => String(v).trim());
  if (!head.some(Boolean)) {
    sh.getRange(1, 1, 1, cols.length).setValues([cols.map(c => c[1])]);
  } else {
    const missing = cols.filter(c => head.indexOf(c[1]) < 0).map(c => c[1]);
    if (missing.length) sh.getRange(1, head.filter(Boolean).length + 1, 1, missing.length).setValues([missing]);
  }
  if (created) formatTab_(sh, cols, ctx);
  return sh;
}

function formatTab_(sh, cols, ctx) {
  const h = tabHeaders_(sh);
  sh.getRange(1, 1, 1, h.lastCol).setFontWeight('bold').setBackground('#e8eaed');
  sh.setFrozenRows(1);
  const n = Math.max(sh.getMaxRows() - 1, 1);
  cols.forEach(([key, title, width, opts]) => {
    const c = h.cols[title];
    if (!c) return;
    sh.setColumnWidth(c, width);
    const body = sh.getRange(2, c, n, 1);
    if (key === 'date') body.setNumberFormat('yyyy-mm-dd');
    if (key === 'reasons' || key === 'note') body.setWrap(true);
    let rule = null;
    if (Array.isArray(opts)) rule = SpreadsheetApp.newDataValidation().requireValueInList(opts, true).setAllowInvalid(false).build();
    else if (opts === 'score') rule = SpreadsheetApp.newDataValidation().requireNumberBetween(1, 10).setAllowInvalid(false).build();
    else if (opts === 'status' && ctx) rule = statusRule_(ctx.main, ctx.mh);
    if (rule) body.setDataValidation(rule);
  });
}

// 「依日期」分頁:B1 選日期,下面是來源分頁中該日期的列
function byDateTab_(name, src, width) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name, ss.getSheets().length);
  const last = colLetter_(width);
  const q = "'" + src + "'!";
  sh.getRange(1, 1).setValue('日期').setFontWeight('bold');
  const pick = sh.getRange(1, 2);
  pick.setDataValidation(SpreadsheetApp.newDataValidation().requireDate().setAllowInvalid(false).build()).setNumberFormat('yyyy-mm-dd');
  if (pick.isBlank()) pick.setValue(today_());
  sh.getRange(1, 3).setValue('← 點兩下選日期');
  sh.getRange(3, 1).setFormula('={' + q + 'A1:' + last + '1}');
  sh.getRange(3, 1, 1, width).setFontWeight('bold').setBackground('#e8eaed');
  sh.getRange(4, 1).setFormula('=IFERROR(FILTER(' + q + 'A2:' + last + ', IFERROR(INT(' + q + 'A2:A)=$B$1, FALSE)), "這天沒有資料")');
  sh.setFrozenRows(3);
  return sh;
}

function colLetter_(n) {
  let s = '';
  for (; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + (n - 1) % 26) + s;
  return s;
}

// ---- table I/O -------------------------------------------------------------

function tabHeaders_(sh) {
  const lastCol = Math.max(sh.getLastColumn(), 1);
  const cols = {};
  let last = 0;
  sh.getRange(1, 1, 1, lastCol).getDisplayValues()[0].forEach((v, i) => {
    const t = String(v).trim();
    if (t) { if (!cols[t]) cols[t] = i + 1; last = i + 1; }
  });
  return { cols: cols, lastCol: last };
}

function colsOf_(sh) {
  return TABS[sh.getName()].cols;
}

// 回傳 {h, rows:[{row, key: 顯示值, key_url: 超連結}]};以「連結」欄判斷資料列
function readTab_(sh) {
  const h = tabHeaders_(sh);
  const n = sh.getLastRow() - 1;
  if (n < 1) return { h: h, rows: [] };
  const rng = sh.getRange(2, 1, n, h.lastCol);
  const values = rng.getValues(), display = rng.getDisplayValues(), formulas = rng.getFormulas();
  const rows = [];
  for (let i = 0; i < n; i++) {
    const r = { row: i + 2 };
    let any = false;
    colsOf_(sh).forEach(([key, title]) => {
      const c = h.cols[title];
      if (!c) return;
      const v = values[i][c - 1];
      r[key] = v instanceof Date ? Utilities.formatDate(v, TZ, 'yyyy-MM-dd') : display[i][c - 1];
      if (key === 'score') r[key] = v === '' || v === null ? '' : Number(v);
      const f = formulas[i][c - 1];
      if (f && LINK_KEYS[key]) { const m = f.match(/HYPERLINK\("([^"]*)"/i); if (m) r[key + '_url'] = m[1]; }
      if (r[key] !== '' && r[key] !== null) any = true;
    });
    if (any && r.url) rows.push(r);
  }
  return { h: h, rows: rows };
}

function lastTabRow_(sh) {
  const h = tabHeaders_(sh);
  const n = sh.getLastRow() - 1;
  if (n < 1) return 1;
  const urls = sh.getRange(2, h.cols['連結'], n, 1).getDisplayValues();
  for (let i = n - 1; i >= 0; i--) if (String(urls[i][0]).trim()) return i + 2;
  return 1;
}

// 一個欄位值 → 要寫進格子的內容(日期轉 Date、連結轉 HYPERLINK、文字防公式)
function cellValue_(sh, key, v, label) {
  if (v === undefined || v === null || v === '') return '';
  if (key === 'date') return toDate_(v);
  if (key === 'score') { const x = Math.round(Number(v)); return isFinite(x) ? Math.min(10, Math.max(1, x)) : ''; }
  if (key === 'mode') return normMode_(v);
  if (key === 'reasons' && Array.isArray(v)) return text_(v.map(s => '• ' + s).join('\n'));
  if (LINK_KEYS[key]) {
    if (key === 'submission_row') {
      const sub = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SUBMIT_NAME);
      const row = Number(v);
      if (sub && isFinite(row)) return '=HYPERLINK("#gid=' + sub.getSheetId() + '&range=A' + row + '","第 ' + row + ' 列")';
      return text_(v);
    }
    const s = String(v);
    if (/^https?:\/\//i.test(s)) return '=HYPERLINK("' + s.replace(/"/g, '%22') + '","' + String(label || linkLabel_(key)).replace(/"/g, "'") + '")';
    return text_(s);
  }
  return text_(v);
}

function linkLabel_(key) {
  return { notion: 'Notion', cv: 'CV PDF', cover_letter: 'Cover letter PDF' }[key] || '連結';
}

function toDate_(v) {
  if (v instanceof Date) return v;
  const m = String(v).match(/(\d{4})-(\d{1,2})-(\d{1,2})/);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : text_(v);
}

function today_() {
  return toDate_(Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd'));
}

function normMode_(v) {
  const s = String(v || '').toLowerCase();
  if (/hybrid|混合/.test(s)) return 'Hybrid';
  if (/remote|遠端/.test(s)) return 'Remote';
  if (/on-?site|office|辦公室|現場|進辦公室/.test(s)) return 'On-site';
  return '未註明';
}

function allowed_(key, sh) {
  const def = colsOf_(sh).find(c => c[0] === key);
  return def && Array.isArray(def[3]) ? def[3] : null;
}

// 在最後一筆資料下方寫入多列(只寫有定義的欄位)
function appendRows_(sh, objs) {
  if (!objs.length) return [];
  const h = tabHeaders_(sh);
  const start = lastTabRow_(sh) + 1;
  const need = start + objs.length - 1 - sh.getMaxRows();
  if (need > 0) sh.insertRowsAfter(sh.getMaxRows(), need);
  const out = [];
  objs.forEach((o, i) => {
    const row = start + i;
    colsOf_(sh).forEach(([key, title]) => {
      const c = h.cols[title];
      if (!c || !(key in o)) return;
      const v = cellValue_(sh, key, o[key], o[key + '_label']);
      if (v === '') return;
      const cell = sh.getRange(row, c);
      if (typeof v === 'string' && v.charAt(0) === '=') cell.setFormula(v); else cell.setValue(v);
    });
    out.push({ url: o.url, row: row });
  });
  return out;
}

// ---- dedupe across every tab -----------------------------------------------

function allKeys_(ctx, tabs) {
  const seen = {};
  const add = (k, where) => { if (k && k !== 'ct:|' && !seen[k]) seen[k] = where; };
  if (ctx) [[ctx.main, ctx.mh], [ctx.auto, ctx.ah]].forEach(([sh, h]) => {
    existingUrls_(sh, h).forEach(u => add(urlKey_(u), sh.getName()));
    existingPairs_(sh, h).forEach(k => add(k, sh.getName()));
  });
  tabs.forEach(name => {
    const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
    if (!sh) return;
    if (name === SUBMIT_NAME) {
      readSubmissions_(sh).rows.forEach(r => { add(urlKey_(r.url), name); add(pairKey_(r.company, r.title), name); });
      return;
    }
    readTab_(sh).rows.forEach(r => {
      add(urlKey_(r.url), name);
      if (r.company && r.title) add(pairKey_(r.company, r.title), name);
    });
  });
  return seen;
}

function pipelineAppend_(input, ctx) {
  const sh = tab_(PIPE_NAME, ctx);
  const seen = allKeys_(ctx, [PIPE_NAME, INTEREST_NAME, SUBMIT_NAME]);
  const fresh = [], skipped = [];
  input.forEach(j => {
    const u = String(j.url || '').trim();
    if (!/^https?:\/\//i.test(u)) { skipped.push({ url: u, reason: 'no url' }); return; }
    const keys = [urlKey_(u), j.company && j.title ? pairKey_(j.company, j.title) : ''];
    const hit = keys.find(k => k && seen[k]);
    if (hit) { skipped.push({ url: u, reason: 'duplicate', tab: seen[hit] }); return; }
    keys.forEach(k => { if (k) seen[k] = PIPE_NAME; });
    fresh.push(Object.assign({}, j, {
      url: u,
      date: j.date || today_(),
      decision: DECISION_OPTIONS.indexOf(j.decision) >= 0 ? j.decision : '未確認',
      state: PIPE_STATE_OPTIONS.indexOf(j.state) >= 0 ? j.state : '待決定',
      type: TYPE_OPTIONS.indexOf(j.type) >= 0 ? j.type : '',
    }));
  });
  const added = appendRows_(sh, fresh);
  return { added: added, skipped: skipped };
}

function pipelineList_(all) {
  const rows = readTab_(tab_(PIPE_NAME)).rows;
  const out = {
    queue: rows.filter(r => r.decision === '是' && r.state === '待決定'),
    undecided: rows.filter(r => !r.decision || r.decision === '未確認'),
  };
  if (all) out.rows = rows;
  return out;
}

function interestList_(all) {
  const rows = readTab_(tab_(INTEREST_NAME)).rows;
  const out = { rows: all ? rows : rows.filter(r => !r.state) };
  out.pending_cv = rows.filter(r => r.state === '待產 CV');
  return out;
}

// 貼連結:只跟「興趣職缺」與「投遞表單」去重(已在每日職缺的職缺也可以貼,代表想做)
function interestAppend_(input, ctx) {
  const sh = tab_(INTEREST_NAME, ctx);
  const seen = allKeys_(null, [INTEREST_NAME, SUBMIT_NAME]);
  const fresh = [], skipped = [];
  input.forEach(j => {
    const u = String(j.url || '').trim();
    if (!/^https?:\/\//i.test(u)) { skipped.push({ url: u, reason: 'no url' }); return; }
    const k = urlKey_(u);
    if (seen[k]) { skipped.push({ url: u, reason: 'duplicate', tab: seen[k] }); return; }
    seen[k] = INTEREST_NAME;
    fresh.push({ url: u, note: j.note || '', date: j.date || today_() });
  });
  return { added: appendRows_(sh, fresh), skipped: skipped };
}

// 依連結更新指定欄位;只改有送的欄位,選單欄位不在選項內就拒絕
function updateByUrl_(name, input, ctx) {
  const sh = tab_(name, ctx);
  const t = readTab_(sh);
  const byKey = {};
  t.rows.forEach(r => { byKey[urlKey_(r.url)] = r.row; });
  const updated = [], notFound = [], rejected = [];
  input.forEach(j => {
    const row = j.url && byKey[urlKey_(j.url)];
    if (!row) { notFound.push(j.url || ''); return; }
    const bad = [];
    const writes = [];
    colsOf_(sh).forEach(([key, title]) => {
      if (key === 'url' || !(key in j) || !t.h.cols[title]) return;
      const opts = allowed_(key, sh);
      if (opts && j[key] !== '' && key !== 'mode' && opts.indexOf(j[key]) < 0) { bad.push(key + '=' + j[key]); return; }
      writes.push([t.h.cols[title], cellValue_(sh, key, j[key], j[key + '_label'])]);
    });
    if (bad.length) { rejected.push({ url: j.url, bad: bad }); return; }
    writes.forEach(([c, v]) => {
      const cell = sh.getRange(row, c);
      if (typeof v === 'string' && v.charAt(0) === '=') cell.setFormula(v); else cell.setValue(v);
    });
    updated.push({ url: j.url, row: row });
  });
  return { updated: updated, not_found: notFound, rejected: rejected };
}

// ---- 投遞表單(求職追蹤表 版面) -------------------------------------------------

// 不存在就建立:複製「求職追蹤表」標題列(含格式)與欄寬,再接上管線欄位;既有分頁補上缺的管線欄位
function submitSheet_(ctx) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const main = ctx ? ctx.main : mainSheet_(), mh = ctx ? ctx.mh : headers_(main);
  let sh = ss.getSheetByName(SUBMIT_NAME);
  if (!sh) {
    sh = ss.insertSheet(SUBMIT_NAME, ss.getSheets().length);
    const width = mh.lastCol;
    main.getRange(mh.row, 1, 1, width).copyTo(sh.getRange(1, 1));
    for (let c = 1; c <= width; c++) sh.setColumnWidth(c, main.getColumnWidth(c));
    sh.setFrozenRows(1);
  }
  let h = headers_(sh);
  const missing = SUBMIT_EXTRA.filter(x => !h.cols[x[1]]);
  if (missing.length) {
    sh.getRange(h.row, h.lastCol + 1, 1, missing.length).setValues([missing.map(x => x[1])])
      .setFontWeight('bold').setBackground('#e8eaed');
    h = headers_(sh);
    const n = Math.max(sh.getMaxRows() - h.row, 1);
    missing.forEach(([key, title, width, opts]) => {
      const c = h.cols[title];
      sh.setColumnWidth(c, width);
      if (Array.isArray(opts)) sh.getRange(h.row + 1, c, n, 1).setDataValidation(
        SpreadsheetApp.newDataValidation().requireValueInList(opts, true).setAllowInvalid(false).build());
      if (key === 'date') sh.getRange(h.row + 1, c, n, 1).setNumberFormat('yyyy-mm-dd');
    });
  }
  return sh;
}

const SUBMIT_MAIN_KEYS = { 'Industry': 'industry', '順序': 'seq', '狀態': 'status', '公司': 'company', '規模': 'scale',
  '應徵條件': 'conditions', '地點': 'location', '求職過程': 'process', '內推人': 'referrers', '內推狀態': 'referral_status',
  '面試紀錄': 'interview_notes', '面試反思': 'interview_reflection', '面試回饋': 'interview_feedback',
  '履歷版本': 'resume_version', '結束追蹤日期': 'end_date' };

function readSubmissions_(sh) {
  const h = headers_(sh);
  const n = lastDataRow_(sh, h) - h.row;
  if (n < 1) return { h: h, rows: [] };
  const width = h.lastCol;
  const rng = sh.getRange(h.row + 1, 1, n, width);
  const vals = rng.getValues(), disp = rng.getDisplayValues(), form = rng.getFormulas(), rich = rng.getRichTextValues();
  const extra = {};
  SUBMIT_EXTRA.forEach(x => { extra[x[1]] = x[0]; });
  const rows = [];
  for (let i = 0; i < n; i++) {
    const r = { row: h.row + 1 + i };
    Object.keys(h.cols).forEach(title => {
      const c = h.cols[title] - 1;
      const key = title === 'Position' ? 'title' : (SUBMIT_MAIN_KEYS[title] || extra[title]);
      if (!key) return;
      const v = vals[i][c];
      r[key] = v instanceof Date ? Utilities.formatDate(v, TZ, 'yyyy-MM-dd') : disp[i][c];
      const m = String(form[i][c] || '').match(/HYPERLINK\("([^"]*)"/i);
      if (m && LINK_KEYS[key]) r[key + '_url'] = m[1];
    });
    const pos = rich[i][h.cols['Position'] - 1];
    r.url = (pos && pos.getLinkUrl()) || r.url || '';
    const loc = String(r.location || '').split('/');
    const place = (loc[0] || '').split(',').map(x => x.trim()).filter(Boolean);
    if (!r.city) r.city = place.length > 1 ? place.slice(0, -1).join(', ') : '';
    if (!r.country) r.country = place.length ? place[place.length - 1] : '';
    if (!r.mode) r.mode = (loc[1] || '').trim();
    try { r.referrals = r.referral_data ? JSON.parse(r.referral_data) : []; } catch (e) { r.referrals = []; }
    delete r.referral_data;
    rows.push(r);
  }
  return { h: h, rows: rows };
}

function mmdd_(v) {
  const d = v ? toDate_(v) : today_();
  return Utilities.formatDate(d instanceof Date ? d : today_(), TZ, 'MM/dd');
}

function setCell_(sh, row, col, v) {
  if (!col || v === undefined || v === null) return;
  const cell = sh.getRange(row, col);
  if (typeof v === 'string' && v.charAt(0) === '=') cell.setFormula(v); else cell.setValue(v);
}

function resumeVersionCell_(j) {
  const label = j.resume_label || ['v3 cards', j.ats !== undefined && j.ats !== null && j.ats !== '' ? 'ATS ' + j.ats : '']
    .filter(Boolean).join(' · ') + (j.cover_letter || j.has_cover_letter ? ' + CL' : '');
  return j.notion && /^https?:\/\//.test(j.notion)
    ? '=HYPERLINK("' + j.notion.replace(/"/g, '%22') + '","' + label.replace(/"/g, "'") + '")' : text_(label);
}

// 同一列的管線欄位(日期、連結、CV 連結 …)
function writeExtras_(sh, h, row, j) {
  SUBMIT_EXTRA.forEach(([key, title]) => {
    if (!(key in j) || j[key] === '' || j[key] === undefined || j[key] === null) return;
    let v = j[key];
    if (key === 'date') v = toDate_(v);
    else if (key === 'mode') v = normMode_(v);
    else if (key === 'referral_data') v = text_(typeof v === 'string' ? v : JSON.stringify(v));
    else if ((key === 'cv' || key === 'cover_letter' || key === 'notion') && /^https?:\/\//.test(String(v)))
      v = '=HYPERLINK("' + String(v).replace(/"/g, '%22') + '","' + ({ cv: 'CV PDF', cover_letter: 'Cover letter PDF', notion: 'Notion' })[key] + '")';
    else v = text_(v);
    setCell_(sh, row, h.cols[title], v);
  });
}

function referrersText_(list) {
  return (list || []).map(p => p.name + (p.title ? ' (' + p.title + ')' : '') + ' — ' + p.url).join('\n');
}

function submissionsAppend_(input, ctx) {
  const sh = submitSheet_(ctx);
  const h = headers_(sh);
  const seen = {};
  readSubmissions_(sh).rows.forEach(r => { if (r.url) seen[urlKey_(r.url)] = r.row; });
  const allowed = statusOptions_(ctx.main, ctx.mh);
  const added = [], skipped = [];
  let row = lastDataRow_(sh, h);
  let seq = maxSeq_(sh, h, row);
  input.forEach(j => {
    const u = String(j.url || '').trim();
    if (!/^https?:\/\//i.test(u)) { skipped.push({ url: u, reason: 'no url' }); return; }
    if (seen[urlKey_(u)]) { skipped.push({ url: u, reason: 'duplicate', row: seen[urlKey_(u)] }); return; }
    row++;
    if (row > sh.getMaxRows()) sh.insertRowsAfter(sh.getMaxRows(), 1);
    seen[urlKey_(u)] = row;
    const status = j.status && (!allowed.length || allowed.indexOf(j.status) >= 0) ? j.status : defaultStatus_();
    const place = [[j.city, j.country].filter(Boolean).join(', '), j.mode ? normMode_(j.mode) : ''].filter(Boolean).join(' / ');
    const head = [j.type, j.score ? '評分 ' + j.score + '/10' : ''].filter(Boolean).join(' · ');
    const reqs = Array.isArray(j.requirements) ? j.requirements : (j.requirements ? [j.requirements] : []);
    const cond = [head].concat(reqs.map(r => '• ' + r)).filter(Boolean).join('\n');
    const proc = '- ' + mmdd_(j.date) + ' CV' + (j.cover_letter || j.has_cover_letter ? ' + cover letter' : '') + ' 產生' +
      (j.ats !== undefined && j.ats !== null && j.ats !== '' ? ' (ATS ' + j.ats + ')' : '');
    const c = h.cols;
    setCell_(sh, row, c['順序'], ++seq);
    setCell_(sh, row, c['狀態'], status);
    setCell_(sh, row, c['公司'], text_(j.company));
    sh.getRange(row, c['Position']).setRichTextValues([[SpreadsheetApp.newRichTextValue().setText(String(j.title || u)).setLinkUrl(u).build()]]);
    if (j.industry) setCell_(sh, row, c['Industry'], text_(j.industry));
    setCell_(sh, row, c['應徵條件'], text_(cond));
    setCell_(sh, row, c['地點'], text_(place));
    setCell_(sh, row, c['求職過程'], text_(proc));
    if (j.referrers && j.referrers.length) setCell_(sh, row, c['內推人'], text_(referrersText_(j.referrers)));
    setCell_(sh, row, c['履歷版本'], resumeVersionCell_(j));
    writeExtras_(sh, h, row, Object.assign({}, j, { url: u, date: j.date || today_() }));
    added.push({ url: u, row: row });
  });
  if (added.length) {
    fillDropdown_(sh, h, added[0].row, added[added.length - 1].row - added[0].row + 1, ctx.main, ctx.mh);
    copyRule_(ctx.main, ctx.mh, sh, h, '內推狀態', added[0].row, added[added.length - 1].row - added[0].row + 1);
  }
  return { added: added, skipped: skipped };
}

// 把 src 某欄的下拉規則複製到 sh 同名欄的 [row, row+n)(沒有規則的格子)
function copyRule_(src, srcH, sh, h, title, row, n) {
  const sc = srcH.cols[title], dc = h.cols[title];
  if (!sc || !dc || n < 1) return 0;
  const m = Math.max(src.getLastRow() - srcH.row, 1);
  const found = src.getRange(srcH.row + 1, sc, m, 1).getDataValidations().find(([r]) => r);
  if (!found) return 0;
  const target = sh.getRange(row, dc, n, 1);
  let fixed = 0;
  const next = target.getDataValidations().map(([r]) => { if (r) return [r]; fixed++; return [found[0]]; });
  if (fixed) target.setDataValidations(next);
  return fixed;
}

function findSubmission_(sh, url) {
  const k = urlKey_(url || '');
  return readSubmissions_(sh).rows.filter(r => r.url && urlKey_(r.url) === k);
}

function appendProcess_(sh, h, row, line) {
  const c = h.cols['求職過程'];
  if (!c) return;
  const cell = sh.getRange(row, c);
  const prev = String(cell.getDisplayValue() || '');
  cell.setValue(text_(prev ? prev + '\n' + line : line));
}

// 之後的步驟補資料:內推人 / 內推訊息 / CV 連結 / Notion / 履歷版本 …(只改有送的欄位)
function submissionsUpdate_(input, ctx) {
  const sh = submitSheet_(ctx), h = headers_(sh);
  const updated = [], notFound = [];
  input.forEach(j => {
    const hits = findSubmission_(sh, j.url);
    if (!hits.length) { notFound.push(j.url || ''); return; }
    hits.forEach(r => {
      if (j.referrers) setCell_(sh, r.row, h.cols['內推人'], text_(referrersText_(j.referrers)));
      if (j.notion || j.resume_label) setCell_(sh, r.row, h.cols['履歷版本'], resumeVersionCell_(Object.assign({ ats: r.ats }, j)));
      if (j.process_line) appendProcess_(sh, h, r.row, '- ' + mmdd_(j.date) + ' ' + j.process_line);
      writeExtras_(sh, h, r.row, j);
      updated.push({ url: j.url, row: r.row });
    });
  });
  return { updated: updated, not_found: notFound };
}

// 候選人在 dashboard 按「已送出」:內推狀態 = ⚠️等待回覆 + 求職過程加一行
function referralSent_(input, ctx) {
  const sh = submitSheet_(ctx), h = headers_(sh);
  const updated = [], notFound = [];
  input.forEach(j => {
    const hits = findSubmission_(sh, j.url);
    if (!hits.length || !j.name) { notFound.push(j.url || ''); return; }
    hits.forEach(r => {
      setCell_(sh, r.row, h.cols['內推狀態'], REFERRAL_SENT);
      appendProcess_(sh, h, r.row, '- ' + mmdd_(j.date) + ' Sent referral request to ' + j.name);
      updated.push({ url: j.url, row: r.row });
    });
  });
  return { updated: updated, not_found: notFound };
}

// set_status 的延伸:投遞表單中同連結(Position 的超連結)的列一起改「狀態」並在「求職過程」加一行
function setSubmissionStatus_(main, mh, input) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SUBMIT_NAME);
  if (!sh) return [];
  const h = headers_(sh);
  const allowed = statusOptions_(main, mh);
  const out = [];
  input.forEach(j => {
    const status = String(j.status || '');
    if (!j.url || !status || (allowed.length && allowed.indexOf(status) < 0)) return;
    findSubmission_(sh, j.url).forEach(r => {
      if (r.status === status) { out.push({ url: j.url, row: r.row, changed: false }); return; }
      setCell_(sh, r.row, h.cols['狀態'], status);
      const label = status === '履歷送出' ? 'Sent' : status;
      appendProcess_(sh, h, r.row, '- ' + mmdd_(j.added) + ' ' + label + ' (' + (j.note || 'CV dashboard') + ')');
      out.push({ url: j.url, row: r.row, changed: true });
    });
  });
  return out;
}

// ---- 一次性:把「求職追蹤表_AI 投遞」中狀態為「準備中」的列複製到「每日職缺」(不刪原列) ----

function pipelineBackfill_(status, ctx) {
  const sh = tab_(PIPE_NAME, ctx);
  const auto = ctx.auto, ah = ctx.ah;
  const n = lastDataRow_(auto, ah) - ah.row;
  if (n < 1) return { added: [], skipped: [] };
  const width = Math.max(ah.lastCol, auto.getLastColumn());
  const rng = auto.getRange(ah.row + 1, 1, n, width);
  const disp = rng.getDisplayValues(), rich = rng.getRichTextValues();
  const seen = {};
  readTab_(sh).rows.forEach(r => { seen[urlKey_(r.url)] = 1; });
  const col = name => ah.cols[name] ? ah.cols[name] - 1 : -1;
  const fresh = [], skipped = [];
  for (let i = 0; i < n; i++) {
    const row = disp[i];
    if (String(row[col('狀態')]).trim() !== status) continue;
    const pos = rich[i][col('Position')];
    const url = pos && pos.getLinkUrl ? pos.getLinkUrl() : '';
    const title = row[col('Position')];
    if (!url) { skipped.push({ title: title, reason: 'no url' }); continue; }
    if (seen[urlKey_(url)]) { skipped.push({ url: url, reason: 'duplicate' }); continue; }
    seen[urlKey_(url)] = 1;
    const place = String(row[col('地點')] || '').split(',').map(s => s.trim()).filter(Boolean);
    const cond = String(row[col('應徵條件')] || '');
    const modeM = cond.match(/工作模式[::]\s*([^\n]+)/);
    const dateM = String(row[col('求職過程')] || '').match(/\d{4}-\d{2}-\d{2}/);
    fresh.push({
      date: dateM ? dateM[0] : '', company: row[col('公司')], title: title, url: url,
      city: place.length > 1 ? place.slice(0, -1).join(', ') : '', country: place.length ? place[place.length - 1] : '',
      mode: modeM ? modeM[1] : '', decision: '未確認', state: '待決定',
    });
  }
  return { added: appendRows_(sh, fresh), skipped: skipped };
}
