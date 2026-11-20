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


def find_wm_page(port=DEFAULT_PORT):
    """Tìm tab WM (sảnh hoặc bàn). Trả ``(page, detect)`` hoặc ``(None, {})``.

    Dùng khi bật AI live: lúc đó có thể còn ở sảnh, nhưng vẫn phải chọn bộ đọc WM (nó tự
    chờ tới khi bàn mở) thay vì rơi sang bộ đọc Petalive và không bao giờ có dữ liệu.
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
                if isinstance(value, dict) and value.get("is_wm"):
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


def find_wm_table(port=DEFAULT_PORT):
    """Tìm tab đang hiện màn hình bàn WM. Trả ``(page, detect)`` hoặc ``(None, {})``.

    Không nhận diện bằng URL (mirror đổi liên tục) mà bằng cấu trúc iframe
    ``iframe_101`` đang hiển thị trong tab.
    """
    page, detect = find_wm_page(port)
    if page is not None and detect.get("kind") == "table":
        return page, detect
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
        self._seeded = set()
        self.live_rounds = 0
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
        if not self.open_key:
            # Không đọc được mã bàn từ iframe (khác origin hoặc iframe chưa render):
            # học bàn từ protocol 21 đầu tiên thay vì bỏ toàn bộ dữ liệu.
            if protocol != WM.PROTOCOL_TABLE:
                return
            self.open_key = key
        elif key != self.open_key:
            # Bàn khác trong sảnh: bỏ qua để không trộn cầu/lá của hai bàn vào một shoe.
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
        elif protocol == WM.PROTOCOL_HISTORY:
            # Nạp lịch sử bàn ngay khi vào: không có bước này thì AI phải chờ vài ván mới
            # có dữ liệu, và UI đứng ở "chưa phân tích" dù bàn đã có cầu dài.
            self._seed_history(key, data)
        elif protocol == WM.PROTOCOL_RESULT:
            round_data = WM.decode_round(data)
            player = round_data["player_cards"]
            banker = round_data["banker_cards"]
            if not player or not banker:
                return
            canonical_player = [WM.canonical_card(card) for card in player]
            canonical_banker = [WM.canonical_card(card) for card in banker]
            signature = ",".join(str(card) for card in sorted(player + banker))
            # Protocol 25 thường không kèm số tay; lấy số tay của pha cược gần nhất
            # (protocol 21) để UI hiện đúng "Tay N" thay vì đếm lịch sử đã nạp.
            last = self.last_state or {}
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
                "round": round_data.get("round") or last.get("round"),
                "game_no": round_data.get("game_no") or last.get("game_no"),
                "wm_cards": {"player": player, "banker": banker},
            }
            self.key = key
            self.table = payload["table"]
            self.last_round = payload
            self.last_state = dict(last, state="SHOW_RESULT", table=self.table,
                                   round=payload["round"], game_no=payload["game_no"],
                                   time_remains=None)
            if self.on_round is not None:
                self.on_round(payload)
            if self.on_state is not None:
                self.on_state(dict(self.last_state))

    def _seed_history(self, key, data):
        """Nạp lịch sử bàn (protocol 26) để AI có dữ liệu ngay khi vừa vào.

        Chỉ nạp một lần cho mỗi bàn và chỉ khi phiên chưa có ván nào: lịch sử cũ không
        được tính là ván mới (``issue_id`` riêng ``:seed``), nên không ảnh hưởng việc
        đặt cược tay tiếp theo.
        """
        rounds = WM.decode_history(data)
        if not rounds:
            return
        if key in self._seeded:
            return
        self._seeded.add(key)
        if self.on_round is None:
            return
        # Chỉ lấy 30 ván gần nhất có đủ lá: nạp quá xa sẽ làm shoe lệch số bài thật
        # còn lại. ``round`` của ván cuối là số tay thật của bàn, dùng luôn cho UI.
        tail = rounds[-30:]
        current_game = (tail[-1] or {}).get("game_no") if tail else None
        valid = [row for row in tail
                 if (row.get("player_cards") and row.get("banker_cards")
                     # Không trộn ván của shoe trước vào số tay/xác suất shoe đang chạy.
                     and (current_game is None
                          or row.get("game_no") in (None, current_game)))]
        for index, row in enumerate(valid):
            player = row.get("player_cards") or []
            banker = row.get("banker_cards") or []
            card_signature = ",".join(str(card) for card in sorted(player + banker))
            self.on_round({
                "issue_id": "%s:seed:%s" % (key, card_signature),
                "table": "WM %s" % key,
                "room_id": key,
                "player_cards": [WM.canonical_card(card) for card in player],
                "banker_cards": [WM.canonical_card(card) for card in banker],
                "bets": [],
                "winner": row.get("winner") or "",
                "player_score": row.get("player_score"),
                "banker_score": row.get("banker_score"),
                "code": row.get("code"),
                "round": row.get("round"),
                "game_no": row.get("game_no"),
                "wm_cards": {"player": player, "banker": banker},
                "seed": True,
                "seed_last": index == len(valid) - 1,
            })

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
                    elif not self.open_key:
                        # Chỉ xoá khi chưa từng học được bàn; giữ bàn đã biết để
                        # sảnh/iframe ẩn tạm thời không làm mất dữ liệu đang đọc.
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
