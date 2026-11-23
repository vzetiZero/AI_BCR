const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(__dirname + '/../aibcr/adapters/reader.js', 'utf8');

function canvas(history, {id = 'tableABC', title = 'Baccarat Tốc Độ A', type = 'baccarat', stale = false} = {}) {
  const root = {stateNode: {}};
  root.stateNode.current = root;
  const table = {memoizedProps: {table: {id, title}}, return: root};
  const game = {memoizedProps: {gameType: type}, return: table};
  const road = {memoizedProps: {type: 'BaccaratHistoryRoadTypeBig', history}, return: game};
  const current = {memoizedProps: {}, return: road};
  const oldRoot = {stateNode: {current: root}};
  const old = {memoizedProps: {}, return: oldRoot, alternate: current};
  const element = {__reactFiber$test: stale ? old : current,
    getBoundingClientRect: () => ({x: 10, y: 20, width: 250, height: 48})};
  root.child = table;
  table.child = game;
  game.child = road;
  road.child = current;
  current.stateNode = element;
  return element;
}

function scan(canvases) {
  const ctx = {window: {}, location: {href: 'https://tmrw17k36as.evo-games.com/frontend/evo/r3/?token=not-output#category=all_games',
    origin: 'https://tmrw17k36as.evo-games.com', pathname: '/frontend/evo/r3/'},
    document: {title: 'Lobby', querySelector: () => ({}), querySelectorAll: selector => {
      assert.equal(selector, '[data-role="history-roads"] canvas');
      return canvases;
    }}};
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return JSON.parse(JSON.stringify(ctx.window.__AIBCR_SCAN()));
}

const history = [{pos: [0, 0], c: 'R'}, {pos: [1, 0], c: 'B', ties: 2}, {pos: [2, 0], c: 'R'}];

test('uses result array order, red=Banker, blue=Player, includes ties', () => {
  const result = scan([canvas(history)]);
  assert.equal(result.tables[0].letters, 'BPTTB');
  assert.equal(result.tables[0].label, 'Baccarat Tốc Độ A');
  assert.equal(result.tables[0].table_id, 'tableABC');
  assert.equal(result.tables[0].read_error, '');
  assert.equal(result.tables[0].source, 'evo-canvas-big');
  assert.equal(result.diag.kind, 'evo-canvas');
  assert.ok(!JSON.stringify(result).includes('not-output'));
});

test('reads committed alternate fiber rather than stale branch', () => {
  assert.equal(scan([canvas(history, {stale: true})]).tables[0].letters, 'BPTTB');
});

test('ignores stale return ancestry even when it reaches the current root', () => {
  const element = canvas(history);
  const current = element.__reactFiber$test;
  const root = current.return.return.return.return;
  element.__reactFiber$test = {return: {memoizedProps: {
    type: 'BaccaratHistoryRoadTypeBig', history: history.slice(0, 1)
  }, return: {memoizedProps: {table: {id: 'wrong', title: 'Old'}}, return: root}}};
  assert.equal(scan([element]).tables[0].letters, 'BPTTB');
  assert.equal(scan([element]).tables[0].table_id, 'tableABC');
});

test('blocks known special-payout variants even when gameType says baccarat', () => {
  for (const id of ['LightningBac0001', 'XXXtremeLB000001', 'NoCommBac0000001',
    'gwbaccarat000001', 'Empgwbaccarat001', 'PTBaccarat000001', 'InsuranceBac0001']) {
    const row = scan([canvas(history, {id})]).tables[0];
    assert.ok(row.read_error.includes('trả thưởng đặc biệt'));
    assert.equal(row.letters, 'BPTTB');
  }
  const element = canvas(history);
  element.__reactFiber$test.return.return.return.memoizedProps.table.gameDetails = {gameConfig: {lightning: true}};
  assert.ok(scan([element]).tables[0].read_error.includes('trả thưởng đặc biệt'));
});

test('keeps IDs distinct when translated table names match', () => {
  const result = scan([canvas(history), canvas(history, {id: 'tableXYZ'})]);
  assert.equal(result.tables.length, 2);
  assert.notEqual(result.tables[0].table_id, result.tables[1].table_id);
});

test('marks first-cell ties as ambiguous rather than claiming exact history', () => {
  const row = scan([canvas([{pos: [0, 0], c: 'R', ties: 1}])]).tables[0];
  assert.ok(row.read_error.includes('Ô đầu có hòa'));
});

test('rejects truncated first column and unknown colors', () => {
  assert.ok(scan([canvas([{pos: [4, 0], c: 'R'}])]).tables[0].read_error);
  const row = scan([canvas([{pos: [0, 0], c: 'G'}])]).tables[0];
  assert.equal(row.letters, '');
  assert.ok(row.read_error);
});

test('rejects invalid ties and oversized road', () => {
  for (const ties of [-1, 1.5, 51, '2']) {
    const row = scan([canvas([{pos: [0, 0], c: 'R', ties}])]).tables[0];
    assert.equal(row.letters, '');
    assert.ok(row.read_error);
  }
  const row = scan([canvas(Array.from({length: 401}, (_, i) => ({pos: [i, 0], c: 'R'})))]).tables[0];
  assert.equal(row.letters, '');
  assert.ok(row.read_error);
});

test('exposes new shoes and Lucky metadata but ignores unrelated games', () => {
  const result = scan([canvas([]), canvas(history, {id: 'lucky', type: 'luckybaccarat'}),
    canvas(history, {id: 'roulette', type: 'roulette'})]);
  assert.equal(result.tables.length, 2);
  assert.equal(result.tables[0].letters, '');
  assert.equal(result.tables[1].game_type, 'luckybaccarat');
});

test('does not fall back to unrelated SVG shapes in the lobby', () => {
  assert.equal(scan([]).tables.length, 0);
});

function beadRoad(names) {
  return {
    getBoundingClientRect: () => ({x: 5, y: 7, width: 320, height: 260}),
    querySelectorAll: selector => {
      assert.equal(selector, '[data-role="roadItem"][name]');
      return names.map(name => ({getAttribute: key => {
        assert.equal(key, 'name');
        return name;
      }}));
    },
  };
}

function scanTableScreen(beads, href = 'https://tmrw17k36as.evo-games.com/frontend/evo/r3/#category=all_games&table_id=suropcmnbqpabwko') {
  const ctx = {window: {}, location: {href, origin: 'https://tmrw17k36as.evo-games.com', pathname: '/frontend/evo/r3/'},
    document: {title: 'Baccarat', querySelector: selector => {
      assert.ok(['[data-role="header-balance"]', '[data-role="footer-balance"]',
        '[data-role="table-tile"]'].includes(selector), selector);
      return null;
    }, querySelectorAll: selector => {
      assert.ok(['*', 'svg circle, svg ellipse, svg rect, svg path, svg use',
        '[data-role="history-roads"] canvas', '[data-role="Bead-road"]'].includes(selector), selector);
      return selector === '[data-role="Bead-road"]' ? beads : [];
    }}};
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return JSON.parse(JSON.stringify(ctx.window.__AIBCR_SCAN()));
}

test('reads the opened-table bead road and ignores pair badges after the result', () => {
  const result = scanTableScreen([beadRoad(['Banker', 'Banker Player', 'Player', 'Player Banker', 'Tie'])]);
  assert.equal(result.diag.kind, 'evo-bead');
  assert.deepEqual(result.tables.map(r => r.letters), ['BBPPT']);
  assert.equal(result.tables[0].read_error, '');
  assert.equal(result.tables[0].source, 'evo-bead');
  // Tên bàn không có trong DOM: nhãn tạm là mã bàn đọc từ URL, browser.py gắn table_id thật.
  assert.equal(result.tables[0].label, 'suropcmnbqpabwko');
  assert.equal(result.tables[0].table_id, '');
});

test('rejects unknown bead symbols and oversized table roads', () => {
  const odd = scanTableScreen([beadRoad(['Banker', 'Dragon'])]).tables[0];
  assert.equal(odd.letters, '');
  assert.ok(odd.read_error.includes('ký hiệu lạ'));
  const long = scanTableScreen([beadRoad(Array.from({length: 401}, () => 'Banker'))]).tables[0];
  assert.equal(long.letters, '');
  assert.ok(long.read_error.includes('400 tay'));
});

test('skips bead containers without readable items', () => {
  assert.equal(scanTableScreen([beadRoad([])]).tables.length, 0);
});

// EVO_ENTRY_JS song song với luồng bấm nút ở lobby_stream.py: đọc trực tiếp từ nguồn Python.
// JS được ghép từ CLICK_PRELUDE_JS (bấm trong trang) + EVO_ENTRY_BODY_JS (chọn thẻ EVO).
const lobbySource = fs.readFileSync(__dirname + '/../aibcr/adapters/lobby_stream.py', 'utf8');
const clickPrelude = /CLICK_PRELUDE_JS = r"""([\s\S]*?)"""/.exec(lobbySource)[1];
const entryBody = /EVO_ENTRY_BODY_JS = r"""([\s\S]*?)"""/.exec(lobbySource)[1];
const entrySource = '(() => {' + clickPrelude + entryBody + '})()';

function evoItem({attrs = {'data-subprovider': 'EVO'}, alt = 'Evolution', disabled = false,
                  buttonClass = 'nrc-button btn-main', buttonText = 'Chơi game',
                  rect = {x: 200, y: 400, width: 120, height: 40}, afterScroll = null,
                  card = {x: 180, y: 360, width: 240, height: 150}, cardAfterScroll = null} = {}) {
  const box = (r) => ({...r, bottom: r.y + r.height, right: r.x + r.width});
  const button = {
    disabled, className: buttonClass,
    innerText: buttonText,
    getAttribute: (key) => (key === 'title' ? '' : null),
    scrollIntoView: () => { if (afterScroll) Object.assign(button.current, afterScroll); },
    getBoundingClientRect: () => box(button.current),
    click: () => clicks.push('button'),
    current: {...rect},
  };
  const node = {
    getAttribute: (name) => (name in attrs ? attrs[name] : null),
    scrollIntoView: () => { if (cardAfterScroll) Object.assign(node.current, cardAfterScroll); },
    getBoundingClientRect: () => box(node.current),
    contains: (element) => !!element && !element.__outside,
    click: () => clicks.push('card'),
    querySelector: (selector) => {
      if (selector === 'img') return alt === null ? null : {alt};
      if (selector === 'button.btn-main') return /\bbtn-main\b/.test(buttonClass) ? button : null;
      return selector === 'button' ? button : null;
    },
    current: {...card},
  };
  return {attrs, node};
}

// Cú bấm trong trang KHÔNG gửi sự kiện chuột CDP: press() phát pointer/mouse rồi click
// trên chính phần tử. Mock chỉ cần ghi lại phần tử nào nhận cú bấm.
let clicks = [];

function portalScan(items, {inIframe = false, innerHeight = 800, innerWidth = 600, hit = 'card'} = {}) {
  // elementFromPoint mô phỏng đúng ô thật nằm dưới điểm bấm: ngôi sao yêu thích che góc phải thẻ.
  clicks = [];
  const hits = {card: {closest: () => null}, fav: {closest: () => ({})},
                outside: {closest: () => null, __outside: true}, none: null};
  const ctx = {innerHeight, innerWidth,
    document: {querySelectorAll: (selector) => {
      const wanted = selector.split(',').map((part) => part.trim());
      assert.equal(wanted.length, 3);
      return items.filter((item) => wanted.some((part) => {
        const rule = /^\.game-item\[data-([a-z-]+)="([^"]+)"\]/.exec(part);
        return rule && item.attrs['data-' + rule[1]] === rule[2];
      })).map((item) => item.node);
    }, elementFromPoint: () => hits[hit]}};
  const win = {};
  win.top = inIframe ? {frameElement: true} : win;
  ctx.window = win;
  vm.createContext(ctx);
  return JSON.parse(JSON.stringify(vm.runInContext(entrySource, ctx)));
}

test('presses the Evolution play button in-page instead of measuring a pointer', () => {
  const box = portalScan([evoItem({attrs: {'data-subprovider': 'SA'}}), evoItem()]);
  assert.equal(box.ok, true);
  assert.equal(box.label, 'Chơi game');
  assert.equal(box.via, 'button');
  assert.deepEqual(clicks, ['button'], 'chỉ nút, không phải thẻ');
});

test('finds the current entry markup: data-gp evo tile with a plain "Bắt đầu" button', () => {
  const box = portalScan([evoItem({attrs: {'data-id': 'LIVE-EVO', 'data-gp': 'evo'}, alt: null,
                                    buttonClass: '', buttonText: 'Bắt đầu'})]);
  assert.equal(box.ok, true);
  assert.equal(box.label, 'Bắt đầu');
  assert.equal(box.via, 'button');
});

test('clicks the tile itself when the start button stays display:none', () => {
  // Markup thật 11/2026: thẻ 239x154 có onclick, nút "Bắt đầu" ẩn (rect 0x0) dù hover.
  const box = portalScan([evoItem({attrs: {'data-id': 'LIVE-EVO', 'data-gp': 'evo'}, alt: null,
                                    buttonClass: '', buttonText: 'Bắt đầu',
                                    rect: {x: 0, y: 0, width: 0, height: 0}})]);
  assert.equal(box.via, 'card');
  assert.equal(box.label, 'LIVE-EVO');
  assert.deepEqual(clicks, ['card']);
});

test('scrolls a below-the-fold play button into view before pressing it', () => {
  const box = portalScan([evoItem({rect: {x: 200, y: 900, width: 120, height: 40},
                                    afterScroll: {x: 200, y: 400, width: 120, height: 40}})]);
  assert.equal(box.ok, true);
  assert.equal(box.via, 'button');
  assert.deepEqual(clicks, ['button']);
});

test('scrolls a below-the-fold tile into view before clicking it', () => {
  const box = portalScan([evoItem({rect: {x: 0, y: 0, width: 0, height: 0},
                                   card: {x: 180, y: 900, width: 240, height: 150},
                                   cardAfterScroll: {x: 180, y: 360, width: 240, height: 150}})]);
  assert.equal(box.via, 'card');
  assert.deepEqual(clicks, ['card']);
});

test('clicks the tile when the window clips it but a usable band stays visible', () => {
  // Cửa sổ thấp hơn thẻ (lỗi thật 11/2026): vẫn bấm được nếu còn dải đủ lớn trong khung nhìn.
  const box = portalScan([evoItem({rect: {x: 0, y: 0, width: 0, height: 0},
                                   card: {x: 180, y: 360, width: 240, height: 150}})],
                         {innerHeight: 420, innerWidth: 600});
  assert.equal(box.via, 'card');
  assert.deepEqual(clicks, ['card']);
});

test('refuses to click an iframe, a missing tile or a disabled button', () => {
  assert.deepEqual(portalScan([evoItem()], {inIframe: true}), {reason: 'not_top_frame'});
  assert.deepEqual(portalScan([evoItem({attrs: {'data-subprovider': 'SA'}})]), {reason: 'evo_item_not_found'});
  assert.deepEqual(portalScan([evoItem({disabled: true})]), {reason: 'evo_button_not_found'});
  assert.deepEqual(portalScan([evoItem({rect: {x: 0, y: 0, width: 0, height: 0},
                                          card: {x: 180, y: 900, width: 240, height: 150}})]),
                   {reason: 'evo_button_pending'});
  assert.deepEqual(clicks, [], 'không bấm gì khi bị từ chối');
});

test('refuses the tile when the favourite star or nothing else is under its hit point', () => {
  const hiddenButton = [evoItem({attrs: {'data-id': 'LIVE-EVO', 'data-gp': 'evo'}, alt: null,
                                 buttonClass: '', rect: {x: 0, y: 0, width: 0, height: 0}})];
  for (const hit of ('fav outside none'.split(' '))) {
    assert.deepEqual(portalScan(hiddenButton, {hit}), {reason: 'evo_card_blocked'});
    assert.deepEqual(clicks, [], 'không bấm vào ngôi sao yêu thích');
  }
});

test('reports a pending button when the tile is found but nothing is measurable yet', () => {
  assert.deepEqual(portalScan([evoItem({attrs: {'data-gp': 'evo'}, alt: null,
                                        buttonClass: '', buttonText: 'Bắt đầu',
                                        rect: {x: 0, y: 0, width: 0, height: 0},
                                        card: {x: 180, y: 360, width: 4, height: 150}})]),
                   {reason: 'evo_button_pending'});
});

test('prefers the Evolution tile and stops when two tiles look identical', () => {
  const picked = portalScan([evoItem({alt: 'Ezugi', rect: {x: 0, y: 100, width: 120, height: 40}}),
                             evoItem({rect: {x: 200, y: 100, width: 120, height: 40}})]);
  assert.equal(picked.ok, true);
  assert.equal(picked.via, 'button');
  const ambiguous = portalScan([evoItem(), evoItem()]);
  assert.equal(ambiguous.reason, 'evo_button_ambiguous');
  assert.equal(ambiguous.count, 2);
});

// READ_JS trong table_bets.py: chạy đúng đoạn JS mà app tiêm vào bàn thật.
const readSource = /READ_JS = r"""([\s\S]*?)"""/.exec(
  fs.readFileSync(__dirname + '/../aibcr/adapters/table_bets.py', 'utf8'))[1];

function selectedChip({marked = [], outside = [], label = null, labelText = null, tray = true} = {}) {
  const chip = (v) => ({
    getAttribute: (k) => (k === 'data-value' ? String(v) : null),
    // READ_JS chỉ giữ chip HIỂN THỊ khi đếm khay; 40×40 như chip thật trên sàn.
    getBoundingClientRect: () => ({x: 0, y: 0, width: 40, height: 40}),
    innerText: String(v),
    textContent: String(v),
  });
  const markedSelector = '[data-role="chip"][data-selected="true"]';
  const chipSelector = '[data-role="chip"][data-value]';
  const wrap = label === null && labelText === null ? null : {
    querySelector: (selector) => {
      assert.equal(selector, '[data-role="chip"][data-value]');
      return label === null ? null : chip(label);
    },
    innerText: labelText === null ? '' : String(labelText),
    textContent: labelText === null ? '' : String(labelText),
  };
  // Chip preview trong nhãn và chip đã đặt trên ô cược cũng mang data-role="chip";
  // chỉ những chip TRONG KHAY mới được tính là tín hiệu "đang chọn".
  const trayEl = tray ? {querySelectorAll: (selector) => {
    if (selector === '[data-role="chip"]') return marked.map(chip);
    if (selector === chipSelector) return marked.map(chip);
    assert.equal(selector, markedSelector);
    return marked.map(chip);
  }} : null;
  const ctx = {
    document: {
      querySelectorAll: (selector) => {
        if (selector === chipSelector) return [];
        assert.equal(selector, markedSelector);
        return marked.concat(outside).map(chip);
      },
      querySelector: (selector) => {
        if (selector === '[data-role="chip-stack"]') return trayEl;
        if (selector === '[data-role="selected-chip"]') return wrap;
        if (selector === '[data-role="Bead-road"]') return null;
        if (selector === '[data-role="total-bet-label"]') return null;
        // Không có ô cược: lượt đọc vẫn trả selected_chip bình thường.
        return null;
      },
    },
    getComputedStyle: () => ({display: 'block', visibility: 'visible'}),
  };
  vm.createContext(ctx);
  return vm.runInContext(readSource, ctx).selected_chip;
}

test('prefers the tray chip marked data-selected over the lagging label', () => {
  // Nhãn [data-role="selected-chip"] còn giữ 20 trong khi khay đã chuyển sang 100:
  // đọc một lần là báo nhầm "Bấm chọn chip 100 nhưng khay đang chọn 20".
  assert.equal(selectedChip({marked: [100], label: 20}), 100);
});

test('falls back to the label when the tray exposes no single marked chip', () => {
  assert.equal(selectedChip({label: 20}), 20);
  assert.equal(selectedChip({marked: [20, 100], label: 500}), 500, 'hai chip được đánh dấu là mơ hồ');
  assert.equal(selectedChip({marked: ['x'], label: 40}), 40, 'mệnh giá rác không được chặn nhãn');
});

test('reads digits from the label when it only exposes text', () => {
  assert.equal(selectedChip({labelText: 'Chip đang chọn 100'}), 100);
});

test('returns null rather than guessing when neither signal exists', () => {
  assert.equal(selectedChip({}), null);
});

test('ignores marked chips outside the tray so the preview cannot fake ambiguity', () => {
  // Khay đã chọn 100 nhưng nhãn selected-chip vẫn trưng chip 20: đếm chung cả document
  // thành 2 chip "đang chọn" và rơi xuống nhãn trễ — đúng lỗi báo nhầm mệnh giá cũ.
  assert.equal(selectedChip({marked: [100], outside: [20], label: 20}), 100);
  assert.equal(selectedChip({marked: [], outside: [20], label: 20}), 20);
});

test('still reads the marked chip when the tray has not rendered yet', () => {
  assert.equal(selectedChip({tray: false, marked: [100], label: 20}), 100);
});

// READ_JS trong table_bets.py (đã khai báo ở trên): nhận diện màn hình bàn và đọc mệnh
// giá thật của khay.
function readChip(value, {selected = false} = {}) {
  return {
    getAttribute: (k) => (k === 'data-value' ? String(value)
      : k === 'data-selected' ? (selected ? 'true' : null) : null),
    getBoundingClientRect: () => ({x: 0, y: 0, width: 40, height: 40}),
    innerText: String(value),
    textContent: String(value),
  };
}

function readTable({bead = false, tray = false, trayChips = [], spots = {},
                    marked = [], label = null, total = null} = {}) {
  const markedSelector = '[data-role="chip"][data-selected="true"]';
  const chipSelector = '[data-role="chip"][data-value]';
  const trayEl = tray ? {querySelectorAll: (selector) => {
    if (selector === '[data-role="chip"]') return trayChips.map((v) => readChip(v));
    assert.equal(selector, markedSelector);
    return marked.map((v) => readChip(v, {selected: true}));
  }} : null;
  const spotEls = {};
  for (const [role, values] of Object.entries(spots)) {
    spotEls['[data-role="bet-spot-' + role + '"]'] = {querySelectorAll: (selector) => {
      assert.equal(selector, '[data-role="chip"]');
      return values.map((v) => readChip(v));
    }};
  }
  const document = {
    querySelectorAll: (selector) => {
      if (selector === chipSelector) return [];
      assert.equal(selector, markedSelector);
      return marked.map((v) => readChip(v, {selected: true}));
    },
    querySelector: (selector) => {
      if (selector === '[data-role="Bead-road"]') return bead ? {} : null;
      if (selector === '[data-role="chip-stack"]') return trayEl;
      if (selector === '[data-role="total-bet-label"]') {
        return total === null ? null : {innerText: String(total), textContent: String(total)};
      }
      if (selector === '[data-role="selected-chip"]') {
        return label === null ? null : {querySelector: () => readChip(label),
                                        innerText: String(label), textContent: String(label)};
      }
      return spotEls[selector] || null;
    },
  };
  const ctx = {document, getComputedStyle: () => ({display: 'block', visibility: 'visible'})};
  vm.createContext(ctx);
  return JSON.parse(JSON.stringify(vm.runInContext(readSource, ctx)));
}

test('a rendered betting area counts as a table even before the bead road appears', () => {
  // Khay chip + ô cược đã mở mà Bead-road chưa render: đòi mỗi Bead-road thì app báo
  // "no_table_view" mãi và không bao giờ đặt chip dù người dùng đã thấy khay.
  const out = readTable({tray: true, trayChips: [20, 100], spots: {Banker: [], Player: []}});
  assert.equal(out.has_table, true);
  assert.deepEqual(out.denominations, [20, 100]);
});

test('the bead road alone still identifies the table', () => {
  assert.equal(readTable({bead: true}).has_table, true);
});

test('a tray without bet spots is not a table view', () => {
  assert.equal(readTable({tray: true, trayChips: [20]}).has_table, false);
});

test('bet spots without a tray are not a table view yet', () => {
  assert.equal(readTable({spots: {Banker: []}}).has_table, false);
});

test('an empty document is not a table view', () => {
  assert.equal(readTable({}).has_table, false);
});

test('reads the denominations actually shown on the tray, deduplicated', () => {
  const out = readTable({bead: true, tray: true, trayChips: [100, 20, 100, 500]});
  assert.deepEqual(out.denominations, [20, 100, 500]);
});

test('counts only the chips lying on a bet spot', () => {
  const out = readTable({bead: true, tray: true, trayChips: [20], spots: {Banker: [100, 20]}});
  assert.equal(out.spots.B.amount, 120);
  assert.deepEqual(out.spots.B.chips, [100, 20]);
  assert.equal(out.total_units, 120);
});
