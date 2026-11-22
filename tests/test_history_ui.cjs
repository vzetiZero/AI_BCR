// Node test: tab Lịch sử lưu bền vững + xuất CSV/JSON/TXT + nút reset.
//
// Luật người dùng yêu cầu:
//  - Lịch sử lưu từ bây giờ về sau, không mất khi mở lại app.
//  - Có nút reset; không reset thì phải xuất được file để đối chiếu.
//  - Xuất được txt hoặc json hoặc csv.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'app.js'), 'utf8');
const main = fs.readFileSync(path.join(ROOT, 'ui', 'main.js'), 'utf8');

test('the history tab offers all three export formats', () => {
  assert.match(html, /id="ba-export-format"/);
  for (const value of ['csv', 'json', 'txt']) {
    assert.match(html, new RegExp(`<option value="${value}"`), `missing ${value}`);
  }
});

test('the history tab has a reset button separate from session reset', () => {
  assert.match(html, /id="ba-history-reset"/);
  assert.match(html, /Reset lịch sử/);
});

test('the history tab carries a cumulative stats strip and file hint', () => {
  assert.match(html, /id="ba-history-stats"/);
  assert.match(html, /id="ba-history-file"/);
  assert.match(html, /aibcr_history\.jsonl/);
});

test('export calls the engine, not the visible 200-row buffer', () => {
  const handler = app.slice(app.indexOf("$('#ba-export')"));
  const body = handler.slice(0, handler.indexOf("$('#ba-history-reset')"));
  assert.match(body, /call\('baccarat', 'history\.export'/);
  assert.match(body, /format/);
  assert.match(body, /api\.saveText\(reply\.name, reply\.text\)/);
  assert.doesNotMatch(body, /snap\.history/);
});

test('reset asks for confirmation and reports the archive', () => {
  const handler = app.slice(app.indexOf("$('#ba-history-reset')"));
  const body = handler.slice(0, handler.indexOf('$(\'#ba-clear-log\')'));
  assert.match(body, /window\.confirm/);
  assert.match(body, /call\('baccarat', 'history\.reset'/);
  assert.match(body, /reply\.archive/);
});

test('reset refuses on an empty history instead of wiping blindly', () => {
  const handler = app.slice(app.indexOf("$('#ba-history-reset')"));
  const body = handler.slice(0, handler.indexOf('$(\'#ba-clear-log\')'));
  assert.match(body, /if \(!total\)/);
});

test('the table renders the cumulative rows and their stats', () => {
  assert.match(app, /function renderHistory\(history, money, stats\)/);
  assert.match(app, /snapshot\.history_stats/);
  assert.match(app, /Tổng tay/);
  assert.match(app, /Lãi\/lỗ tích luỹ/);
});

test('the save dialog adapts its filter to the exported extension', () => {
  assert.match(main, /path\.extname\(safeName\)/);
  assert.match(main, /csv: 'CSV', json: 'JSON', txt: 'Văn bản'/);
});
