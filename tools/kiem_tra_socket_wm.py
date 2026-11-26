# -*- coding: utf-8 -*-
"""Soi socket đặt cược của bàn WM đang mở — chạy tay khi tool báo chưa bắt được socket.

Dùng khi bạn ĐANG đứng trong bàn (Chrome mở profile riêng, CDP 9222, đã đăng nhập):

    python tools/kiem_tra_socket_wm.py

Kết quả cho biết:

    - tab/target nào đang mở, có phải WM không;
    - hook WebSocket có cài được không;
    - có socket nào đang sống (kể cả socket tạo TRƯỚC khi cài hook);
    - socket nào đang gửi protocol 21 của bàn đang mở (groupID khớp ``#container``);
    - kết luận: đặt cược được hay còn thiếu gì.

Script chỉ ĐỌC: không gửi lệnh cược, không tải lại trang, không đóng tab.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aibcr.adapters import browser as BR  # noqa: E402
from aibcr.adapters import wm as WM  # noqa: E402
from aibcr.adapters import wm_targets as WT  # noqa: E402
from aibcr.adapters import wm_ws  # noqa: E402

SCOPE_NAME = {0: "CONNECTING", 1: "OPEN", 2: "CLOSING", 3: "CLOSED"}


def main():
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    session = BR.BrowserSession()
    page = None
    try:
        page = session._cdp_page()
    except Exception as exc:
        print("Không hỏi được tab Chrome CDP %d (%s)." % (BR.CDP_PORT, type(exc).__name__))
    if page is None:
        print("Không thấy tab WM đang mở trên cổng CDP %d." % BR.CDP_PORT)
        print("Mở Chrome bằng profile có --remote-debugging-port=%d rồi vào bàn." % BR.CDP_PORT)
        return 2
    print("Tab: %s" % str(page.get("url") or "")[:120])

    try:
        targets = WT.all_targets(BR.CDP_PORT)
    except Exception:
        targets = []
    print("Target (%d):" % len(targets))
    for row in WT.describe(targets):
        print("  %-8s wm=%-5s %s" % (row["type"], row["wm"], row["url"][:110]))

    frame, _parent = (None, None)
    try:
        frame, _parent = WT.find_wm_frame_target(BR.CDP_PORT)
    except Exception:
        pass
    if frame:
        session.wm_oopif_target = str(frame.get("id") or "")
        session.wm_page_id = str((_parent or {}).get("id") or page.get("id") or "")
        print("Khung WM nhúng (OOPIF): %s" % str(frame.get("url") or "")[:110])

    socket = wm_ws.WMSocket(session)
    try:
        socket._attach_page()
        socket._attach_oopif()
    except Exception as exc:
        print("Không gắn được CDP (%s)." % type(exc).__name__)
        socket._close()
        return 2

    try:
        socket.install_everywhere()
        adopted = socket.adopt_existing()
        state = socket.state(fresh=True)
    finally:
        pass

    print("\nHook: %s · socket nhận thêm từ trang đang sống: %d"
          % ("đã cài" if state.get("installed") else "CHƯA cài", adopted))
    states = state.get("states") or []
    urls = state.get("urls") or []
    scopes = state.get("scopes") or {}
    if not states:
        print("Không thấy socket nào. Hook chỉ bọc được socket tạo SAU khi cài;")
        print("nếu bàn đã mở trước đó, chạy lại lệnh này (nó sẽ nhận socket cũ).")
    for index, value in enumerate(states):
        print("  [%d] %-10s %-8s %s" % (
            index, SCOPE_NAME.get(value, str(value)),
            str(scopes.get(index, "")), (urls[index] if index < len(urls) else "")[:90]))

    # Bàn ĐANG MỞ theo DOM (nguồn tin cậy nhất): ``game_group``, vd ``101_506``. Đây
    # chính là khoá mà app dùng để chốt an toàn tiền trước khi gửi lệnh.
    open_key = ""
    want_group = None
    try:
        value = socket.evaluate(WM.WM_OPEN_TABLE_JS)
        if isinstance(value, dict) and value.get("group") is not None \
                and value.get("game") is not None:
            open_key = WM.table_key(value["game"], value["group"])
            want_group = int(value["group"])
            print("\nBàn ĐANG MỞ theo DOM: %s (dealer: %s)"
                  % (open_key, str(value.get("dealer") or "")[:40]))
        else:
            print("\nBàn ĐANG MỞ theo DOM: KHÔNG xác định (%s)"
                  % str((value or {}).get("reason") or "không đọc được"))
    except Exception as exc:
        print("\nBàn ĐANG MỞ theo DOM: KHÔNG đọc được (%s)" % type(exc).__name__)

    current = wm_ws.current_table(state, want_group)
    replies = state.get("recent22") or []
    if replies:
        print("\nPhản hồi protocol 22 gần nhất (%d):" % len(replies))
        for item in replies[-4:]:
            data = (item or {}).get("data") or {}
            print("  socket #%s · ok=%s · serial=%s · data=%s"
                  % (item.get("socket"), item.get("ok", data.get("bOk")),
                     item.get("serial", data.get("betSerialNumber")),
                     str(data)[:80]))
            if item.get("raw"):
                print("    raw: %s" % str(item.get("raw"))[:300])
    print("\nMã bàn đang mở (#container group): %s" % state.get("open_group"))
    print("Số khung protocol 21 đã thấy: %d" % len(state.get("recent21") or []))
    if current is None:
        if want_group is not None:
            print("\nKẾT LUẬN: CHƯA đặt cược được — chưa thấy khung protocol 21 của bàn "
                  "đang mở (group %d)." % want_group)
            print("App sẽ TỪ CHỐI gửi lệnh trong trạng thái này để không đặt nhầm bàn.")
        else:
            print("\nKẾT LUẬN: CHƯA đặt cược được — không xác định được bàn đang mở.")
            print("Hãy mở đúng bàn trong Chrome rồi chạy lại lệnh này.")
        print("Nếu danh sách socket ở trên rỗng: giữ nguyên bàn, đợi ~3 giây rồi chạy lại")
        print("(tool trong app tự làm việc này mỗi 3 giây); hoặc đổi bàn rồi quay lại.")
        socket._close()
        return 1
    index, data, seen_at = current
    print("\nKẾT LUẬN: ĐẶT CƯỢC ĐƯỢC.")
    print("  socket #%d · ván gameNo=%s gameNoRound=%s · groupID=%s"
          % (index, data.get("gameNo"), data.get("gameNoRound"), data.get("groupID")))
    print("  pha cược: betMilliSecond=%s tableStatus=%s"
          % (data.get("betMilliSecond"), data.get("tableStatus")))
    age = None
    try:
        import time
        age = (time.time() * 1000.0) - float(seen_at or 0)
    except Exception:
        age = None
    if age is not None:
        print("  khung 21 gần nhất cách đây %.1f giây" % (age / 1000.0))
    if want_group is not None and data.get("groupID") is not None:
        try:
            if int(data.get("groupID")) != int(want_group):
                print("\nCẢNH BÁO: khung 21 chọn được KHÔNG phải bàn đang mở "
                      "(group %s vs %s) — app sẽ từ chối gửi lệnh."
                      % (data.get("groupID"), want_group))
        except (TypeError, ValueError):
            pass
    socket._close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
