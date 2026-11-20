# -*- coding: utf-8 -*-
"""Đọc bàn WM (M88) đang mở qua CDP: lá bài + cửa thắng + nhịp cược.

WM đẩy dữ liệu bằng WebSocket **nhị phân** (base64 trong CDP). Mỗi ván có:

    protocol 21  thông tin bàn/pha cược (gameNo, gameNoRound, betMilliSecond)
    protocol 24  từng lá được chia (cardArea 1/3/5 = Player, 2/4/6 = Banker)
    protocol 25  kết quả ván: mã cửa thắng + điểm + dtCard đầy đủ 6 ô

Thread này chỉ đọc tab bàn, không bấm, không đổi trang, không gửi lệnh cược. Dữ liệu
được đổi sang id lá 1..52 chuẩn của ``aibcr.live.cards`` để dùng chung shoe/predict.
"""

import json
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

from aibcr.adapters import wm as WM

DEFAULT_PORT = 9222
RECONNECT_SECONDS = 3.0


def list_pages(port=DEFAULT_PORT):
    with urlopen("http://127.0.0.1:%d/json/list" % port, timeout=3) as response:
        return [page for page in json.load(response)
                if isinstance(page, dict) and page.get("type") == "page"]


def find_wm_table(port=DEFAULT_PORT):
    """Tìm tab đang hiện màn hình bàn WM. Trả ``(page, detect)`` hoặc ``(None, {})``.

    Không nhận diện bằng URL (mirror đổi liên tục) mà bằng cấu trúc iframe
    ``iframe_101`` đang hiển thị trong tab.
    """
    import websocket

    for page in list_pages(port):
        endpoint = page.get("webSocketDebuggerUrl") or ""
        url = str(page.get("url") or "")
        if not endpoint or not url.startswith("https://"):
            continue
        try:
            ws = websocket.create_connection(
                endpoint, suppress_origin=True, timeout=4, redirect_limit=0,
                http_no_proxy=["127.0.0.1", "localhost", "::1"])
        except Exception:
            continue
        try:
            ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
            ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
                "expression": WM.WM_DETECT_JS, "returnByValue": True, "silent": True}}))
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                message = json.loads(ws.recv())
                if message.get("id") != 2:
                    continue
                value = (message.get("result") or {}).get("result", {}).get("value")
                if isinstance(value, dict) and value.get("kind") == "table":
                    return page, value
                break
        except Exception:
            continue
        finally:
            try:
                ws.close()
            except Exception:
                pass
    return None, {}


class WMReader(threading.Thread):
    """Thread đọc một tab bàn WM; tự gắn lại khi tab đổi hoặc WS đứt."""

    def __init__(self, on_round=None, on_state=None, on_status=None,
                 port=DEFAULT_PORT, stop=None):
        super().__init__(daemon=True, name="bcr-wm-live")
        self.on_round = on_round
        self.on_state = on_state
        self.on_status = on_status
        self.port = port
        self.stop = stop or threading.Event()
        self.connected = False
        self.table = ""
        self.key = ""
        # Bàn đang mở (đọc từ class #container của iframe bàn). Sảnh đẩy kết quả của
        # MỌI bàn qua cùng WebSocket, nên phải lọc đúng bàn đang xem.
        self.open_key = ""
        self._probe_id = 0
        self.last_error = ""
        self.last_round = None
        self.last_state = None
        self.frames = 0

    def _status(self, text, level="info"):
        if self.on_status is not None:
            try:
                self.on_status(text, level)
            except Exception:
                pass

    # ------------------------------------------------------------------ dữ liệu
    def handle(self, message):
        """Xử lý một khung protocol của WM. Tách riêng để test không cần socket."""
        if not isinstance(message, dict):
            return
        protocol = message.get("protocol")
        data = message.get("data")
        if not isinstance(data, dict):
            return
        game, group = data.get("gameID"), data.get("groupID")
        if game is None or group is None:
            return
        key = WM.table_key(game, group)
        if not self.open_key or key != self.open_key:
            # Chưa biết bàn đang mở (hoặc là bàn khác trong sảnh): bỏ qua để không trộn
            # cầu/lá của hai bàn vào một shoe.
            return
        self.frames += 1
        if protocol == WM.PROTOCOL_TABLE:
            info = WM.decode_table(data)
            self.key = key
            self.table = "WM %s" % key
            self.last_state = {
                "table": self.table,
                "state": "BETTING",
                "time_remains": (float(info.get("bet_milliseconds") or 0) / 1000.0) or None,
                "round": info.get("round"),
                "game_no": info.get("game_no"),
            }
            if self.on_state is not None:
                self.on_state(dict(self.last_state))
        elif protocol == WM.PROTOCOL_RESULT:
            round_data = WM.decode_round(data)
            player = round_data["player_cards"]
            banker = round_data["banker_cards"]
            if not player or not banker:
                return
            canonical_player = [WM.canonical_card(card) for card in player]
            canonical_banker = [WM.canonical_card(card) for card in banker]
            signature = ",".join(str(card) for card in sorted(player + banker))
            payload = {
                "issue_id": "%s:%s" % (key, signature),
                "table": "WM %s" % key,
                "room_id": key,
                "player_cards": canonical_player,
                "banker_cards": canonical_banker,
                "bets": [],
                "winner": round_data["winner"],
                "player_score": round_data["player_score"],
                "banker_score": round_data["banker_score"],
                "code": round_data["code"],
                "wm_cards": {"player": player, "banker": banker},
            }
            self.key = key
            self.table = payload["table"]
            self.last_round = payload
            self.last_state = dict(self.last_state or {}, state="SHOW_RESULT",
                                   table=self.table)
            if self.on_round is not None:
                self.on_round(payload)

    # -------------------------------------------------------------------- vòng
    def run(self):
        while not self.stop.is_set():
            page, _detect = find_wm_table(self.port)
            if page is None:
                if self.last_error == "":
                    self._status("Chưa thấy màn hình bàn WM; hãy mở một bàn rồi thử lại.", "warn")
                self.connected = False
                time.sleep(RECONNECT_SECONDS)
                continue
            self._stream(page)
            if self.stop.is_set():
                break
            self.connected = False
            self._status("Mất kết nối bàn WM; thử gắn lại…", "warn")
            time.sleep(RECONNECT_SECONDS)

    def _stream(self, page):
        import websocket

        endpoint = page.get("webSocketDebuggerUrl") or ""
        try:
            ws = websocket.create_connection(
                endpoint, suppress_origin=True, timeout=10, redirect_limit=0,
                http_no_proxy=["127.0.0.1", "localhost", "::1"])
        except Exception as exc:
            self.last_error = "Không gắn được tab bàn WM (%s)." % type(exc).__name__
            self._status(self.last_error, "error")
            return
        try:
            ws.send(json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
            self.connected = True
            self.last_error = ""
            self._status("Đã gắn bàn WM · đang đọc lá bài qua WebSocket.", "win")
            sequence = 1
            next_probe = 0.0
            while not self.stop.is_set():
                now = time.monotonic()
                if now >= next_probe:
                    # Đọc lại bàn đang mở định kỳ: người dùng có thể đổi bàn trong lúc
                    # đọc, và sảnh gửi kết quả của mọi bàn trên cùng một socket.
                    sequence += 1
                    self._probe_id = sequence
                    ws.send(json.dumps({"id": sequence, "method": "Runtime.evaluate",
                                        "params": {"expression": WM.WM_OPEN_TABLE_JS,
                                                   "returnByValue": True, "silent": True}}))
                    next_probe = now + 2.0
                ws.settimeout(0.5)
                try:
                    message = json.loads(ws.recv())
                except Exception as exc:
                    if type(exc).__name__ in ("WebSocketTimeoutException", "TimeoutError"):
                        continue
                    break
                if message.get("id") == getattr(self, "_probe_id", 0):
                    value = (message.get("result") or {}).get("result", {}).get("value")
                    if isinstance(value, dict) and value.get("group") is not None \
                            and value.get("game") is not None:
                        self.open_key = WM.table_key(value["game"], value["group"])
                    else:
                        self.open_key = ""
                    continue
                if message.get("method") != "Network.webSocketFrameReceived":
                    continue
                response = (message.get("params") or {}).get("response") or {}
                opcode = response.get("opcode")
                if opcode not in (1, 2):
                    continue
                frame = WM.decode_frame(response.get("payloadData"), opcode)
                if frame is not None:
                    self.handle(frame)
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def close(self):
        self.stop.set()
