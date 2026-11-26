# -*- coding: utf-8 -*-
"""Đặt cược trên bàn WM (M88) — CHỈ qua WebSocket (protocol 22), không bấm chuột.

Bàn WM đặt cược bằng lệnh ``protocol 22`` gửi trên chính socket của bàn::

    {"protocol": 22, "data": {"betSerialNumber": 1, "gameNo": ..., "gameNoRound": ...,
                              "betArr": [{"betArea": 2, "addBetMoney": 10}],
                              "commission": 0, "remark": "1,0"}}

Nhờ vậy không cần khay chip, ô cược hay toạ độ: app chỉ cần giữ được instance
``WebSocket`` của trang (``wm_ws.WMSocket``) rồi gửi lệnh, sau đó xác minh bằng số chip
thật đang treo trên bàn.

Thang tiền: khay chip WM in ``10 / 20 / 50 / 100 / 500 / 1k`` theo đơn vị nghìn đồng
(VNDK), nên **1 đơn vị sảnh = 1.000đ**: chip ``10`` = 10.000đ, ``1k`` = 1.000.000đ.
Số gửi lên sàn đúng bằng số trên khay (``addBetMoney: 10`` cho chip 10), nên không cần
nhân quy đổi — chỉ cần chia tiền theo đúng mệnh giá đang có.

Toàn bộ đường bấm chip bằng chuột đã bị xoá hẳn. Không có socket thì ``place`` trả lỗi
rõ ràng chứ không rơi về mô phỏng chuột.
"""

import json
import time

from aibcr.adapters import browser as BR
from aibcr.models import chips as CHIPS

# Mã ô cược trên WebSocket (protocol 22: ``betArea``) — đã đối chiếu với lệnh thật.
BET_AREA = {"B": 1, "P": 2, "T": 3}
SIDE_NAME = {"B": "Banker", "P": "Player", "T": "Hoà"}
# Một đơn vị sảnh = 1.000đ (khay WM in theo VNDK).
DONG_PER_UNIT = 1000.0

# Đọc trạng thái bàn (khay chip, ô cược, số tiền đang treo, số dư, pha cược). Kích thước
# (``w``/``h``) và phần tử nằm trên tâm được trả về để biết khu vực cược đã render và
# không bị lớp kết quả che — chỉ để ĐỌC, không dùng để bấm.
# Bàn có thể nằm trong iframe_101 (bản nhiều khung) hoặc ngay trong khung gốc (bản
# một-document), nên chọn document có khay chip thay vì chỉ tìm iframe_101.
READ_JS = r"""(() => {
    const candidates = [];
    const push = (doc, inside) => {
        if (!doc) return;
        let box = null;
        try { box = doc.getElementById('chip_box') || doc.getElementById('container'); }
        catch (e) { return; }
        if (!box) return;
        // Ưu tiên document có khay chip thật (bàn đang vẽ), rồi mới tới #container.
        const score = (doc.getElementById('chip_box') ? 2 : 0)
                    + (doc.getElementById('playbetboxBanker') ? 1 : 0);
        candidates.push({doc: doc, inside: inside, score: score});
    };
    for (const id of ['iframe_101']) {
        const frame = document.getElementById(id);
        if (!frame) continue;
        try { push(frame.contentDocument, true); } catch (e) {}
    }
    for (const frame of document.querySelectorAll('iframe')) {
        try { push(frame.contentDocument, true); } catch (e) {}
    }
    push(document, false);
    if (!candidates.length) return {reason: 'no_table_frame'};
    candidates.sort((a, b) => b.score - a.score);
    const picked = candidates[0];
    const doc = picked.doc;
    const box = (el) => {
        const r = el.getBoundingClientRect();
        const hit = doc.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        return {w: r.width, h: r.height,
                hit: hit ? (hit.id || '') + '|' + (hit.className || '').toString().slice(0, 40) : ''};
    };
    const chips = [];
    for (const item of doc.querySelectorAll('#chip_box .chip_item')) {
        const cls = (item.className || '').toString();
        const m = /chip_b_([0-9a-z]+)/.exec(cls);
        if (!m) continue;
        const info = box(item);
        if (info.w < 4) continue;
        chips.push(Object.assign({key: m[1], on: item.classList.contains('on')}, info));
    }
    const spots = {};
    for (const side of ['B', 'P', 'T']) {
        const id = side === 'B' ? 'Banker' : (side === 'P' ? 'Player' : 'Tie');
        const el = doc.getElementById(id);
        if (!el) continue;
        const holder = doc.getElementById('playbetbox' + id);
        const money = holder ? holder.querySelector('.mybet_money') : null;
        const value = money ? parseFloat((money.textContent || '').replace(/[^0-9.]/g, '')) : 0;
        spots[side] = Object.assign({my_bet: Number.isFinite(value) ? value : 0,
                                     chips: holder ? holder.children.length : 0},
                                    box(el));
    }
    // Ví hiển thị ở khung gốc (``#menu_ver2_balance``) hoặc trong khung bàn
    // (``#userBalanceBox`` in "VNDK 80.84"); thử cả hai, chỉ để đọc.
    const walletEl = doc.getElementById('menu_ver2_balance')
        || document.getElementById('menu_ver2_balance')
        || doc.getElementById('userBalanceBox');
    const countdown = doc.getElementById('countdown');
    return {
        chips, spots, in_iframe: picked.inside,
        open_result: !!doc.querySelector('#opencard-wrapper.wrapper.on'),
        countdown: countdown ? (countdown.className || '').toString() : '',
        wallet: walletEl ? (walletEl.textContent || '').replace(/\s+/g, ' ').trim() : ''
    };
})()"""

# Bấm nút huỷ chip trong đúng document đang vẽ bàn (iframe hoặc một-document).
CANCEL_JS = r"""(() => {
    const docs = [document];
    const frame = document.getElementById('iframe_101');
    if (frame) { try { if (frame.contentDocument) docs.unshift(frame.contentDocument); } catch (e) {} }
    for (const f of document.querySelectorAll('iframe')) {
        try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {}
    }
    for (const doc of docs) {
        const btn = doc.getElementById('cancel_btn') || doc.querySelector('#cancel_btn, .cancel_btn');
        if (!btn) continue;
        btn.click();
        return {ok: true};
    }
    return {ok: false, reason: 'cancel_missing'};
})()"""


def chip_value(key):
    """Đổi mã mệnh giá của khay ('10', '1k') sang số đơn vị sảnh."""
    text = str(key or "").strip().lower()
    if text.endswith("k"):
        try:
            return int(round(float(text[:-1]) * 1000))
        except ValueError:
            return 0
    try:
        return int(round(float(text)))
    except ValueError:
        return 0


def parse_denominations(chips):
    """Danh sách mệnh giá (đơn vị sảnh) từ khay đọc được."""
    values = []
    for chip in chips or []:
        value = chip_value((chip or {}).get("key"))
        if value > 0 and value not in values:
            values.append(value)
    return sorted(values)


def box_clickable(box, min_size=4):
    """Hộp đã render đủ lớn (dùng để biết khu vực cược/bàn đã sẵn sàng)."""
    if not isinstance(box, dict):
        return False
    return (box.get("w") or 0) >= min_size and (box.get("h") or 0) >= min_size


def uncovered(box):
    """Phần tử không bị lớp phủ (kết quả chia bài, tooltip) che ở tâm hộp."""
    if not isinstance(box, dict):
        return False
    hit = str(box.get("hit") or "")
    return "chip_item" in hit or "playbetbox" in hit or "table_item" in hit


class WMBet:
    """Đọc khay chip/ô cược và đặt chip trên bàn WM chỉ bằng WebSocket."""

    def __init__(self, session=None):
        self.session = session or BR.BrowserSession()
        self.last_error = ""
        self.last_state = {}

    # ------------------------------------------------------------------ kết nối
    def _command_factory(self, ws):
        state = {"seq": 0}

        def command(method, params=None, timeout=BR.SCRIPT_TIMEOUT):
            state["seq"] += 1
            seq = state["seq"]
            ws.settimeout(timeout)
            ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
            deadline = time.monotonic() + timeout
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError(method)
                message = json.loads(ws.recv())
                if message.get("id") == seq:
                    if message.get("error"):
                        raise RuntimeError(str(message["error"])[:120])
                    return message.get("result", {})

        return command

    def read(self):
        """Đọc trạng thái bàn (khay chip, ô cược, số tiền đang treo, nút bấm).

        Ưu tiên kết nối CDP dùng chung của ``WMSocket``: ``wait_phase`` gọi hàm này vài
        lần mỗi giây, mở/đóng kết nối mới liên tục sẽ làm tab chậm và timeout.
        """
        value = None
        socket = getattr(self.session, "wm_socket", None)
        if socket is not None and getattr(socket, "_command", None) is not None:
            try:
                value = socket.evaluate(READ_JS)
            except Exception:
                value = None
        if value is None:
            value = self._read_once()
            if value is None:
                return {"ok": False, "error": self.last_error}
        if not isinstance(value, dict) or value.get("reason"):
            self.last_error = "Bàn WM chưa sẵn sàng (%s)." % (
                (value or {}).get("reason", "no_value"))
            return {"ok": False, "error": self.last_error}
        value["ok"] = True
        value["denominations"] = parse_denominations(value.get("chips"))
        value["my_bet"] = sum((spot or {}).get("my_bet") or 0
                              for spot in (value.get("spots") or {}).values())
        self.last_state = value
        self.last_error = ""
        return value

    def _read_once(self):
        """Đọc trạng thái qua một kết nối CDP riêng (dự phòng khi chưa có WMSocket).

        WM nhúng KHÁC ORIGIN không đọc được từ tab cổng (``iframe.contentDocument`` bị
        chặn), nên khi tab không thấy bàn thì thử gắn thẳng vào target iframe WM.
        """
        import websocket

        value = None
        page = self.session._cdp_page()
        if page is None:
            self.last_error = "Không có tab WM trong Chrome."
        else:
            ws = None
            try:
                ws = websocket.create_connection(
                    BR.validated_endpoint(page), suppress_origin=True,
                    timeout=BR.SCRIPT_TIMEOUT, redirect_limit=0,
                    http_no_proxy=["127.0.0.1", "localhost", "::1"])
                command = self._command_factory(ws)
                command("Runtime.enable")
                value = command("Runtime.evaluate", {
                    "expression": READ_JS, "returnByValue": True, "silent": True,
                }).get("result", {}).get("value")
            except Exception as exc:
                self.last_error = "Không đọc được bàn WM (%s)." % type(exc).__name__
                value = None
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
        if isinstance(value, dict) and not value.get("reason"):
            return value
        if not str(getattr(self.session, "wm_oopif_target", "") or ""):
            return value
        from aibcr.adapters import wm_targets as WT
        try:
            socket_ = WT.open_frame_socket(BR.CDP_PORT, timeout=BR.SCRIPT_TIMEOUT)
        except Exception:
            return value
        try:
            socket_.command("Runtime.enable")
            inner = socket_.evaluate(READ_JS)
            return inner if isinstance(inner, dict) else value
        except Exception:
            return value
        finally:
            socket_.close()

    # ------------------------------------------------------------------ pha cược
    def wait_phase(self, deadline_seconds=40.0, poll=0.25):
        """Chờ bàn mở cửa cược: khay chip hiển thị (không bị lớp kết quả che).

        Đây là điều kiện thật của WM — chính client cũng chỉ gửi được khi lớp phủ kết quả
        đã tan. Dùng chung cho cả WebSocket: gửi lệnh sớm sẽ bị server trả ``bOk: false``.
        Không dựa vào class ``#opencard-wrapper`` vì nó có thể "on" suốt ván.
        """
        deadline = time.monotonic() + max(1.0, float(deadline_seconds))
        while time.monotonic() < deadline:
            state = self.read()
            if state.get("ok"):
                chips = [chip for chip in state.get("chips") or [] if box_clickable(chip)]
                if chips and any(uncovered(chip) for chip in chips):
                    return state
            time.sleep(poll)
        return {}

    # ------------------------------------------------------------------ đặt cược
    def place(self, side, units, confirm=True, max_chips=20, wait_seconds=40.0,
              allow_short=False):
        """Đặt ``units`` (đơn vị sảnh, 1 đv = 1.000đ) vào cửa ``side`` (B/P/T).

        Trả dict ``{"ok", "placed", "total", "denominations", "error"}``.

        Lệnh luôn đi qua WebSocket (``_place_ws``). ``confirm=False`` (chế độ "treo
        chip chờ xác nhận" của đường chuột cũ) bị từ chối vì không còn tồn tại.

        ``allow_short`` = chế độ ALL-IN: số tiền không chia khít được bằng mệnh giá
        khay (ví dụ còn 636 mà khay chỉ có 10/20/50/100/500/1k/5k/10k) thì đặt mức lớn
        nhất ghép được (630 = 500+100+20+10) thay vì bỏ tay. Chỉ bật khi lệnh bị chặn
        bởi số dư cuối cùng — đặt đúng/đủ vẫn là mặc định.
        """
        side = str(side or "").upper()[:1]
        if side not in BET_AREA:
            return {"ok": False, "error": "Cửa cược không hợp lệ: %r" % (side,)}
        try:
            target = int(round(float(units)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Số tiền không hợp lệ: %r" % (units,)}
        if target <= 0:
            return {"ok": False, "error": "Số tiền phải lớn hơn 0."}
        if not confirm:
            return {"ok": False,
                    "error": "Chỉ hỗ trợ đặt cược qua WebSocket (WM) — không còn chế độ "
                             "treo chip chờ xác nhận."}
        all_in_note = ""

        # Chờ đúng cửa sổ cược trước khi gửi lệnh (server từ chối khi cửa đã đóng).
        phase = self.wait_phase(wait_seconds)
        if not phase:
            return {"ok": False, "error": "Bàn chưa mở cược trong %d giây." % int(wait_seconds)}

        denominations = parse_denominations(phase.get("chips"))
        plan = CHIPS.decompose(target, unit=1, denominations=denominations)
        if (not plan.get("exact") or not plan.get("chips")) and allow_short:
            # ALL-IN: không chia khít (ví dụ 636) → đặt mức lớn nhất ghép được (630).
            short = CHIPS.affordable(target, unit=1, denominations=denominations)
            if short > 0:
                target = short
                plan = CHIPS.decompose(target, unit=1, denominations=denominations)
                all_in_note = (" · all-in %d đv (số dư không chia khít mệnh giá)" % target)
        if not plan.get("exact") or not plan.get("chips"):
            # Không gửi số lẻ khay không có: giữ nguyên tắc "không bao giờ đặt thiếu".
            return {"ok": False, "denominations": denominations,
                    "error": "Không chia được %d đv theo mệnh giá %s (còn %s đv)."
                             % (target, denominations, plan.get("remainder_vnd"))}
        if len(plan["chips"]) > max_chips:
            return {"ok": False, "denominations": denominations,
                    "error": "Cần %d mệnh giá (> %d) — dừng cho an toàn."
                             % (len(plan["chips"]), max_chips)}
        return self._place_ws(side, target, denominations, all_in_note)

    def _place_ws(self, side, target, denominations=None, all_in_note=""):
        """Gửi lệnh cược qua WebSocket; không có socket thì trả lỗi rõ ràng.

        Không còn đường bấm chuột: nếu socket của bàn chưa bắt được thì báo đúng lý do để
        người dùng mở lại bàn/thử lại, thay vì âm thầm chuyển sang mô phỏng chuột.
        """
        from aibcr.adapters.wm_ws import get_socket

        denominations = list(denominations or [])
        socket = get_socket(self.session, start=True)
        if socket is None:
            return {"ok": False, "denominations": denominations, "via": "ws",
                    "error": "Không mở được kết nối WebSocket của bàn WM — đặt cược chỉ "
                             "hỗ trợ qua WebSocket. Kiểm tra Chrome còn mở (CDP 9222)."}
        if not socket.ready():
            # Thử cứu ngay tại chỗ: bàn mở trước khi hook chạy là tình huống thường gặp
            # (người dùng vào bàn rồi mới bấm Chạy tool). Không tải lại trang.
            try:
                socket.adopt_existing()
            except Exception:
                pass
        waited = False
        if not socket.ready() and hasattr(socket, "wait_ready"):
            waited = bool(socket.wait_ready(6.0))
        if not socket.ready() and not waited:
            return {"ok": False, "denominations": denominations, "via": "ws",
                    "error": "Chưa bắt được WebSocket của bàn WM — đặt cược chỉ hỗ trợ "
                             "qua WebSocket. Kiểm tra bằng “python "
                             "tools/kiem_tra_socket_wm.py” rồi đổi sang bàn khác nếu "
                             "socket của bàn này không nhận được."}
        current = self.read_bets()
        if not current.get("ok"):
            return {"ok": False, "denominations": denominations, "via": "ws",
                    "error": "Chưa đọc được chip đang treo trên bàn WM (%s)."
                             % (current.get("error") or "no_read")}
        if current.get("total"):
            return {"ok": False, "denominations": denominations, "via": "ws",
                    "error": "Bàn còn %g đv chip đang treo — huỷ trước khi đặt tay mới."
                             % float(current.get("total") or 0)}
        result = socket.place(side, target)
        if not result.get("ok") and result.get("error") == "no_reply" and result.get("sent"):
            # ``no_reply`` KHÁC "bị từ chối": lệnh đã rời máy nhưng không khớp được khung
            # phản hồi. Lấy chip thật làm trọng tài.
            settled = self._verify_after_send(side, target)
            if settled is not None:
                return {"ok": True, "placed": [target], "total": settled, "via": "ws",
                        "denominations": denominations, "confirmed": True,
                        "bet_area": BET_AREA[side],
                        "note": "Đã đặt %d đv vào %s qua WebSocket (xác nhận bằng chip "
                                "trên bàn).%s" % (target, SIDE_NAME[side], all_in_note)}
            # Chip vẫn trống ⇒ tiền CHƯA đi. Nguyên nhân thường gặp: lệnh đi vào socket
            # KHÔNG phải socket cược của bàn (socket sảnh cũng phát khung 21 của bàn, nên
            # bước chọn socket có thể nhầm). Đánh dấu socket đó là "không trả lời" để tay
            # sau chọn socket khác — TUYỆT ĐỐI không gửi lại tiền trong cùng tay.
            mark = getattr(socket, "mark_unresponsive", None)
            if callable(mark):
                try:
                    mark(result.get("detail") or {})
                except Exception:
                    pass
        if not result.get("ok"):
            detail = result.get("detail") or {}
            hint = ""
            if detail:
                hint = (" [bàn group=%s · serial=%s · socket=%s · phản hồi 22=%s · "
                        "protocol đã thấy=%s · 22 gần nhất=%s]"
                        % (detail.get("group"), detail.get("serial"), detail.get("socket"),
                           detail.get("recent22"), detail.get("seen"),
                           detail.get("replies22")))
            error = result.get("error") or "no_reply"
            if error == "table_mismatch":
                return {"ok": False, "denominations": denominations, "via": "ws",
                        "error": "%s Chưa đọc được khung dữ liệu của đúng bàn đang mở nên "
                                 "KHÔNG gửi lệnh (tránh đặt nhầm bàn). Thử lại sau vài giây; "
                                 "nếu vẫn vậy, đóng rồi mở lại bàn." % detail.get("note", "")}
            if error == "unknown_table":
                return {"ok": False, "denominations": denominations, "via": "ws",
                        "error": "Không xác định được bàn đang mở nên KHÔNG gửi lệnh. "
                                 "Hãy mở đúng bàn rồi thử lại."}
            return {"ok": False, "denominations": denominations, "via": "ws",
                    "error": "WebSocket từ chối lệnh cược (%s).%s" % (error, hint)}
        # Xác minh bằng số chip thật đang treo trên bàn.
        deadline = time.monotonic() + 2.5
        placed = 0.0
        while time.monotonic() < deadline:
            after = self.read_bets()
            placed = float(((after.get("spots") or {}).get(side) or {}).get("amount") or 0)
            if abs(placed - target) < 0.01:
                break
            time.sleep(0.2)
        if abs(placed - target) >= 0.01:
            # Server đã xác nhận (``bOk: true``) nhưng số chip đọc được chưa khớp. Không
            # huỷ: chênh lệch thường chỉ là độ trễ vẽ chip, còn bấm huỷ là huỷ một lệnh
            # THẬT vừa vào. Ghi nhận số đọc được để sổ vốn tính theo tiền thật.
            if placed <= 0:
                return {"ok": True, "placed": [target], "total": target, "via": "ws",
                        "denominations": denominations, "confirmed": True,
                        "bet_area": BET_AREA[side],
                        "note": "Server đã nhận %d đv vào %s qua WebSocket (chip hiển thị "
                                "chưa kịp cập nhật).%s"
                                % (target, SIDE_NAME[side], all_in_note)}
            return {"ok": False, "placed": [], "denominations": denominations,
                    "via": "ws", "error": "WS đặt lệch: cần %d, bàn ghi %g — không tự huỷ, "
                             "kiểm tra bàn trước khi đặt tay mới."
                             % (target, placed)}
        return {"ok": True, "placed": [target], "total": placed, "via": "ws",
                "denominations": denominations,
                "confirmed": True, "bet_area": BET_AREA[side],
                "note": "Đã đặt %d đv vào %s qua WebSocket.%s"
                        % (target, SIDE_NAME[side], all_in_note)}

    def _verify_after_send(self, side, target, wait_seconds=5.0):
        """Đọc lại chip thật sau khi đã gửi lệnh mà không khớp được phản hồi.

        Trả số tiền đang treo đúng cửa nếu khớp (đặt thành công), ``None`` nếu chưa xác
        nhận được. Không tự huỷ: chip còn trên bàn là dấu hiệu AN TOÀN (gate chờ bàn dọn
        chip sẽ không cho đặt chồng tay mới), còn huỷ nhầm một lệnh vừa vào sẽ làm mất
        tiền thật.
        """
        deadline = time.monotonic() + max(0.5, float(wait_seconds))
        while time.monotonic() < deadline:
            try:
                after = self.read_bets()
            except Exception:
                after = {}
            if after.get("ok"):
                placed = float(((after.get("spots") or {}).get(side) or {}).get("amount") or 0)
                if abs(placed - target) < 0.01:
                    return placed
            time.sleep(0.25)
        return None

    def cancel(self):
        """Huỷ toàn bộ chip đang treo trên bàn bằng nút huỷ (bấm trong trang).

        Nút huỷ không phải thao tác đặt cược nên vẫn dùng ``element.click()`` trong trang;
        không gửi sự kiện chuột CDP.
        """
        state = self.read()
        if not state.get("ok"):
            return {"ok": False, "error": state.get("error") or "Chưa đọc được bàn."}
        if not state.get("my_bet"):
            return {"ok": True, "note": "Không có chip nào đang treo."}
        socket = getattr(self.session, "wm_socket", None)
        if socket is None or getattr(socket, "_command", None) is None:
            return {"ok": False, "error": "Không có kết nối CDP tới bàn WM để huỷ chip."}
        try:
            value = socket.evaluate(CANCEL_JS)
        except Exception as exc:
            return {"ok": False, "error": "Lỗi khi huỷ chip (%s)." % type(exc).__name__}
        time.sleep(0.5)
        after = self.read()
        return {"ok": True, "cancelled": bool(isinstance(value, dict) and value.get("ok")),
                "my_bet_after": after.get("my_bet") if after.get("ok") else None}

    # ------------------------------------------------------------------ tiện ích
    def read_bets(self):
        """Chip của CHÍNH MÌNH đang treo trên bàn, cùng định dạng ``table_bets.read``.

        Trả ``spots``/``total``/``total_units``/``denominations``/``unit`` để
        ``live_sim.compare_chips`` và ``table_is_clear`` dùng lại được mà không phải
        biết đang đọc bàn EVO hay WM.
        """
        state = self.read()
        if not state.get("ok"):
            return {"ok": False, "error": state.get("error") or "Chưa đọc được bàn.",
                    "spots": {}, "unit": 1, "denominations": [], "ts": time.time()}
        spots = {}
        for side, spot in (state.get("spots") or {}).items():
            units = float((spot or {}).get("my_bet") or 0)
            spots[side] = {"amount": units, "amount_vnd": units,
                           "chips": int((spot or {}).get("chips") or 0)}
        total = sum(item["amount"] for item in spots.values())
        return {"ok": True, "error": "", "source": "wm",
                "spots": spots, "total": total, "total_units": total,
                "denominations": state.get("denominations") or [],
                "unit": 1, "wallet": state.get("wallet") or "",
                "countdown": state.get("countdown") or "",
                "open_result": bool(state.get("open_result")),
                "ts": time.time(),
                "note": "Chip của bạn đang treo trên bàn WM (1 đv = 1.000đ)."}


def to_site_amount(units):
    """Đổi đơn vị sảnh (1 đv = 1.000đ) sang số tiền gửi sàn — WM giữ nguyên số."""
    try:
        return int(round(float(units or 0)))
    except (TypeError, ValueError):
        return 0


def to_units(amount_vnd):
    """Đổi tiền thật (đồng) về đơn vị sảnh của WM."""
    try:
        return float(amount_vnd or 0) / DONG_PER_UNIT
    except (TypeError, ValueError):
        return 0.0
