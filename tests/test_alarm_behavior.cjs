// Chạy THẬT kpiStateOf + syncKpiAudio của renderer trong sandbox vm (không cần DOM,
// không cần Electron) để chứng minh chuông/còi kêu đúng lúc — bổ sung cho test_alarm.cjs
// (chỉ kiểm tra cấu trúc). Đây là test hành vi, không phải so khớp chuỗi.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(ROOT, 'ui', 'renderer', 'app.js'), 'utf8');

function extract(name, text = source) {
  const at = text.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `không tìm thấy function ${name}`);
  let depth = 0;
  let started = false;
  for (let i = at; i < text.length; i += 1) {
    if (text[i] === '{') {
      depth += 1;
      started = true;
    } else if (text[i] === '}') {
      depth -= 1;
      if (started && depth === 0) return text.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} không cân dấu ngoặc`);
}

// Môi trường tối thiểu: state + appendLog + window.aibcrAlarm ghi lại lời gọi.
function makeHarness() {
  const calls = [];
  const sandbox = {
    state: { kpi: { signature: null } },
    appendLog: (game, level, text) => calls.push({ kind: 'log', level, text }),
    window: {
      aibcrAlarm: {
        notify: (text, opts) => calls.push({ kind: 'siren', text, level: opts && opts.level }),
        success: (text) => calls.push({ kind: 'chime', text }),
      },
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${extract('kpiStateOf')}\n${extract('syncKpiAudio')}
    this.kpiStateOf = kpiStateOf; this.syncKpiAudio = syncKpiAudio;`, sandbox);
  return { sandbox, calls };
}

const snapshot = ({ blocked = null, takeProfit = false, sessionLost = false } = {}) => ({
  money: { blocked },
  table: { take_profit_done: takeProfit },
  session_lost: sessionLost,
});

const TAKE_PROFIT = ['TAKE_PROFIT', 'Lãi 25% vốn (990). NGỪNG — đã chốt lời.'];
const NO_CAPITAL = ['NO_CAPITAL', 'Vốn còn lại thấp hơn 1 đơn vị cược.'];

test('lần render ĐẦU TIÊN chỉ ghi mốc, không kêu', () => {
  // Mở app khi phiên đã ở trạng thái chốt lời sẵn: render đầu tiên phải im.
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot({ blocked: TAKE_PROFIT, takeProfit: true }));
  assert.equal(calls.filter((c) => c.kind !== 'log').length, 0,
    'mở app lúc phiên đã chốt lời sẵn thì không được giật mình');
});

test('render đầu im, nhưng chuyển sang chốt lời sau đó thì rung chuông', () => {
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot());
  assert.equal(calls.filter((c) => c.kind !== 'log').length, 0);
  sandbox.syncKpiAudio(snapshot({ blocked: TAKE_PROFIT, takeProfit: true }));
  assert.equal(calls.filter((c) => c.kind === 'chime').length, 1);
});

test('chốt lời → chuông thành công đúng MỘT lần dù state lặp lại', () => {
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot());
  sandbox.syncKpiAudio(snapshot({ blocked: TAKE_PROFIT, takeProfit: true }));
  sandbox.syncKpiAudio(snapshot({ blocked: TAKE_PROFIT, takeProfit: true }));
  sandbox.syncKpiAudio(snapshot({ blocked: TAKE_PROFIT, takeProfit: true }));
  const chimes = calls.filter((c) => c.kind === 'chime');
  assert.equal(chimes.length, 1);
  assert.match(chimes[0].text, /chốt lời/);
  assert.equal(calls.filter((c) => c.kind === 'siren').length, 0,
    'chốt lời không được hú còi lỗi');
});

test('take_profit_done nằm dưới snapshot.table vẫn rung chuông', () => {
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot());
  sandbox.syncKpiAudio(snapshot({ takeProfit: true }));
  assert.equal(calls.filter((c) => c.kind === 'chime').length, 1);
  assert.equal(calls.filter((c) => c.kind === 'siren').length, 0);
});

test('hết vốn → còi lỗi, và lặp lại thì im', () => {
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot());
  sandbox.syncKpiAudio(snapshot({ blocked: NO_CAPITAL }));
  sandbox.syncKpiAudio(snapshot({ blocked: NO_CAPITAL }));
  const sirens = calls.filter((c) => c.kind === 'siren');
  assert.equal(sirens.length, 1);
  assert.equal(sirens[0].level, 'error');
  assert.match(sirens[0].text, /Vốn/);
});

test('mất phiên đăng nhập → còi lỗi', () => {
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot());
  sandbox.syncKpiAudio(snapshot({ sessionLost: true }));
  const sirens = calls.filter((c) => c.kind === 'siren');
  assert.equal(sirens.length, 1);
  assert.match(sirens[0].text, /Mất phiên/);
});

test('reset phiên rồi chốt lời lần nữa thì chuông kêu lần nữa', () => {
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot());
  sandbox.syncKpiAudio(snapshot({ blocked: TAKE_PROFIT, takeProfit: true }));
  sandbox.syncKpiAudio(snapshot());                       // session.reset: hết chặn
  sandbox.syncKpiAudio(snapshot({ blocked: TAKE_PROFIT, takeProfit: true }));
  assert.equal(calls.filter((c) => c.kind === 'chime').length, 2);
});

test('chốt lời rồi hết vốn: chuông trước, còi sau, không kêu chéo', () => {
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot());
  sandbox.syncKpiAudio(snapshot({ blocked: TAKE_PROFIT, takeProfit: true }));
  sandbox.syncKpiAudio(snapshot({ blocked: NO_CAPITAL }));
  const audible = calls.filter((c) => c.kind !== 'log');
  assert.deepEqual(audible.map((c) => c.kind), ['chime', 'siren']);
});

test('chốt lời cũng ghi vào nhật ký ở mức win', () => {
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot());
  sandbox.syncKpiAudio(snapshot({ blocked: TAKE_PROFIT, takeProfit: true }));
  const logs = calls.filter((c) => c.kind === 'log');
  assert.equal(logs.length, 1);
  assert.equal(logs[0].level, 'win');
});

test('lỗi cũng ghi vào nhật ký ở mức error', () => {
  const { sandbox, calls } = makeHarness();
  sandbox.syncKpiAudio(snapshot());
  sandbox.syncKpiAudio(snapshot({ blocked: NO_CAPITAL }));
  const logs = calls.filter((c) => c.kind === 'log');
  assert.equal(logs.length, 1);
  assert.equal(logs[0].level, 'error');
});
