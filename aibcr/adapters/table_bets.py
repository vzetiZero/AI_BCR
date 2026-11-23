# -*- coding: utf-8 -*-
"""Đọc tiền cược đang đặt thật trên bàn (chip) qua DOM/CDP, CHỈ ĐỌC.

Markup Evolution Baccarat đã khảo sát trên bàn thật:
    [data-role="bet-spot-Banker"|"bet-spot-Player"|"bet-spot-Tie"]
        -> [data-role="chip"][data-value="100"]   (chip đã đặt vào cửa)
    [data-role="total-bet-label"]   -> "TỔNG ĐẶT CƯỢC 100 VN2"
    [data-role="selected-chip"]     -> mệnh giá chip đang chọn ở khay
    [data-role="chip-stack"]        -> khay chip (20/40/100/500/2000/10k)

Chip ẩn (0×0) và chip trong khay bị loại bằng kích thước thật. Không bấm, không đặt cược,
không đọc cookie/token; chuỗi nối CDP chỉ dùng để trả giá trị DOM.

Toàn bộ đường đặt chip bằng chuột đã bị xoá: đặt cược chỉ đi qua WebSocket của sảnh WM
(``wm_bet.WMBet.place`` → ``wm_ws``). EVO chỉ còn đọc bảng cầu và đọc số chip đang trên bàn.
"""

import json
import time

from aibcr.adapters import browser as BR
# Chia chip la logic thuan (Model); adapter chi re-export de giu tuong thich.
from aibcr.models.chips import (  # noqa: F401
    DEFAULT_CHIP_UNIT,
    DEFAULT_DENOMINATIONS,
    decompose,
)

SIDES = ("B", "P", "T")
SPOT_ROLE = (("Banker", "B"), ("Player", "P"), ("Tie", "T"))
# 1 đơn vị chip = 1 đơn vị hiển thị trên sàn (VN2). Chip "100" = 100, "20" = 20.
# Không quy đổi sang VNĐ nữa để UI, khay chip và số dư luôn cùng một thang số.
# Mệnh giá khay chip theo đơn vị hiển thị; khay thật luôn được đọc từ DOM và thay thế.

READ_JS = r"""(() => {
  const txt = (el) => (el ? (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const digits = (s) => {
    if (s === null || s === undefined) return null;
    const m = String(s).replace(/[⁦⁩]/g, '').match(/\d[\d.,]*/);
    return m ? parseInt(m[0].replace(/[^\d]/g, ''), 10) : null;
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 4 && r.height > 4 && st.display !== 'none' && st.visibility !== 'hidden';
  };
  const tray = (() => {
    const named = document.querySelector('[data-role="chip-stack"]');
    if (named) return named;
    const chips = [...document.querySelectorAll('[data-role="chip"][data-value]')]
      .filter((el) => visible(el) && !el.closest('[data-role^="bet-spot-"]')
        && (parseInt(el.getAttribute('data-value'), 10) || 0) > 0);
    if (!chips.length) return null;
    let node = chips[0];
    for (let i = 0; i < 8 && node && node !== document.body; i++) {
      const count = [...node.querySelectorAll('[data-role="chip"][data-value]')]
        .filter((el) => visible(el) && !el.closest('[data-role^="bet-spot-"]')).length;
      if (count >= chips.length) return node;
      node = node.parentElement;
    }
    return chips[0].parentElement;
  })();
  // Nhận diện bàn bằng KHU VỰC CƯỢC, không chỉ bằng cầu: khay chip + ô cược đã hiện
  // nhưng Bead-road chưa render/đang ẩn vẫn là bàn. Đòi mỗi Bead-road thì app báo
  // "no_table_view" mãi dù người dùng đã thấy khay chip.
  // Ô cược + khay chỉ có trong màn hình bàn, ô bàn ở sảnh không có nên không nhận nhầm.
  const hasTable = !!(document.querySelector('[data-role="Bead-road"]')
    || (tray && document.querySelector('[data-role="bet-spot-Banker"]')));
  const totalEl = document.querySelector('[data-role="total-bet-label"]');

  const spots = {};
  for (const [role, side] of [['Banker', 'B'], ['Player', 'P'], ['Tie', 'T']]) {
    const spot = document.querySelector('[data-role="bet-spot-' + role + '"]');
    if (!spot) { spots[side] = null; continue; }
    const chips = [...spot.querySelectorAll('[data-role="chip"]')]
      .filter(visible)
      .map((c) => parseInt(c.getAttribute('data-value'), 10))
      .filter((v) => Number.isInteger(v) && v > 0);
    spots[side] = {amount: chips.reduce((s, v) => s + v, 0), chips};
  }

  // Mệnh giá thực có trên khay chip của bàn này.
  const denominations = tray
    ? [...new Set([...tray.querySelectorAll('[data-role="chip"]')]
        .filter(visible)
        .map((c) => parseInt(c.getAttribute('data-value'), 10))
        .filter((v) => Number.isInteger(v) && v > 0))].sort((a, b) => a - b)
    : [];

  // Tổng tiền tính từ CHIP THẬT đã đặt; nhãn "TỔNG ĐẶT CƯỢC" có thể trễ một nhịp so với chip.
  const spots_total = Object.values(spots).reduce((sum, s) => sum + ((s && s.amount) || 0), 0);
  const selectedChip = (() => {
    // Chip được đánh dấu trong khay đổi ngay khi bấm, nhãn [data-role="selected-chip"]
    // trễ hơn một nhịp. Chỉ tìm TRONG KHAY — chip preview của nhãn và chip đã đặt trên ô
    // cược cũng mang data-role="chip", đếm chung sẽ ra 2 phần tử "đang chọn" và rơi
    // xuống nhãn trễ, báo nhầm mệnh giá.
    const marked = [...(tray || document).querySelectorAll('[data-role="chip"][data-selected="true"]')]
      .map((el) => parseInt(el.getAttribute('data-value'), 10))
      .filter((v) => Number.isInteger(v) && v > 0);
    if (marked.length === 1) return marked[0];
    const wrap = document.querySelector('[data-role="selected-chip"]');
    if (!wrap) return null;
    const inner = wrap.querySelector('[data-role="chip"][data-value]');
    if (inner) {
      const v = parseInt(inner.getAttribute('data-value'), 10);
      if (Number.isInteger(v)) return v;
    }
    return digits(txt(wrap));
  })();

  return {
    has_table: hasTable,
    total_raw: totalEl ? txt(totalEl) : null,
    total_label: totalEl ? digits(txt(totalEl)) : null,
    total_units: spots_total,
    selected_chip: selectedChip,
    spots,
    denominations,
  };
})()"""


def read(session, timeout=None, unit=DEFAULT_CHIP_UNIT):
    """Đọc chip đang đặt trên bàn đang mở và quy đổi về VNĐ.

    Số in trên chip chính là số tiền VN2 hiển thị trên sàn (chip 20 = 20), nên ``unit``
    mặc định 1 và mọi giá trị trả ra ngoài ``raw_*`` đều đã nhân ``unit``. Trả dict:
        {ok, error, total, total_units, selected_chip, selected_chip_vnd,
         spots:{B:{amount,amount_vnd,chips,chips_vnd}}, denominations, unit, ts}
    ok=False khi chưa ở trong bàn hoặc CDP không đọc được; không ném lỗi ra UI.
    """
    out = {"ok": False, "error": "", "total": None, "total_units": None,
           "total_label": None, "total_label_units": None,
           "selected_chip": None, "selected_chip_vnd": None,
           "spots": {}, "denominations": [], "unit": int(unit or DEFAULT_CHIP_UNIT),
           "ts": time.time()}
    ws = None
    try:
        page = session._cdp_page()
        if page is None:
            out["error"] = "no_cdp_page"
            return out
        import websocket
        ws = websocket.create_connection(
            BR.validated_endpoint(page), suppress_origin=True, timeout=timeout or BR.SCRIPT_TIMEOUT,
            redirect_limit=0, http_no_proxy=["127.0.0.1", "localhost", "::1"])
        seq = 0
        contexts = []

        def command(method, params=None):
            nonlocal seq
            seq += 1
            ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
            deadline = time.monotonic() + BR.SCRIPT_TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("cdp_timeout")
                ws.settimeout(remaining)
                message = json.loads(ws.recv())
                if message.get("method") == "Runtime.executionContextCreated":
                    context = message.get("params", {}).get("context", {})
                    if (context.get("auxData") or {}).get("isDefault"):
                        contexts.append(context["id"])
                if message.get("id") == seq:
                    if "error" in message:
                        raise RuntimeError("cdp_command_failed")
                    return message.get("result", {})

        command("Runtime.enable")
        # Chờ context chính xuất hiện (tab vừa mở cần một nhịp).
        deadline = time.monotonic() + 6
        while not contexts and time.monotonic() < deadline:
            command("Runtime.evaluate", {"expression": "1", "returnByValue": True})
        best = None
        for context_id in list(contexts):
            try:
                value = command("Runtime.evaluate", {
                    "expression": READ_JS, "contextId": context_id,
                    "returnByValue": True, "silent": True,
                    "timeout": BR.SCRIPT_TIMEOUT * 1000}).get("result", {}).get("value")
            except Exception:
                continue
            if isinstance(value, dict) and value.get("has_table"):
                best = value
                break
        if best is None:
            out["error"] = "no_table_view"
            return out

        unit = max(1, int(unit or DEFAULT_CHIP_UNIT))
        spots = {}
        for side in SIDES:
            entry = (best.get("spots") or {}).get(side)
            if isinstance(entry, dict):
                chips = [int(v) for v in (entry.get("chips") or [])]
                units = int(entry.get("amount") or sum(chips))
            else:
                chips, units = [], 0
            spots[side] = {"amount": units, "amount_vnd": float(units * unit),
                           "chips": chips, "chips_vnd": [float(c * unit) for c in chips]}
        # Tổng lấy từ chip thật; nhãn chỉ để đối chiếu vì có thể trễ một nhịp.
        total_units = int(best.get("total_units") or sum(s["amount"] for s in spots.values()))
        label_units = best.get("total_label")
        selected = best.get("selected_chip")
        out.update(
            ok=True,
            total_units=total_units,
            total=float(total_units * unit),
            total_label_units=label_units,
            total_label=(float(label_units * unit) if label_units is not None else None),
            selected_chip=selected,
            selected_chip_vnd=(float(selected * unit) if selected is not None else None),
            spots=spots,
            denominations=([int(v) for v in (best.get("denominations") or [])]
                           or list(DEFAULT_DENOMINATIONS)),
        )
        return out
    except Exception as exc:
        out["error"] = type(exc).__name__
        return out
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
