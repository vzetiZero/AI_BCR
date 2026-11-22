# -*- coding: utf-8 -*-
"""Tìm đúng CDP target chứa WM khi WM nằm trong iframe KHÁC ORIGIN (OOPIF).

Vì sao cần module riêng: nhiều cổng nhúng WM bằng một iframe trỏ sang máy chủ khác
(ví dụ trang `m.9vnv.com/vn/wm_casino` bọc `wmvn.m8810.com/?sid=…`). Với iframe khác
origin, Chrome:

- chặn ``iframe.contentDocument`` → mọi JS dò trong khung gốc **không thấy** nội dung WM;
- tách iframe thành **target riêng** (Out-Of-Process IFrame) → ``Network.enable`` trên
  tab mẹ **không** nhận được WebSocket của WM.

Vì vậy phải liệt kê target ở cấp trình duyệt (``/json/version`` → ``Target.getTargets``),
rồi chọn target của WM để gắn và đọc. Module này chỉ dò và phân loại; phần đọc/ghi vẫn
do ``BrowserSession``/``LobbyStream`` làm.

Không phụ thuộc tên miền cụ thể: nhận diện theo **cấu trúc** (target type iframe/page
nằm dưới tab đang dùng, host có dấu hiệu WM) để mirror đổi tên vẫn chạy.
"""

import json
import urllib.request

DEFAULT_PORT = 9222

# Dấu hiệu host của WM/M88. Không đặt cứng một tên miền duy nhất vì mirror đổi liên tục;
# chỉ cần MỘT trong các mảnh dưới xuất hiện trong host là đủ nghi ngờ.
WM_HOST_HINTS = ("m8810", "wmvn", "wm-casino", "wmlive", "wmcasino", "wm.", "wm_")

# Tham số trong URL mà WM dùng để nhận phiên (``sid=…&ui=…&co=wm``).
WM_QUERY_HINTS = ("co=wm", "sid=")


def browser_websocket(port=DEFAULT_PORT, timeout=4.0):
    """WebSocket cấp trình duyệt (dùng cho Target.*), khác WebSocket của từng tab."""
    url = "http://127.0.0.1:%d/json/version" % port
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.load(response)
    endpoint = data.get("webSocketDebuggerUrl") or ""
    if not endpoint.startswith("ws://"):
        raise ValueError("no_browser_endpoint")
    return endpoint


def page_targets(port=DEFAULT_PORT, timeout=4.0):
    """Danh sách target thô (gồm cả iframe nếu Chrome có bật OOPIF)."""
    url = "http://127.0.0.1:%d/json/list" % port
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return [item for item in json.load(response) if isinstance(item, dict)]


def host_of(url):
    try:
        from urllib.parse import urlsplit
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def looks_like_wm(url):
    """URL có dấu hiệu là WM: host khớp mảnh nhận diện hoặc query ``co=wm``."""
    text = str(url or "").lower()
    host = host_of(text)
    if host and any(hint in host for hint in WM_HOST_HINTS if hint.endswith(".") is False):
        return True
    if any(hint in text for hint in WM_QUERY_HINTS):
        # ``sid=`` một mình quá rộng; chỉ nhận khi đi kèm ``co=wm`` hoặc host WM.
        return "co=wm" in text or (host and any(h in host for h in WM_HOST_HINTS))
    return False


def is_frame_target(target):
    return isinstance(target, dict) and str(target.get("type") or "") in ("iframe", "page")


def pick_wm_target(targets, parent_id=None):
    """Chọn target WM trong danh sách.

    Ưu tiên:
    1. Target ``iframe`` có URL giống WM (OOPIF nằm dưới một trang bất kỳ).
    2. Target ``page`` có URL giống WM (trường hợp cổng mở thẳng WM).
    3. Trả ``None`` khi không có manh mối — lớp gọi tự dò DOM như cũ.
    """
    items = [t for t in (targets or ()) if isinstance(t, dict)]
    frames = [t for t in items if str(t.get("type") or "") == "iframe"
              and looks_like_wm(t.get("url"))]
    if frames:
        return frames[0]
    pages = [t for t in items if str(t.get("type") or "") == "page"
             and looks_like_wm(t.get("url"))]
    if pages:
        # Nếu biết tab mẹ, ưu tiên target nằm dưới nó.
        if parent_id:
            child = [t for t in pages if t.get("parentId") == parent_id]
            if child:
                return child[0]
        return pages[0]
    return None


def describe(targets):
    """Tóm tắt để log/UI: từng target là gì (giúp người dùng thấy app đã dò gì)."""
    rows = []
    for target in targets or ():
        if not isinstance(target, dict):
            continue
        rows.append({
            "type": str(target.get("type") or ""),
            "url": str(target.get("url") or "")[:200],
            "host": host_of(target.get("url")),
            "wm": looks_like_wm(target.get("url")),
            "parent": str(target.get("parentId") or ""),
        })
    return rows


# --------------------------------------------------------------------------- targets
def all_targets(port=DEFAULT_PORT, timeout=4.0):
    """Danh sách target ĐẦY ĐỦ, gồm cả OOPIF (iframe khác origin).

    ``/json/list`` chỉ trả target cấp tab trên nhiều bản Chrome, nên iframe nhúng WM
    (ví dụ ``wmvn.m8810.com`` trong ``m.9vnv.com/vn/wm_casino``) không xuất hiện. Gọi
    ``Target.getTargets`` ở WebSocket cấp trình duyệt mới thấy.
    """
    import websocket

    merged = {}
    try:
        for item in page_targets(port, timeout):
            if item.get("id"):
                merged[item["id"]] = item
    except Exception:
        pass
    try:
        endpoint = browser_websocket(port, timeout)
        ws = websocket.create_connection(
            endpoint, suppress_origin=True, timeout=timeout, redirect_limit=0,
            http_no_proxy=["127.0.0.1", "localhost", "::1"])
        try:
            ws.send(json.dumps({"id": 1, "method": "Target.getTargets",
                                "params": {"filter": [{}]}}))
            while True:
                ws.settimeout(timeout)
                message = json.loads(ws.recv())
                if message.get("id") != 1:
                    continue
                infos = ((message.get("result") or {}).get("targetInfos")) or []
                for info in infos:
                    if not isinstance(info, dict) or not info.get("targetId"):
                        continue
                    merged.setdefault(info["targetId"], {
                        "id": info["targetId"],
                        "type": info.get("type"),
                        "url": info.get("url"),
                        "title": info.get("title"),
                        "parentId": info.get("openerId") or "",
                        "attached": info.get("attached"),
                    })
                break
        finally:
            try:
                ws.close()
            except Exception:
                pass
    except Exception:
        pass
    return list(merged.values())


def find_wm_frame_target(port=DEFAULT_PORT, parent_url=None):
    """Tìm target iframe của WM và trả kèm URL tab mẹ (nếu suy ra được).

    Trả ``(frame_target, parent_target)``; ``(None, None)`` khi không thấy.
    """
    targets = all_targets(port)
    by_id = {t.get("id"): t for t in targets if isinstance(t, dict)}
    frame = pick_wm_target(targets)
    if frame is None:
        return None, None
    parent = None
    parent_id = frame.get("parentId")
    if parent_id and parent_id in by_id:
        parent = by_id[parent_id]
    elif parent_url:
        for target in targets:
            if (str(target.get("type")) == "page"
                    and str(target.get("url") or "").rstrip("/")
                    == str(parent_url).rstrip("/")):
                parent = target
                break
    return frame, parent


class FrameSocket:
    """Socket CDP gắn vào MỘT target con (OOPIF) nhưng dùng như socket thường.

    Vì sao cần: toàn bộ mã đọc WM hiện có chỉ biết ``send``/``recv`` JSON CDP của một
    tab. Iframe khác origin không có endpoint riêng trên mọi bản Chrome, nên phải
    ``Target.attachToTarget(flatten=True)`` rồi gắn ``sessionId`` vào từng lệnh.

    Lớp này bọc kết nối cấp trình duyệt và chỉ chuyển tiếp message thuộc session của
    mình, nên mã cũ không phải sửa: nó vẫn ``send`` một JSON có ``id`` và đọc ``recv``
    như với tab thường.
    """

    def __init__(self, port=DEFAULT_PORT, target_id=None, timeout=10.0):
        import websocket

        self.port = port
        self.target_id = target_id
        self.session_id = ""
        self._seq = 0
        self._endpoint = browser_websocket(port)
        if not target_id:
            frame, _parent = find_wm_frame_target(port)
            if frame is None:
                raise RuntimeError("no_wm_target")
            self.target_id = frame["id"]
        self._socket = websocket.create_connection(
            self._endpoint, suppress_origin=True, timeout=timeout, redirect_limit=0,
            http_no_proxy=["127.0.0.1", "localhost", "::1"])
        self._timeout = timeout
        result = self._browser_call("Target.attachToTarget",
                                    {"targetId": self.target_id, "flatten": True})
        self.session_id = (result or {}).get("sessionId") or ""
        if not self.session_id:
            self.close()
            raise RuntimeError("attach_failed")

    # ---------------------------------------------------------------- nội bộ
    def _browser_call(self, method, params=None):
        """Lệnh cấp trình duyệt (không kèm sessionId)."""
        self._seq += 1
        request_id = self._seq
        self._socket.send(json.dumps({"id": request_id, "method": method,
                                      "params": params or {}}))
        while True:
            self._socket.settimeout(self._timeout)
            message = json.loads(self._socket.recv())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(str((message["error"] or {}).get("message")))
            return message.get("result") or {}

    # ------------------------------------------------------- API kiểu socket
    def send(self, payload):
        """Nhận JSON CDP y như socket tab, tự gắn ``sessionId`` của iframe."""
        message = json.loads(payload)
        message["sessionId"] = self.session_id
        self._socket.send(json.dumps(message))

    def recv(self):
        """Trả message thuộc session này; bỏ message của tab/browser khác."""
        while True:
            raw = self._socket.recv()
            message = json.loads(raw)
            if message.get("sessionId") == self.session_id:
                message.pop("sessionId", None)
                return json.dumps(message)
            # Sự kiện cấp trình duyệt (Target.*) không có sessionId: bỏ qua để không
            # làm lệch vòng đọc của mã WM hiện có.
            continue

    def settimeout(self, value):
        self._timeout = value
        try:
            self._socket.settimeout(value)
        except Exception:
            pass

    def close(self):
        try:
            self._socket.close()
        except Exception:
            pass

    def evaluate(self, expression):
        """Tiện ích: chạy JS trong iframe này và trả giá trị."""
        self._seq += 1
        request_id = self._seq
        self.send(json.dumps({"id": request_id, "method": "Runtime.evaluate",
                              "params": {"expression": expression,
                                         "returnByValue": True, "silent": True}}))
        while True:
            message = json.loads(self.recv())
            if message.get("id") != request_id:
                continue
            return ((message.get("result") or {}).get("result") or {}).get("value")

    def command(self, method, params=None):
        """Gọi một lệnh CDP và chờ đúng phản hồi."""
        self._seq += 1
        request_id = self._seq
        self.send(json.dumps({"id": request_id, "method": method,
                              "params": params or {}}))
        while True:
            message = json.loads(self.recv())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(str((message["error"] or {}).get("message")))
            return message.get("result") or {}


def open_frame_socket(port=DEFAULT_PORT, target_id=None, timeout=10.0):
    """Tạo :class:`FrameSocket` gắn vào iframe WM. Raise khi không thấy."""
    return FrameSocket(port=port, target_id=target_id, timeout=timeout)
