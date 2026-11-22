# -*- coding: utf-8 -*-
"""Chẩn đoán sảnh: in CHÍNH XÁC app thấy gì trong tab Chrome của bạn.

Dùng khi app báo "Chưa thấy tab sảnh Baccarat" hoặc khi sảnh không được nhận diện.
Công cụ CHỈ ĐỌC: không bấm, không cuộn, không đặt cược, không sửa URL.

Cách dùng:

    # 1. Chrome do app mở, đang ở sảnh Baccarat (port CDP 9222)
    python tools/chan_doan_sanh.py

    # 2. Xem cả nội dung khung gốc + từng iframe (rất hữu ích khi sảnh nằm trong
    #    iframe mà không dùng id iframe_109/iframe_101)
    python tools/chan_doan_sanh.py --frames

    # 3. Đổi cổng CDP nếu app đang dùng cổng khác
    python tools/chan_doan_sanh.py --port 9223

Kết quả sẽ cho biết từng tab được nhận là gì (EVO / Petalive / WM sảnh / WM bàn /
trang cổng / không rõ), kèm "dấu hiệu" đã thấy và các selector quan trọng của WM.
"""

import argparse
import json
import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aibcr.adapters import browser as BR
from aibcr.adapters import wm as WM
from aibcr.adapters import wm_targets as WT


PORT = BR.CDP_PORT

# Đếm các dấu hiệu nhận diện trong MỘT document (khung gốc hay iframe đều dùng được).
PROBE_JS = r"""(() => {
    const out = {href: location.href, title: (document.title || '').slice(0, 120)};
    const q = (sel) => document.querySelectorAll(sel).length;
    out.iframe_109 = !!document.getElementById('iframe_109');
    out.iframe_101 = !!document.getElementById('iframe_101');
    out.game_list_box = !!document.getElementById('game_list_box');
    out.room_items = q('section.game_list.roomItem, [id^="groupBac_"]');
    out.bigroad = !!document.getElementById('bigRoadContainer');
    out.wallet_box = !!document.getElementById('userBalanceBox');
    out.exit_btn = !!document.getElementById('exit_game_btn');
    const box = document.getElementById('container');
    const cls = box ? (box.className || '').toString() : '';
    out.container_class = cls.slice(0, 120);
    out.container_game = /(?:^|\s)game(\d+)/.exec(cls) ? /(?:^|\s)game(\d+)/.exec(cls)[1] : null;
    out.container_group = /(?:^|\s)group(\d+)/.exec(cls) ? /(?:^|\s)group(\d+)/.exec(cls)[1] : null;
    out.iframes = [];
    for (const frame of document.querySelectorAll('iframe')) {
        const rect = frame.getBoundingClientRect();
        out.iframes.push({
            id: frame.id || '',
            src: (frame.getAttribute('src') || '').slice(0, 160),
            w: Math.round(rect.width), h: Math.round(rect.height),
            same_origin: (() => { try { return !!frame.contentDocument; } catch (e) { return false; } })(),
        });
    }
    // Vài dòng chữ tiêu đề để nhận ra trang casino bọc ngoài.
    out.head_text = (document.body ? (document.body.innerText || '') : '')
        .replace(/\s+/g, ' ').trim().slice(0, 200);
    return out;
})()"""


def port_open(port, timeout=1.0):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def cdp_targets(port):
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=4) as resp:
        return [page for page in json.load(resp) if isinstance(page, dict)]


class Conn:
    def __init__(self, endpoint):
        import websocket
        self.ws = websocket.create_connection(
            endpoint, suppress_origin=True, timeout=6, redirect_limit=0,
            http_no_proxy=["127.0.0.1", "localhost", "::1"])
        self.seq = 0
        self.contexts = []

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    def command(self, method, params=None):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            self.ws.settimeout(8)
            message = json.loads(self.ws.recv())
            if message.get("method") == "Runtime.executionContextCreated":
                ctx = message.get("params", {}).get("context", {})
                if (ctx.get("auxData") or {}).get("isDefault"):
                    self.contexts.append(ctx["id"])
            if message.get("id") != self.seq:
                continue
            if "error" in message:
                raise RuntimeError(message["error"].get("message", "cdp_error"))
            return message.get("result", {})

    def evaluate(self, expression, context_id=None):
        params = {"expression": expression, "returnByValue": True}
        if context_id is not None:
            params["contextId"] = context_id
        result = self.command("Runtime.evaluate", params)
        return (result.get("result") or {}).get("value")


def summarize_probe(probe):
    if not isinstance(probe, dict):
        return "không đọc được"
    hits = [name for name in ("iframe_109", "iframe_101", "game_list_box", "bigroad",
                              "wallet_box", "exit_btn") if probe.get(name)]
    if probe.get("room_items"):
        hits.append("room_items=%s" % probe["room_items"])
    if probe.get("container_game") or probe.get("container_group"):
        hits.append("container=%s/%s" % (probe.get("container_game"),
                                         probe.get("container_group")))
    return ", ".join(hits) or "không có dấu hiệu WM"


def classify(probe):
    if not isinstance(probe, dict):
        return "không rõ"
    if probe.get("game_list_box") or probe.get("room_items"):
        return "SẢNH WM ✔"
    if (probe.get("container_game") or probe.get("bigroad")
            or probe.get("wallet_box") or probe.get("exit_btn")):
        return "BÀN WM ✔"
    if probe.get("iframe_109") or probe.get("iframe_101"):
        return "có iframe WM nhưng chưa có nội dung"
    return "không rõ"


def main():
    parser = argparse.ArgumentParser(description="Chẩn đoán sảnh Baccarat trong Chrome CDP")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--frames", action="store_true",
                        help="đọc thêm từng iframe (bỏ qua khác origin)")
    parser.add_argument("--json", action="store_true", help="in JSON thô để gửi kèm báo lỗi")
    args = parser.parse_args()

    print("=" * 78)
    print("CHẨN ĐOÁN SẢNH BACCARAT  ·  CDP port %d" % args.port)
    print("=" * 78)
    if not port_open(args.port):
        print()
        print("CỔNG %d ĐANG ĐÓNG." % args.port)
        print("Nghĩa là KHÔNG có Chrome nào đang mở ở chế độ app dùng.")
        print("Cửa sổ Chrome bạn tự mở (dù đã đăng nhập) KHÔNG có --remote-debugging-port,")
        print("nên app không đọc được. Hãy:")
        print("  1. Đóng hết cửa sổ Chrome (tránh 2 phiên cùng tài khoản).")
        print("  2. Trong app bấm “Mở Chrome”.")
        print("  3. Đăng nhập, mở sảnh Baccarat TRONG cửa sổ vừa mở.")
        print("  4. Chạy lại lệnh này.")
        return 2

    try:
        targets = cdp_targets(args.port)
    except Exception as exc:
        print("Không đọc được /json/list: %s" % exc)
        return 2

    # Iframe KHÁC ORIGIN (OOPIF) không xuất hiện trong /json/list, nên phải hỏi
    # Target.getTargets. Đây chính là trường hợp cổng bọc WM (wm_casino → wmvn.m8810.com).
    print("\n--- Dò OOPIF (iframe khác origin, vd WM nhúng) ---")
    try:
        every = WT.all_targets(args.port)
    except Exception as exc:
        every = []
        print("  không dò được Target.getTargets: %s" % exc)
    oopif_rows = [row for row in WT.describe(every) if row["type"] == "iframe"]
    if oopif_rows:
        for row in oopif_rows:
            print("  iframe target  wm=%-5s host=%-24s %s"
                  % (row["wm"], row["host"] or "-", row["url"]))
        frame, parent = WT.find_wm_frame_target(args.port)
        if frame is not None:
            print("  → NHẬN ĐƯỢC iframe WM: %s" % frame.get("url"))
            print("    tab mẹ: %s" % ((parent or {}).get("url") or "-"))
            try:
                sock = WT.open_frame_socket(args.port, target_id=frame["id"], timeout=6)
                try:
                    sock.command("Runtime.enable")
                    inner = sock.evaluate(PROBE_JS)
                finally:
                    sock.close()
                print("    KẾT LUẬN trong iframe: %s" % classify(inner))
                print("    dấu hiệu:              %s" % summarize_probe(inner))
                if isinstance(inner, dict) and inner.get("container_class"):
                    print("    #container class:      %s" % inner["container_class"])
            except Exception as exc:
                print("    không đọc được nội dung iframe: %s" % type(exc).__name__)
        else:
            print("  → Có iframe nhưng không cái nào trông giống WM.")
    else:
        print("  không có target iframe riêng (WM có thể nằm cùng origin).")

    pages = [t for t in targets if t.get("type") == "page"]
    print("\nCó %d tab (page). Chi tiết:\n" % len(pages))

    results = []
    for index, page in enumerate(pages, 1):
        url = page.get("url") or ""
        entry = {"n": index, "id": page.get("id", ""), "url": url, "tab": None,
                 "frames": []}
        print("-" * 78)
        print("TAB %d  %s" % (index, BR._safe_url(url) or "(không có URL)"))
        print("  là sảnh/bàn theo URL:  EVO=%s  Petalive=%s  /entry=%s"
              % (BR.is_lobby_url(url), BR.is_petalive_table_url(url),
                 BR.is_evo_entry_url(url)))
        endpoint = page.get("webSocketDebuggerUrl") or ""
        if not endpoint:
            print("  KHÔNG có webSocketDebuggerUrl — bỏ qua")
            results.append(entry)
            continue
        conn = None
        try:
            conn = Conn(endpoint)
            conn.command("Runtime.enable")
            probe = conn.evaluate(PROBE_JS)
            entry["tab"] = probe
            print("  KẾT LUẬN:              %s" % classify(probe))
            print("  dấu hiệu:              %s" % summarize_probe(probe))
            if isinstance(probe, dict):
                print("  title:                 %s" % (probe.get("title") or ""))
                if probe.get("container_class"):
                    print("  #container class:      %s" % probe["container_class"])
                if probe.get("iframes"):
                    for frame in probe["iframes"]:
                        print("  iframe  id=%-12s %4dx%-4d same_origin=%-5s src=%s"
                              % (frame.get("id") or "-", frame.get("w"), frame.get("h"),
                                 frame.get("same_origin"), frame.get("src") or "-"))
                else:
                    print("  iframe:                (không có iframe nào)")

            if args.frames and conn.contexts:
                print("  --- đọc từng execution context ---")
                for ctx in list(conn.contexts):
                    try:
                        inner = conn.evaluate(PROBE_JS, context_id=ctx)
                    except Exception as exc:
                        print("    context %s: lỗi %s" % (ctx, type(exc).__name__))
                        continue
                    entry["frames"].append({"context": ctx, "probe": inner})
                    print("    context %-4s %-22s %s" % (
                        ctx, classify(inner), summarize_probe(inner)))
                    if isinstance(inner, dict) and inner.get("href"):
                        print("               %s" % BR._safe_url(inner["href"]))
        except Exception as exc:
            print("  LỖI đọc tab: %s (%s)" % (type(exc).__name__, exc))
        finally:
            if conn is not None:
                conn.close()
        results.append(entry)

    print("-" * 78)
    usable = [row for row in results
              if BR.is_lobby_url(row["url"]) or BR.is_petalive_table_url(row["url"])
              or classify(row["tab"]).startswith(("SẢNH WM", "BÀN WM"))]
    print("\nTỔNG KẾT: %d/%d tab dùng được." % (len(usable), len(results)))
    if not usable:
        print("  → Không tab nào là sảnh/bàn. Nguyên nhân thường gặp:")
        print("    · Tab đang là TRANG CASINO BỌC NGOÀI (wm_casino) — phải bấm vào")
        print("      sảnh Baccarat bên trong.")
        print("    · Sảnh còn đang tải (iframe danh sách bàn chưa render).")
        print("    · Chrome này không do app mở (không có CDP).")
    else:
        for row in usable:
            print("  ✔ TAB %d: %s" % (row["n"], BR._safe_url(row["url"])))
        print("  → Có thể bấm “VÀO BÀN HỢP LỆ” trong app.")
    if args.json:
        print("\n--- JSON ---")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
