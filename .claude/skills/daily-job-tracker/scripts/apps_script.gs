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
      default:
        return json_(Object.assign({ ok: true }, append_(main, mh, auto, ah, body.rows || [])));
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

  jobs.forEach((j, i) => {
    const fresh = {
      '順序': ++seq,
      '狀態': defaultStatus_(),
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
