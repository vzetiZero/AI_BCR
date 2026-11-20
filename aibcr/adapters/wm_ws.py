# -*- coding: utf-8 -*-
"""Đặt cược WM bằng WebSocket (protocol 22) — nhanh, không phụ thuộc toạ độ chuột.

Bàn WM nói chuyện với server bằng khung **nhị phân** chứa JSON, mỗi khung có ``protocol``:

    21  thông tin bàn + ván (gameNo, gameNoRound, betMilliSecond, tableStatus)
    22  lệnh cược: {"betSerialNumber", "gameNo", "gameNoRound",
                    "betArr": [{"betArea": 2, "addBetMoney": 10}], "commission", "remark"}
    24  lá bài đang chia        25  kết quả ván        26  lịch sử bàn

``betArea``: 1 = Banker, 2 = Player, 3 = Hoà. Đã đối chiếu với lệnh thật: gửi
``betArea 2, addBetMoney 10`` thì server trả ``{"protocol": 22, "bOk": true}``, số dư giảm
đúng 10 (10.000đ) và chip hiện trên ô Player.

Muốn gửi lệnh thì phải giữ được instance ``WebSocket`` của trang, mà socket chỉ tạo một
lần lúc tải trang. Vì vậy module này chạy một thread giữ kết nối CDP: cài hook
``WebSocket``, tải lại tab một lần nếu cần (hook chỉ ăn từ document mới), rồi giữ nguyên
kết nối đó để gửi lệnh và đọc phản hồi. Sàn giữ nhiều socket (bàn cũ chưa đóng), nên
socket được chọn theo ``groupID`` khớp bàn đang mở (``#container`` của iframe bàn).

Không có socket thì ``WMBet`` tự quay về đường bấm chuột — tính năng đặt cược không bao
giờ phụ thuộc duy nhất vào WebSocket.
"""

import json
import threading
import time

from aibcr.adapters import browser as BR

# Ô cược -> betArea trên WebSocket.
BET_AREA = {"B": 1, "P": 2, "T": 3}
PROTOCOL_BET = 22
# Ghi chú cố định của client WM khi xác nhận cược (chép từ khung thật).
BET_REMARK = "1,0"
# Tên biến toàn cục của hook; đổi tên khi đổi cấu trúc để không đụng hook cũ còn sót.
HOOK_NAME = "__wmHook2"

HOOK_JS = r"""(() => {
    if (window.__wmHook2) return 'already';
    const Native = window.WebSocket;
    const decoder = new TextDecoder('utf-8');
    const hook = {sockets: [], urls: [], protocols: {}, recent21: [], last22: null,
                  sent: [], errors: []};
    window.__wmHook2 = hook;
    const indexOf = (ws) => hook.sockets.indexOf(ws);
    function Wrapped(url, protocols) {
        const ws = protocols === undefined ? new Native(url) : new Native(url, protocols);
        try {
            hook.sockets.push(ws);
            hook.urls.push(String(url));
            const send = ws.send.bind(ws);
            ws.send = function (data) {
                try {
                    let text = data;
                    if (data instanceof ArrayBuffer) text = decoder.decode(new Uint8Array(data));
                    hook.sent.push(String(text).slice(0, 240));
                    if (hook.sent.length > 60) hook.sent.shift();
                } catch (e) {}
                return send(data);
            };
            ws.addEventListener('message', function (event) {
                try {
                    let text = event.data;
                    if (text instanceof ArrayBuffer) text = decoder.decode(new Uint8Array(text));
                    if (typeof text !== 'string') return;
                    const msg = JSON.parse(text);
                    const proto = msg && msg.protocol;
                    if (typeof proto !== 'number') return;
                    const idx = indexOf(ws);
                    hook.protocols[idx] = proto;
                    if (proto === 21) {
                        // Một socket đẩy protocol 21 cho NHIỀU bàn (sảnh + bàn đang mở),
                        // nên giữ danh sách gần đây rồi lọc theo groupID bàn đang mở.
                        hook.recent21.push({socket: idx, at: Date.now(), data: msg.data});
                        if (hook.recent21.length > 60) hook.recent21.shift();
                    } else if (proto === 22) {
                        hook.last22 = {socket: idx, at: Date.now(), data: msg.data};
                    }
                } catch (e) {
                    if (hook.errors.length < 20) hook.errors.push(String(e).slice(0, 80));
                }
            });
        } catch (e) {}
        return ws;
    }
    Wrapped.prototype = Native.prototype;
    ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach((k) => { Wrapped[k] = Native[k]; });
    window.WebSocket = Wrapped;
    return 'installed';
})()"""

READ_JS = r"""(() => {
    const frame = document.getElementById('iframe_101');
    const doc = (frame && frame.contentDocument) || document;
    const box = doc.getElementById('container');
    const m = box ? /(?:^|\s)group(\d+)/.exec((box.className || '').toString()) : null;
    const openGroup = m ? parseInt(m[1], 10) : null;
    // Socket có thể do khung gốc hoặc do iframe bàn tạo: lấy realm nào thật sự có socket.
    const candidates = [window];
    try { if (frame && frame.contentWindow) candidates.push(frame.contentWindow); } catch (e) {}
    let h = null, scope = '';
    for (let i = 0; i < candidates.length; i++) {
        const hook = candidates[i].__wmHook2;
        if (hook && hook.sockets && hook.sockets.length) {
            h = hook;
            scope = i === 0 ? 'top' : 'iframe';
            break;
        }
        if (hook && !h) { h = hook; scope = i === 0 ? 'top' : 'iframe'; }
    }
    if (!h) return {installed: false, open_group: openGroup, scope: ''};
    return {installed: true, urls: h.urls,
            states: h.sockets.map((s) => s.readyState),
            protocols: h.protocols, recent21: h.recent21, last22: h.last22,
            open_group: openGroup, scope: scope,
            sent: h.sent.slice(-6), errors: h.errors};
})()"""

SEND_JS = r"""(() => {
    const frame = document.getElementById('iframe_101');
    const candidates = [window];
    try { if (frame && frame.contentWindow) candidates.push(frame.contentWindow); } catch (e) {}
    let h = null;
    for (let i = 0; i < candidates.length; i++) {
        const hook = candidates[i].__wmHook2;
        if (hook && hook.sockets && hook.sockets.length) { h = hook; break; }
        if (hook && !h) h = hook;
    }
    if (!h) return {ok: false, error: 'no_hook'};
    const socket = h.sockets[%d];
    if (!socket) return {ok: false, error: 'no_socket'};
    if (socket.readyState !== 1) return {ok: false, error: 'socket_not_open'};
    try {
        const bytes = new TextEncoder().encode(%s);
        socket.send(bytes.buffer);
        return {ok: true, bytes: bytes.length};
    } catch (e) {
        return {ok: false, error: String(e).slice(0, 120)};
    }
})()"""


def bet_frame(side, amount, game_no, round_no, serial=1):
    """Khung protocol 22 đặt ``amount`` (đơn vị sảnh) vào cửa ``side``."""
    return {"protocol": PROTOCOL_BET,
            "data": {"betSerialNumber": int(serial), "gameNo": game_no,
                     "gameNoRound": round_no,
                     "betArr": [{"betArea": BET_AREA[side], "addBetMoney": int(amount)}],
                     "commission": 0, "remark": BET_REMARK}}


def current_table(state):
    """Socket + khung 21 của ĐÚNG bàn đang mở: ``(index, data, seen_at_ms)`` hoặc ``None``.

    Một socket của WM đẩy ``protocol 21`` cho nhiều bàn (sảnh + bàn đang mở), nên phải
    lọc theo ``groupID`` khớp class ``#container`` của iframe bàn rồi lấy khung mới nhất.
    """
    frames = (state or {}).get("recent21") or []
    states = (state or {}).get("states") or []
    group = (state or {}).get("open_group")

    def open_socket(index):
        return 0 <= index < len(states) and states[index] == 1

    best = None
    for entry in frames:
        try:
            index = int((entry or {}).get("socket"))
        except (TypeError, ValueError):
            continue
        if not open_socket(index):
            continue
        data = (entry or {}).get("data") or {}
        if group is not None and data.get("groupID") != group:
            continue
        at = float((entry or {}).get("at") or 0)
        if best is None or at > best[2]:
            best = (index, data, at)
    return best


class WMSocket(threading.Thread):
    """Thread giữ kết nối CDP + hook WebSocket của tab WM, gửi lệnh cược."""

    def __init__(self, session=None, on_status=None, reload_on_start=True):
        super().__init__(daemon=True, name="wm-socket")
        self.session = session or BR.BrowserSession()
        self.on_status = on_status
        self.reload_on_start = reload_on_start
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self._command = None
        self._ws = None
        self._state = {}
        self._state_at = 0.0
        self._reloaded = False
        self._serial = {"key": None, "value": 0}
        self.last_error = ""
        self.ready_at = 0.0

    # ------------------------------------------------------------------ tiện ích
    def _status(self, text, level="info"):
        if self.on_status is None:
            return
        try:
            self.on_status(text, level)
        except Exception:
            pass

    def _command_locked(self, method, params=None, timeout=BR.SCRIPT_TIMEOUT):
        if self._command is None:
            raise RuntimeError("not_connected")
        return self._command(method, params, timeout)

    def evaluate(self, expression, timeout=BR.SCRIPT_TIMEOUT):
        with self.lock:
            result = self._command_locked("Runtime.evaluate", {
                "expression": expression, "returnByValue": True, "silent": True,
            }, timeout)
        return (result or {}).get("result", {}).get("value")

    def state(self, fresh=False):
        """Ảnh chụp hook (đọc trực tiếp nếu ``fresh`` hoặc cache đã cũ)."""
        with self.lock:
            if fresh or time.time() - self._state_at > 1.0:
                try:
                    value = self._command_locked("Runtime.evaluate", {
                        "expression": READ_JS, "returnByValue": True, "silent": True,
                    }).get("result", {}).get("value")
                    if isinstance(value, dict):
                        self._state = value
                        self._state_at = time.time()
                except Exception as exc:
                    self.last_error = "Không đọc được hook WS (%s)." % type(exc).__name__
        return dict(self._state or {})

    def ready(self):
        state = self.state()
        return current_table(state) is not None

    # ------------------------------------------------------------------ kết nối
    def _connect(self):
        import websocket

        page = self.session._cdp_page()
        if page is None:
            raise RuntimeError("no_page")
        ws = websocket.create_connection(
            BR.validated_endpoint(page), suppress_origin=True, timeout=BR.SCRIPT_TIMEOUT,
            redirect_limit=0, http_no_proxy=["127.0.0.1", "localhost", "::1"])
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
                if message.get("id") != seq:
                    continue
                if message.get("error"):
                    raise RuntimeError(str(message["error"])[:120])
                return message.get("result", {})

        self._ws = ws
        self._command = command
        command("Page.enable")
        command("Runtime.enable")
        command("Page.addScriptToEvaluateOnNewDocument",
                {"source": HOOK_JS, "runImmediately": True})
        return True

    def _close(self):
        self._command = None
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        self._ws = None

    def wait_ready(self, timeout=20.0):
        """Chờ hook thấy socket của bàn đang mở."""
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() < deadline:
            if self.ready():
                self.ready_at = time.time()
                return True
            time.sleep(0.5)
        return False

    def prepare(self, timeout=25.0):
        """Kết nối, cài hook; chưa có socket thì tải lại tab một lần (nếu được phép)."""
        if self._connect() and self.wait_ready(4.0):
            return True
        if not self.reload_on_start or self._reloaded:
            return self.wait_ready(timeout)
        self._reloaded = True
        try:
            with self.lock:
                self._command_locked("Page.reload", {"ignoreCache": False}, timeout=15)
        except Exception:
            return False
        self._status("Đang tải lại tab WM để bắt socket đặt cược…", "info")
        return self.wait_ready(timeout)

    # ------------------------------------------------------------------ vòng nền
    def run(self):
        while not self.stop_event.is_set():
            try:
                if self._command is None:
                    if not self.prepare(25.0):
                        self._status("Chưa bắt được socket WM; sẽ bấm chip bằng chuột.", "warn")
                        self._close()
                        self.stop_event.wait(20.0)
                        continue
                    self._status("Socket WM sẵn sàng · đặt cược gửi thẳng qua WebSocket.",
                                 "win")
                self.state(fresh=True)
                self.stop_event.wait(0.5)
            except Exception as exc:
                self.last_error = "Mất kết nối socket WM (%s)." % type(exc).__name__
                self._close()
                self.stop_event.wait(5.0)

    def close(self):
        self.stop_event.set()
        self._close()

    # ------------------------------------------------------------------ đặt cược
    def _next_serial(self, game_no, round_no):
        key = "%s:%s" % (game_no, round_no)
        if self._serial["key"] != key:
            self._serial = {"key": key, "value": 0}
        self._serial["value"] += 1
        return self._serial["value"]

    def place(self, side, amount, wait_seconds=6.0):
        """Gửi lệnh cược qua WebSocket; trả ``{"ok", "frame", "reply", "error"}``."""
        side = str(side or "").upper()[:1]
        if side not in BET_AREA:
            return {"ok": False, "error": "Cửa cược không hợp lệ: %r" % (side,)}
        try:
            amount = int(round(float(amount)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Số tiền không hợp lệ."}
        if amount <= 0:
            return {"ok": False, "error": "Số tiền phải lớn hơn 0."}
        if self._command is None:
            return {"ok": False, "error": "not_connected"}

        state = self.state(fresh=True)
        current = current_table(state)
        if current is None:
            return {"ok": False, "error": "no_socket"}
        socket_index, data, seen_at = current
        game_no, round_no = data.get("gameNo"), data.get("gameNoRound")
        if game_no is None or round_no is None:
            return {"ok": False, "error": "Chưa biết ván hiện tại của bàn."}
        age = (time.time() * 1000.0) - float(seen_at or 0)
        if age > 15000:
            return {"ok": False, "error": "Dữ liệu bàn đã cũ (%.1fs) — chưa gửi."
                                          % (age / 1000.0)}
        # Không tự đoán pha cược bằng ``tableStatus``: server trả ``bOk: false`` khi cửa
        # cược đã đóng, đó mới là căn cứ.
        frame = bet_frame(side, amount, game_no, round_no,
                          serial=self._next_serial(game_no, round_no))
        payload = json.dumps(json.dumps(frame, ensure_ascii=False))
        sent_at = time.time() * 1000.0
        with self.lock:
            try:
                result = self._command_locked("Runtime.evaluate", {
                    "expression": SEND_JS % (int(socket_index), payload),
                    "returnByValue": True, "silent": True,
                }).get("result", {}).get("value") or {}
            except Exception as exc:
                return {"ok": False, "error": "send_failed", "frame": frame,
                        "detail": type(exc).__name__}
            if not result.get("ok"):
                return {"ok": False, "error": result.get("error") or "send_failed",
                        "frame": frame}
            deadline = time.monotonic() + max(1.0, float(wait_seconds))
            while time.monotonic() < deadline:
                try:
                    info = self._command_locked("Runtime.evaluate", {
                        "expression": READ_JS, "returnByValue": True, "silent": True,
                    }).get("result", {}).get("value") or {}
                except Exception:
                    break
                reply = info.get("last22") or {}
                if float(reply.get("at") or 0) >= sent_at:
                    body = reply.get("data") or {}
                    return {"ok": bool(body.get("bOk")), "frame": frame, "reply": body,
                            "error": "" if body.get("bOk") else "server_rejected"}
                time.sleep(0.15)
        return {"ok": False, "frame": frame, "error": "no_reply"}


# ------------------------------------------------------------------ dùng chung
def get_socket(session, start=True, reload_on_start=False, on_status=None):
    """Lấy (và khởi động) một ``WMSocket`` dùng chung cho cả app."""
    socket = getattr(session, "wm_socket", None)
    if socket is not None and socket.is_alive():
        return socket
    socket = WMSocket(session, on_status=on_status, reload_on_start=reload_on_start)
    session.wm_socket = socket
    if start:
        socket.start()
    return socket
