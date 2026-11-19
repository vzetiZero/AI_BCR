// Renderer AI_BCR: một khối state duy nhất cho Baccarat.
// Bàn chơi / cấu hình / lịch sử / nhật ký (port từ giao diện Tk cũ).
const api = window.aibcr;
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// Gom lỗi runtime để kiểm tra nhanh khi chạy thử (không ảnh hưởng vận hành).
window.__errors = [];
window.addEventListener('error', (event) => window.__errors.push(String(event.message || event.error)));
window.addEventListener('unhandledrejection', (event) => window.__errors.push(String(event.reason)));

const state = {
  game: 'baccarat',
  baccarat: null,
  logs: { baccarat: [] },
  sub: { baccarat: 'play' },
  tableSearch: '',
  huntTick: 0,
};

/* ------------------------------- định dạng ------------------------------- */
const fmtVn2 = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const text = Number(value).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${text} VN2`;
};
const fmtMoney = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toLocaleString('de-DE', { maximumFractionDigits: 2 })} VN2`;
};
const fmtUnits = (value, digits = 0) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('de-DE', { maximumFractionDigits: digits });
};
const fmtVnd = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toLocaleString('de-DE', { maximumFractionDigits: 0 })}đ`;
};
const fmtTime = (seconds) => {
  if (!seconds) return '—';
  return new Date(Number(seconds) * 1000).toLocaleTimeString('vi-VN', { hour12: false });
};
const fmtSigned = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const number = Number(value);
  return `${number > 0 ? '+' : ''}${number.toLocaleString('de-DE', { maximumFractionDigits: 2 })}`;
};
const pct = (value) => `${Number(value || 0).toFixed(2)}%`;
const SIDE_NAME = { B: 'Banker', P: 'Player', T: 'Hoà' };

function toast(message) {
  const box = $('#toast');
  if (!box) return;
  box.textContent = message;
  box.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.classList.remove('show'), 2800);
}

/* --------------------------------- gọi API -------------------------------- */
async function call(game, method, params = {}) {
  const reply = await api.call(game, method, params);
  const payloadError = reply && reply.result && reply.result.ok === false
    ? reply.result.error || reply.result.note || 'engine từ chối lệnh'
    : null;
  if (!reply || reply.ok === false || payloadError) {
    const text = (reply && reply.error) || payloadError || 'lỗi không rõ';
    appendLog(game, 'error', `${method}: ${text}`);
    toast(text);
    return null;
  }
  return reply.result;
}

/* ---------------------------------- log ---------------------------------- */
function appendLog(game, level, text) {
  const list = state.logs[game];
  list.push({ at: new Date().toLocaleTimeString('vi-VN', { hour12: false }), level, text });
  if (list.length > 400) list.shift();
  renderLog(game);
}

function renderLog(game) {
  const box = $('#ba-log');
  if (!box) return;
  const list = state.logs[game];
  if (!list.length) {
    box.innerHTML = '<div class="log-line info"><span class="m">Chưa có dòng nào.</span></div>';
    return;
  }
  box.innerHTML = list
    .map((entry) => `<div class="log-line ${entry.level}"><span class="t">${entry.at}</span><span class="m">${escapeHtml(entry.text)}</span></div>`)
    .join('');
  box.scrollTop = box.scrollHeight;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

// Nút "Mở Chrome" phải luôn bấm lại được: người dùng có thể đóng cửa sổ Chrome
// bất cứ lúc nào, khi đó chỉ đổi nhãn thành "Mở lại Chrome".
const chromeSeen = { baccarat: false };

function syncOpenChromeButton(selector, game, snapshot) {
  const button = $(selector);
  if (!button) return;
  if (snapshot.chrome_connected) chromeSeen[game] = true;
  button.disabled = false;
  button.textContent = (!snapshot.chrome_connected && chromeSeen[game])
    ? 'Mở lại Chrome' : 'Mở Chrome';
  button.title = snapshot.chrome_connected
    ? 'Chrome đang kết nối — nếu bạn đã đóng cửa sổ, bấm để mở lại'
    : (chromeSeen[game] ? 'Chrome đã đóng — bấm để mở lại' : 'Chrome chưa kết nối — bấm để mở');
}

/* ------------------------------- điều hướng ------------------------------- */
function switchGame(game) {
  state.game = game;
  $$('.game').forEach((button) => button.classList.toggle('active', button.dataset.game === game));
  $$('.view').forEach((view) => view.classList.toggle('hidden', view.id !== `view-${game}`));
  switchSub(game, state.sub[game] || 'play');
}

function switchSub(game, sub) {
  state.sub[game] = sub;
  const nav = $(`[data-subtabs="${game}"]`);
  if (nav) {
    $$('button', nav).forEach((button) => button.classList.toggle('active', button.dataset.sub === sub));
  }
  $$(`.subview[data-game="${game}"]`).forEach((view) => view.classList.toggle('hidden', view.dataset.sub !== sub));
}

/* =============================== BACCARAT =============================== */
const CHIP_STATUS = {
  match: ['KHỚP', 'ok'],
  no_bet: ['CHƯA ĐẶT', 'warn'],
  wrong_side: ['SAI CỬA', 'bad'],
  wrong_amount: ['SAI TIỀN', 'bad'],
  extra_bets: ['ĐẶT THÊM', 'bad'],
  ungroupable: ['KHÔNG GHÉP ĐƯỢC', 'bad'],
  unknown: ['CHƯA ĐỌC', 'warn'],
};

function setText(selector, text) {
  const node = $(selector);
  if (node) node.textContent = text;
}

function setValue(selector, value) {
  const node = $(selector);
  if (node && document.activeElement !== node) node.value = value ?? '';
}

function renderRoad(letters) {
  const box = $('#ba-road');
  if (!box) return;
  const text = String(letters || '');
  if (!text) {
    box.innerHTML = '<span class="hint">Chưa có cầu.</span>';
    return;
  }
  box.innerHTML = text
    .split('')
    .slice(-60)
    .map((ch) => `<span class="road-cell ${ch.toLowerCase()}">${ch}</span>`)
    .join('');
}

function renderChips(chips, plan) {
  const statusBox = $('#ba-chip-status');
  const detailBox = $('#ba-chip-detail');
  if (!statusBox || !detailBox) return;
  const check = chips && chips.check;
  const live = chips && chips.live;
  if (!check) {
    statusBox.textContent = 'Chưa đọc chip.';
    statusBox.dataset.state = 'warn';
    detailBox.textContent = 'Vào bàn rồi chip sẽ được đọc tự động.';
    return;
  }
  const [label, level] = CHIP_STATUS[check.status] || [check.status, 'warn'];
  statusBox.textContent = `${label} · ${check.note || ''}`;
  statusBox.dataset.state = level;

  const lines = [];
  if (check.suggested_chips && check.suggested_chips.length && plan.side) {
    lines.push(`Cần bấm: ${check.suggested_chips.join(' + ')} (${check.suggested_chips.length} chip) = ${fmtMoney(check.expected)}`);
  } else if (check.expected) {
    lines.push(`Lệnh: ${fmtMoney(check.expected)}`);
  }
  if (live && live.ok) {
    const parts = [];
    ['B', 'P', 'T'].forEach((side) => {
      const spot = (live.spots || {})[side] || {};
      if (spot.amount_vnd) {
        const chipText = (spot.chips || []).join(' + ');
        parts.push(`${SIDE_NAME[side]} ${fmtMoney(spot.amount_vnd)}${chipText ? ` [${chipText}]` : ''}`);
      }
    });
    lines.push(`Trên bàn: ${parts.length ? parts.join(' · ') : 'chưa có chip'}`);
    if (live.total !== null && live.total !== undefined) {
      lines.push(`Tổng đặt: ${fmtMoney(live.total)}`);
    }
  } else if (live && live.error) {
    lines.push(`Chưa đọc được chip (${live.error}).`);
  }
  detailBox.innerHTML = lines.map((line) => `<div>${escapeHtml(line)}</div>`).join('');
}

function renderTables(rows) {
  const box = $('#ba-tables');
  const title = $('#ba-table-title');
  if (!box) return;
  const search = state.tableSearch.trim().toLowerCase();
  const list = (rows || []).filter((row) => {
    return !search || String(row.label || '').toLowerCase().includes(search);
  });
  if (title) title.textContent = `Bàn đọc được · ${list.length}`;
  if (!list.length) {
    box.innerHTML = '<p class="hint">Không có bàn nào khớp. Bấm “VÀO BÀN HỢP LỆ” để quét lại sảnh.</p>';
    return;
  }
  box.innerHTML = list
    .map((row) => {
      const tagClass = row.read_error ? 'bad' : row.ready ? 'ok' : 'muted';
      const leadText = row.lead === null || row.lead === undefined
        ? ''
        : ` · B ${row.banker} / P ${row.player} · lệch ${row.lead}`;
      return `<div class="tbl-row ${row.current ? 'current' : ''}">
        <div class="tbl-main">
          <b>${escapeHtml(row.label || row.key)}</b>
          <small>${row.hands} tay${leadText} · mạnh ${escapeHtml(row.strong_name || '—')}${row.current ? ' · đang theo dõi' : ''}</small>
        </div>
        <div class="tbl-meta">
          <span class="tag ${tagClass}">${escapeHtml(row.verdict || '')}</span>
          <small>${escapeHtml(row.status || '')}</small>
        </div>
      </div>`;
    })
    .join('');
}

function renderHistory(history, money) {
  const body = $('#ba-history-body');
  const note = $('#ba-history-note');
  if (!body) return;
  const rows = (history || []).slice().reverse();
  if (note) {
    note.textContent = `${money.hands || 0} tay · ${money.wins || 0} thắng / ${money.losses || 0} thua / `
      + `${money.pushes || 0} hoà · lãi/lỗ ${fmtSigned(money.profit)} · sụt vốn lớn nhất ${fmtMoney(money.drawdown)}`;
  }
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">Chưa có tay nào được ghi.</td></tr>';
    return;
  }
  body.innerHTML = rows
    .map((row) => {
      const cls = row.outcome === 'win' ? 'win' : row.outcome === 'loss' ? 'loss' : row.outcome === 'not_placed' ? 'warn' : '';
      const result = row.result ? `${row.result}` : '—';
      const bet = row.bet ? SIDE_NAME[row.bet] || row.bet : '—';
      return `<tr class="${cls}">
        <td>${escapeHtml(row.at || '')}</td>
        <td>${escapeHtml(row.table || '')}</td>
        <td>${row.hand || ''}</td>
        <td>${escapeHtml(bet)}</td>
        <td>${escapeHtml(result)}</td>
        <td>${row.stake ? fmtMoney(row.stake) : '—'}</td>
        <td>${row.delta ? fmtSigned(row.delta) : '—'}</td>
        <td>${fmtMoney(row.balance_after)}</td>
      </tr>`;
    })
    .join('');
}

function renderBaConfig(config) {
  if (!config) return;
  setValue('#ba-capital-input', config.capital);
  setValue('#ba-tp-input', config.take_profit_pct);
  setValue('#ba-tables-target', config.tables_to_play);
  setValue('#ba-bet-side', config.bet_side);
  setValue('#ba-bet-stake', config.bet_stake);
  setValue('#ba-start-url', config.start_url);
  setValue('#ba-ai-mode', config.ai_mode);
  setValue('#ba-ai-url', config.ai_url);
  setValue('#ba-ai-model', config.ai_model);
  setValue('#ba-ai-every', config.ai_every);
  setValue('#ba-ai-timeout', config.ai_timeout);
  setValue('#ba-live-trials', config.live_trials);
  setValue('#ba-live-markov', config.live_markov_weight);

  const flags = {
    '#ba-auto-hunt': config.auto_hunt,
    '#ba-auto-recover': config.auto_recover,
    '#ba-favorites-only': config.favorites_only,
    '#ba-human-mouse': config.human_mouse,
    '#ba-auto-place': config.auto_place,
  };
  Object.entries(flags).forEach(([selector, value]) => {
    const node = $(selector);
    if (node) node.checked = !!value;
  });

  const window = config.chrome_window || {};
  const full = $('#ba-fullscreen');
  if (full) full.checked = !!window.fullscreen;
  setValue('#ba-win-w', window.width);
  setValue('#ba-win-h', window.height);
  setValue('#ba-win-scale', window.percent);
}

/* --- AI live: số liệu do engine tính, AI chỉ viết nhận định --- */
function renderAiPanel(live) {
  if (!live) return;
  const badge = $('#ba-ai-live-state');
  const verdict = $('#ba-ai-verdict');
  const numbers = $('#ba-ai-numbers');
  const probsBox = $('#ba-ai-probs');
  const risk = $('#ba-ai-risk');
  const explain = $('#ba-ai-explain');
  const meta = $('#ba-ai-meta');

  if (!live.running) {
    if (badge) { badge.textContent = 'CHƯA BẬT'; badge.className = 'hero-state'; }
    if (verdict) verdict.textContent = live.note || 'Bấm “Bật AI live” để đọc bàn Petalive.';
    if (numbers) numbers.textContent = '—';
    if (probsBox) probsBox.innerHTML = '';
    if (risk) risk.textContent = '';
    if (explain) explain.textContent = '';
    if (meta) meta.textContent = '';
    return;
  }

  const pred = live.prediction || {};
  const analysis = live.analysis || {};
  const ai = live.ai || {};
  if (badge) {
    badge.textContent = analysis.verdict ? 'CÓ AI' : (ai.busy ? 'ĐANG HỎI AI' : 'ĐANG ĐỌC');
    badge.className = 'hero-state' + (analysis.verdict ? ' bet' : '');
  }
  if (verdict) verdict.textContent = analysis.verdict || pred.recommendation || 'Chờ ván đầu tiên…';

  const probs = pred.probabilities || {};
  const ev = pred.ev || {};
  if (numbers) {
    if (pred.ok) {
      const bestEv = Math.max(...['P', 'B', 'T'].map((side) => Number(ev[side] || 0)));
      numbers.textContent = `P ${pct((probs.p_player || 0) * 100)} · B ${pct((probs.p_banker || 0) * 100)}`
        + ` · T ${pct((probs.p_tie || 0) * 100)} · EV tốt nhất ${bestEv >= 0 ? '+' : ''}${bestEv.toFixed(4)}`;
    } else {
      numbers.textContent = pred.error || 'Chưa có dự đoán.';
    }
  }
  if (probsBox) {
    const keyOf = { P: 'p_player', B: 'p_banker', T: 'p_tie' };
    probsBox.innerHTML = ['P', 'B', 'T'].map((side) => {
      const value = Number((probs[keyOf[side]] || 0) * 100);
      const width = Math.max(0, Math.min(100, value)).toFixed(1);
      return `<div class="ai-prob-row"><span>${SIDE_NAME[side]}</span>`
        + `<div class="ai-bar"><span style="width:${width}%"></span></div><b>${width}%</b></div>`;
    }).join('');
  }
  if (risk) risk.textContent = analysis.risk ? `Rủi ro: ${analysis.risk}` : '';
  if (explain) explain.textContent = analysis.explanation || '';
  if (meta) {
    const bits = [live.table || '?', `${live.rounds || 0} ván`, ai.model || ''];
    if (analysis.round && analysis.round !== live.rounds) bits.push(`AI ở ván ${analysis.round}`);
    if (analysis.confidence) bits.push(`tin cậy: ${analysis.confidence}`);
    if (ai.error) bits.push(`lỗi AI: ${ai.error}`);
    meta.textContent = bits.filter(Boolean).join(' · ');
  }
}

function renderBaccarat(snapshot) {
  if (!snapshot) return;
  state.baccarat = snapshot;
  const money = snapshot.money || {};
  const table = snapshot.table || {};
  const plan = snapshot.plan || {};
  const config = snapshot.config || {};

  /* --- KPI --- */
  const capitalBox = $('#ba-capital');
  if (capitalBox) {
    capitalBox.textContent = fmtMoney(money.capital);
    capitalBox.className = 'kpi-value';
  }
  setText('#ba-capital-note', `Số dư phiên: ${fmtMoney(money.balance)}`);

  const profitBox = $('#ba-profit');
  if (profitBox) {
    profitBox.textContent = fmtSigned(money.profit);
    profitBox.className = 'kpi-value ' + (money.profit > 0 ? 'green' : money.profit < 0 ? 'red' : '');
  }
  setText('#ba-profit-note', `${money.hands || 0} tay · ${money.wins || 0}T/${money.losses || 0}B/${money.pushes || 0}H`);

  const roiBox = $('#ba-roi');
  if (roiBox) {
    roiBox.textContent = `${money.roi > 0 ? '+' : ''}${pct(money.roi)}`;
    roiBox.className = 'kpi-value ' + (money.roi > 0 ? 'green' : money.roi < 0 ? 'red' : '');
  }
  setText('#ba-roi-note', `sụt vốn ${fmtMoney(money.drawdown)}`);

  setText('#ba-winrate', `${Math.round(money.win_rate || 0)}% / ${Math.round(100 - (money.win_rate || 0))}%`);
  setText('#ba-winrate-note', money.blocked ? String(money.blocked[1] || 'đã chặn') : 'không tính hoà');

  /* --- thanh trạng thái --- */
  const chromeChip = $('#ba-chrome-chip');
  if (chromeChip) {
    chromeChip.textContent = snapshot.chrome_connected ? 'Chrome: đã kết nối' : 'Chrome: chưa kết nối';
    chromeChip.dataset.state = snapshot.chrome_connected ? 'on' : 'off';
  }
  const toolChip = $('#ba-tool-chip');
  if (toolChip) {
    toolChip.textContent = snapshot.tool_running ? 'Tool: đang chạy' : 'Tool: đang dừng';
    toolChip.dataset.state = snapshot.tool_running ? 'on' : 'warn';
  }
  const toolButton = $('#ba-tool');
  if (toolButton) {
    toolButton.textContent = snapshot.tool_running ? 'Dừng tool' : 'Chạy tool';
    toolButton.classList.toggle('danger-btn', !!snapshot.tool_running);
  }
  if (snapshot.chrome_connected) chromeSeen.baccarat = true;
  const led = $('[data-led="baccarat"]');
  if (led) led.classList.toggle('on', !!snapshot.tool_running || !!snapshot.chrome_connected);

  const enter = snapshot.enter_valid || {};
  const enterButton = $('#ba-enter-valid');
  if (enterButton) {
    enterButton.disabled = !!enter.active;
    enterButton.textContent = enter.active ? 'ĐANG TÌM BÀN HỢP LỆ…' : 'VÀO BÀN HỢP LỆ';
  }

  setText('#ba-conn-note', snapshot.chrome_note || '');
  setText('#ba-wallet', snapshot.wallet_text || '');
  setText('#ba-sweep-note', snapshot.sweep_note || '');

  /* --- banner trạng thái vào bàn hợp lệ / tự săn bàn --- */
  const hunt = $('#ba-hunt');
  const cycle = snapshot.cycle || {};
  if (hunt) {
    if (enter.active) {
      hunt.classList.remove('hidden');
      state.huntTick += 1;
      const spin = $('#ba-hunt-spin');
      if (spin) spin.textContent = state.huntTick % 2 ? '●' : '○';
      const remain = Math.max(0, Math.round(enter.remain || 0));
      setText('#ba-hunt-text', `${enter.note || 'Đang tìm bàn hợp lệ…'} · còn ${remain}s`);
      const cancel = $('#ba-hunt-cancel');
      if (cancel) cancel.textContent = 'Huỷ vào bàn';
    } else if (cycle.waiting) {
      hunt.classList.remove('hidden');
      state.huntTick += 1;
      const spin = $('#ba-hunt-spin');
      if (spin) spin.textContent = state.huntTick % 2 ? '●' : '○';
      const remain = Math.max(0, Math.round(cycle.retry_in || 0));
      setText('#ba-hunt-text', remain > 0
        ? `Đang tự săn bàn… nghỉ ${remain}s rồi quét lại (đã thử ${cycle.attempts || 0} lượt).`
        : `Đang tự quét sảnh tìm bàn có mã bàn… (đã thử ${cycle.attempts || 0} lượt)`);
      const cancel = $('#ba-hunt-cancel');
      if (cancel) cancel.textContent = 'Dừng tự quét';
    } else {
      hunt.classList.add('hidden');
    }
  }

  /* --- lệnh tay kế tiếp --- */
  const planState = $('#ba-plan-state');
  const planBox = $('#ba-plan');
  const planReason = $('#ba-plan-reason');
  let reasonText = plan.reason || '';
  if (planState && planBox) {
    if (!table.key) {
      planState.textContent = 'CHƯA CÓ BÀN';
      planState.className = 'hero-state';
      planBox.textContent = 'Bấm “VÀO BÀN HỢP LỆ” để mở bàn đầu tiên có mã bàn.';
      reasonText = '';
    } else if (money.blocked) {
      planState.textContent = 'ĐÃ CHẶN';
      planState.className = 'hero-state stop';
      planBox.textContent = String(money.blocked[1] || 'Phiên đã chạm ngưỡng dừng.');
    } else if (plan.action === 'bet') {
      planState.textContent = plan.armed ? 'ĐÃ CHỐT' : 'SẴN SÀNG';
      planState.className = 'hero-state bet';
      const source = plan.source === 'AUTO' ? 'AI/predict' : (SIDE_NAME[plan.source] || plan.source || 'UI');
      planBox.textContent = `${plan.side_name || SIDE_NAME[plan.side] || '—'} ${fmtMoney(plan.stake)} · ${source}`;
      if (plan.verdict) reasonText = `${plan.verdict} · ${reasonText}`;
    } else if (plan.playable === false) {
      planState.textContent = 'BỎ TAY';
      planState.className = 'hero-state stop';
      planBox.textContent = 'predict chốt bỏ tay (EV âm/nhiễu). Chọn P/B/T nếu vẫn muốn đặt.';
    } else {
      planState.textContent = 'CHỜ';
      planState.className = 'hero-state';
      planBox.textContent = table.running
        ? 'Chọn cửa + chip rồi bấm “Tự đặt cược”.' : 'Chưa theo dõi bàn.';
    }
  }
  if (planReason) planReason.textContent = reasonText;

  /* --- bàn đang theo dõi --- */
  setText('#ba-table-name', table.key ? `${table.name || table.key} · ${table.hands || 0} tay` : 'Chưa chọn bàn');
  renderRoad(table.letters);
  const noteBits = [];
  if (table.key) {
    noteBits.push(table.running ? 'Đang theo dõi' : 'Đã dừng');
    if (table.entry_waiting) noteBits.push('chờ kết quả tay hiện tại');
    if (table.place_skips) noteBits.push(`bỏ ${table.place_skips} tay`);
  } else {
    noteBits.push('Chưa vào bàn — bấm “VÀO BÀN HỢP LỆ”.');
  }
  setText('#ba-table-note', [table.note, noteBits.join(' · ')].filter(Boolean).join(' — '));

  renderChips(snapshot.chips, plan);
  const place = snapshot.place || {};
  const placeBox = $('#ba-place-status');
  if (placeBox) {
    placeBox.textContent = place.note || '';
    placeBox.dataset.state = place.ok === true ? 'on' : place.ok === false ? 'err' : '';
  }

  renderTables(snapshot.tables || []);
  renderBaConfig(config);
  renderAiPanel(snapshot.live);
  renderHistory(snapshot.history || [], money);
}

/* ============================== sự kiện chung ============================== */
api.onEngineEvent(({ game, event, data }) => {
  if (event === 'state') {
    renderBaccarat(data);
    return;
  }
  if (event === 'live_prediction') {
    renderAiPanel(data);
    return;
  }
  if (event === 'log') {
    appendLog(game, data.level || 'info', data.text || '');
    return;
  }
  if (event === 'ready') {
    appendLog(game, 'info', `engine ${data.game} sẵn sàng · CDP ${data.cdp_port}`);
  }
});

document.addEventListener('click', (event) => {
  const winButton = event.target.closest('[data-win]');
  if (winButton) api.windowControl(winButton.dataset.win);
});

$$('.game').forEach((button) => button.addEventListener('click', () => switchGame(button.dataset.game)));

$$('[data-subtabs]').forEach((nav) => {
  const game = nav.dataset.subtabs;
  $$('button', nav).forEach((button) => button.addEventListener('click', () => switchSub(game, button.dataset.sub)));
});

/* ------------------------------ BACCARAT actions ------------------------------ */
$('#ba-enter-valid').addEventListener('click', async () => {
  const reply = await call('baccarat', 'table.enterValid');
  if (reply) toast(reply.note || 'Đang tìm bàn hợp lệ…');
});

$('#ba-tool').addEventListener('click', async () => {
  const running = state.baccarat && state.baccarat.tool_running;
  const reply = await call('baccarat', running ? 'tool.stop' : 'tool.start');
  if (reply) toast(reply.note || (running ? 'Đã dừng tool.' : 'Tool đang chạy.'));
});

$('#ba-hunt-cancel').addEventListener('click', async () => {
  if (state.baccarat && (state.baccarat.enter_valid || {}).active) {
    const reply = await call('baccarat', 'table.cancelEnter');
    if (reply) toast(reply.note || 'Đã huỷ vào bàn.');
    return;
  }
  await call('baccarat', 'config.set', { auto_hunt: false });
  toast('Đã dừng tự săn bàn.');
});

$('#ba-search').addEventListener('input', (event) => {
  state.tableSearch = event.target.value || '';
  renderTables((state.baccarat && state.baccarat.tables) || []);
});

$('#ba-save-money').addEventListener('click', async () => {
  const reply = await call('baccarat', 'config.set', {
    capital: Number($('#ba-capital-input').value),
    take_profit_pct: Number($('#ba-tp-input').value),
  });
  if (reply) toast(reply.note || 'Đã lưu vốn.');
});

$('#ba-new-session').addEventListener('click', async () => {
  const reply = await call('baccarat', 'session.new', {
    capital: Number($('#ba-capital-input').value),
    take_profit_pct: Number($('#ba-tp-input').value),
  });
  if (reply) toast(reply.note || 'Đã tạo phiên mới.');
});

$('#ba-reset-session').addEventListener('click', async () => {
  const reply = await call('baccarat', 'session.reset');
  if (reply) toast(reply.note || 'Đã reset phiên.');
});

function betFormParams() {
  return {
    side: $('#ba-bet-side').value,
    stake: Number($('#ba-bet-stake').value),
  };
}

$('#ba-bet-save').addEventListener('click', async () => {
  const reply = await call('baccarat', 'bet.config', betFormParams());
  if (reply) toast(reply.note || 'Đã lưu lệnh cược.');
});

$('#ba-bet-place').addEventListener('click', async () => {
  const saved = await call('baccarat', 'bet.config', betFormParams());
  if (!saved) return;
  const reply = await call('baccarat', 'bet.place');
  if (reply) toast(reply.note || 'Đã gửi lệnh cược.');
});

$('#ba-save-options').addEventListener('click', async () => {
  const reply = await call('baccarat', 'config.set', {
    auto_hunt: $('#ba-auto-hunt').checked,
    auto_recover: $('#ba-auto-recover').checked,
    favorites_only: $('#ba-favorites-only').checked,
    human_mouse: $('#ba-human-mouse').checked,
    auto_place: $('#ba-auto-place').checked,
    tables_to_play: Number($('#ba-tables-target').value),
  });
  if (reply) toast(reply.note || 'Đã lưu tùy chọn.');
});

$('#ba-save-window').addEventListener('click', async () => {
  const reply = await call('baccarat', 'chrome.window', {
    width: Number($('#ba-win-w').value),
    height: Number($('#ba-win-h').value),
    percent: Number($('#ba-win-scale').value),
    fullscreen: $('#ba-fullscreen').checked,
  });
  if (reply) toast(reply.note || 'Đã lưu cửa sổ Chrome.');
});

$('#ba-save-url').addEventListener('click', async () => {
  const reply = await call('baccarat', 'config.set', { start_url: $('#ba-start-url').value.trim() });
  if (reply) toast(reply.note || 'Đã lưu link.');
});

$('#ba-export').addEventListener('click', async () => {
  const snap = state.baccarat;
  const rows = (snap && snap.history) || [];
  if (!rows.length) {
    toast('Chưa có dữ liệu để xuất.');
    return;
  }
  const head = ['time', 'table', 'hand', 'bet', 'result', 'stake', 'delta', 'outcome', 'balance_after'];
  const csv = [head.join(',')]
    .concat(rows.map((row) => head.map((key) => {
      const value = row[key === 'time' ? 'at' : key];
      const text = value === null || value === undefined ? '' : String(value);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    }).join(',')))
    .join('\n');
  const name = `aibcr-baccarat-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.csv`;
  const saved = await api.saveText(name, csv);
  if (saved && saved.ok) toast(`Đã lưu: ${saved.path}`);
  else if (saved && saved.error) toast(saved.error);
});

$('#ba-clear-log').addEventListener('click', () => {
  state.logs.baccarat = [];
  renderLog('baccarat');
});

/* ------------------------------ AI live actions ------------------------------ */
$('#ba-live-start').addEventListener('click', async () => {
  const reply = await call('baccarat', 'live.start');
  if (reply) toast(reply.note || 'Đã bật AI live.');
});

$('#ba-live-stop').addEventListener('click', async () => {
  const reply = await call('baccarat', 'live.stop');
  if (reply) toast(reply.note || 'Đã tắt AI live.');
});

$('#ba-live-analyze').addEventListener('click', async () => {
  const reply = await call('baccarat', 'live.analyze');
  if (reply && reply.ok) toast('Đã phân tích xong.');
});

$('#ba-ai-save').addEventListener('click', async () => {
  const reply = await call('baccarat', 'config.set', {
    ai_mode: $('#ba-ai-mode').value,
    ai_url: $('#ba-ai-url').value.trim(),
    ai_model: $('#ba-ai-model').value.trim(),
    ai_every: Number($('#ba-ai-every').value),
    ai_timeout: Number($('#ba-ai-timeout').value),
    live_trials: Number($('#ba-live-trials').value),
    live_markov_weight: Number($('#ba-live-markov').value),
  });
  if (reply) toast(reply.note || 'Đã lưu cấu hình AI.');
});

$('#ba-ai-key-save').addEventListener('click', async () => {
  const key = $('#ba-ai-key').value.trim();
  const reply = await call('baccarat', 'ai.key.set', { key });
  if (reply) {
    $('#ba-ai-key').value = '';
    toast(reply.note || 'Đã lưu key trong phiên này.');
  }
});

$('#ba-ai-key-clear').addEventListener('click', async () => {
  $('#ba-ai-key').value = '';
  const reply = await call('baccarat', 'ai.key.set', { key: '' });
  if (reply) toast(reply.note || 'Đã xoá key tuỳ biến.');
});

/* ---------------------------------- khởi động ---------------------------------- */
(async function boot() {
  renderLog('baccarat');
  const info = await api.info();
  $('#app-ver').textContent = `v${info.version}`;
  switchGame('baccarat');
  const ba = await call('baccarat', 'state');
  if (ba) renderBaccarat(ba);
})();
