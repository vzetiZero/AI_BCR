// Kiểm tra hook WebSocket của WM (aibcr/adapters/wm_ws.py) bằng cách chạy đúng mã JS
// trong một môi trường giả lập. Trọng tâm: socket được tạo TRƯỚC khi cài hook (người
// dùng vào bàn rồi mới bấm Chạy tool) vẫn phải đọc được protocol 21/22 sau khi "nhận".
//
//   node tests/test_wm_hook.cjs
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PY = fs.readFileSync(path.join(__dirname, '..', 'aibcr', 'adapters', 'wm_ws.py'),
                           'utf8');

/** Đọc một hằng chuỗi JS (r"""...""") từ file Python. */
function js(name) {
  const re = new RegExp('^' + name + ' = r"""(.*?)"""$', 'ms');
  const found = re.exec(PY);
  assert.ok(found, 'không tìm thấy ' + name + ' trong wm_ws.py');
  return found[1];
}

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.listeners = [];
    this.sent = [];
  }
  addEventListener(type, fn) {
    if (type === 'message') this.listeners.push(fn);
  }
  send(data) { this.sent.push(data); }
  /** Server đẩy một khung tới trang. */
  deliver(frame) { this.listeners.forEach((fn) => fn({ data: frame })); }
}

/** Một "realm" = window với WebSocket riêng, giống khung gốc / iframe cùng origin. */
function realm(tag) {
  const context = {
    WebSocket: FakeWebSocket,
    TextDecoder,
    TextEncoder,
    Uint8Array,
    JSON,
    Date,
    ArrayBuffer,
    Blob: undefined,
    console,
  };
  context.window = context;
  context.top = context;
  context.document = { querySelectorAll: () => [] };
  vm.createContext(context);
  if (tag) context.__tag = tag;
  return context;
}

function frame21(groupID) {
  return JSON.stringify({ protocol: 21, data: { groupID, gameNo: 9, gameNoRound: 3 } });
}
function frame22(ok) {
  return JSON.stringify({ protocol: 22, data: { bOk: ok } });
}

const cases = [];
function test(name, fn) { cases.push([name, fn]); }

test('socket tạo SAU hook được bắt và ghi protocol 21/22', () => {
  const win = realm();
  vm.runInContext(js('HOOK_JS'), win);
  const ws = new win.WebSocket('wss://wm/table');
  ws.deliver(frame21(1352));
  ws.deliver(frame22(true));
  const store = win.__wmHook3;
  assert.strictEqual(store.sockets.length, 1);
  assert.strictEqual(store.protocols[0], 22);
  assert.strictEqual(store.recent21.length, 1);
  assert.strictEqual(store.recent21[0].data.groupID, 1352);
  assert.strictEqual(store.last22.data.bOk, true);
  assert.strictEqual(store.recent22.length, 1, 'phải giữ lịch sử phản hồi lệnh cược');
});

test('socket tạo TRƯỚC hook vẫn nhận được bằng ADOPT_FN', () => {
  const win = realm();
  // Tạo socket trước khi cài hook: mô phỏng "đã vào bàn rồi mới bấm Chạy tool".
  const old = new win.WebSocket('wss://wm/table');
  vm.runInContext(js('HOOK_JS'), win);
  assert.strictEqual(win.__wmHook3.sockets.length, 0, 'hook không thấy socket cũ');

  const adopt = vm.runInContext('(' + js('ADOPT_FN').trim() + ')', win);
  const store = win.__wmHook3;
  const added = adopt.call([old], store, 'iframe');
  assert.strictEqual(added, 1, 'phải nhận đúng 1 socket cũ');
  assert.strictEqual(store.sockets[0], old);
  assert.strictEqual(store.scope[0], 'iframe');

  old.deliver(frame21(508));
  old.deliver(frame22(true));
  assert.strictEqual(store.recent21[0].data.groupID, 508);
  assert.strictEqual(store.recent22.length, 1);
  assert.strictEqual(store.adopted, 1);
});

test('nhận lại lần hai không nhân đôi socket', () => {  const win = realm();
  const old = new win.WebSocket('wss://wm/table');
  vm.runInContext(js('HOOK_JS'), win);
  const adopt = vm.runInContext('(' + js('ADOPT_FN').trim() + ')', win);
  const store = win.__wmHook3;
  assert.strictEqual(adopt.call([old], store, 'iframe'), 1);
  assert.strictEqual(adopt.call([old], store, 'iframe'), 0);
  assert.strictEqual(store.sockets.length, 1);
});

test('khung của iframe khác origin (realm khác) vẫn đọc được ArrayBuffer', () => {
  const win = realm();
  vm.runInContext(js('HOOK_JS'), win);
  const ws = new win.WebSocket('wss://wm/table');
  const bytes = new TextEncoder().encode(frame21(7));
  // ArrayBuffer của realm khác: instanceof ở realm này là false.
  const foreign = vm.runInNewContext('new ArrayBuffer(' + bytes.length + ')');
  new Uint8Array(foreign).set(bytes);
  ws.deliver(foreign);
  assert.strictEqual(win.__wmHook3.recent21.length, 1,
                     'phải nhận theo cấu trúc, không theo instanceof');
  assert.strictEqual(win.__wmHook3.recent21[0].data.groupID, 7);
});

test('ADOPT_FN bỏ qua object không phải WebSocket', () => {
  const win = realm();
  vm.runInContext(js('HOOK_JS'), win);
  const adopt = vm.runInContext('(' + js('ADOPT_FN').trim() + ')', win);
  const added = adopt.call([{}, null, 5, { readyState: 1 }], win.__wmHook3, 'top');
  assert.strictEqual(added, 0);
});

test('WINDOWS_JS chỉ gom khung truy cập được (khác origin thì bỏ)', () => {
  const win = realm();
  const frames = [
    { contentWindow: realm() },
    { get contentWindow() { throw new Error('cross origin'); } },
  ];
  win.document = { querySelectorAll: () => frames };
  const list = vm.runInContext(js('WINDOWS_JS'), win);
  assert.strictEqual(list.length, 2, 'window gốc + 1 iframe cùng origin');
  assert.ok(list[0].WebSocket === win.WebSocket, 'phần tử đầu phải là window gốc');
  assert.ok(list[1].WebSocket, 'iframe cùng origin phải có mặt');
});

test('socket tạo SAU khi nhận socket cũ vẫn được hook bắt', () => {
  // ``adopt_existing`` tạo store rồi bọc constructor; socket tạo sau đó (trang tự kết
  // nối lại khi đổi bàn) phải vào store, nếu không tool lại mất socket.
  const win = realm();
  const old = new win.WebSocket('wss://wm/table');
  vm.runInContext(js('HOOK_JS'), win);
  const adopt = vm.runInContext('(' + js('ADOPT_FN').trim() + ')', win);
  const store = win.__wmHook3;
  assert.strictEqual(adopt.call([old], store, 'iframe'), 1);
  const later = new win.WebSocket('wss://wm/table2');
  later.deliver(frame21(99));
  assert.strictEqual(store.sockets.length, 2, 'socket mới vẫn phải được bọc');
  assert.strictEqual(store.recent21[store.recent21.length - 1].data.groupID, 99);
});

test('phản hồi protocol 22 được chép cả bOk/serial cấp ngoài lẫn trong data', () => {
  const win = realm();
  vm.runInContext(js('HOOK_JS'), win);
  const ws = new win.WebSocket('wss://wm/table');
  // Dạng 1: mọi thứ nằm trong ``data``.
  ws.deliver(JSON.stringify({ protocol: 22, data: { bOk: true, betSerialNumber: 7 } }));
  const store = win.__wmHook3;
  assert.strictEqual(store.last22.ok, true, 'bOk trong data phải đọc được');
  assert.strictEqual(store.last22.serial, 7, 'serial trong data phải đọc được');
  // Dạng 2: ``bOk`` ở cấp ngoài, ``data`` null — dạng tài liệu dự án mô tả.
  ws.deliver(JSON.stringify({ protocol: 22, bOk: true, betSerialNumber: 8 }));
  assert.strictEqual(store.last22.ok, true, 'bOk cấp ngoài phải đọc được');
  assert.strictEqual(store.last22.serial, 8, 'serial cấp ngoài phải đọc được');
  assert.strictEqual(store.recent22.length, 2, 'phải giữ cả hai phản hồi');
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log('✔ ' + name);
  } catch (error) {
    failed += 1;
    console.log('✖ ' + name + '\n    ' + error.message);
  }
}
console.log('\n' + (cases.length - failed) + '/' + cases.length + ' passed');
process.exitCode = failed ? 1 : 0;
