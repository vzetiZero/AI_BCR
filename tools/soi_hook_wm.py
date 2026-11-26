# -*- coding: utf-8 -*-
"""Soi thẳng store hook đang sống trong trang: mọi protocol đã thấy + khung 21/22 thô.

    python tools/soi_hook_wm.py

Chỉ ĐỌC. Không cài hook mới, không gửi lệnh. Dùng sau khi vừa bấm cược tay bằng tay
trên web để xem sàn thật trả khung protocol 22 gì (bOk/betSerialNumber nằm ở đâu).
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aibcr.adapters import browser as BR  # noqa: E402
from aibcr.adapters import wm_targets as WT  # noqa: E402
from aibcr.adapters import wm_ws  # noqa: E402

PROBE_JS = r"""(() => {
    const out = {stores: []};
    const grab = (win, label) => {
        let hook = null;
        try { hook = win.__wmHook3; } catch (e) { return; }
        if (!hook) return;
        const protocols = {};
        for (const key of Object.keys(hook.protocols || {})) {
            const code = hook.protocols[key];
            protocols[code] = (protocols[code] || 0) + 1;
        }
        out.stores.push({
            label: label,
            sockets: (hook.sockets || []).length,
            urls: (hook.urls || []).slice(0, 8),
            states: (hook.sockets || []).map((ws) => ws && ws.readyState),
            protocols_seen: protocols,
            seen: hook.seen || {},
            recent21: (hook.recent21 || []).slice(-2),
            recent22: (hook.recent22 || []).map((item) => ({
                socket: item.socket, at: item.at, ok: item.ok,
                serial: item.serial, raw: item.raw, data: item.data,
            })),
            sent: (hook.sent || []).slice(-8),
            errors: (hook.errors || []).slice(0, 5),
        });
    };
    grab(window, 'TOP');
    for (const frame of document.querySelectorAll('iframe')) {
        let child = null;
        try { child = frame.contentWindow; } catch (e) { continue; }
        if (child) grab(child, 'IFRAME:' + (frame.id || frame.name || '?'));
    }
    return out;
})()"""


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
    except Exception:
        page = None
    if page is None:
        print("Không thấy tab WM trên CDP %d." % BR.CDP_PORT)
        return 2

    try:
        frame, parent = WT.find_wm_frame_target(BR.CDP_PORT)
    except Exception:
        frame, parent = None, None
    if frame:
        session.wm_oopif_target = str(frame.get("id") or "")
        session.wm_page_id = str((parent or {}).get("id") or page.get("id") or "")

    socket = wm_ws.WMSocket(session)
    try:
        socket._attach_page()
        socket._attach_oopif()
    except Exception as exc:
        print("Không gắn CDP (%s)." % type(exc).__name__)
        socket._close()
        return 2

    try:
        for channel in socket._channels():
            print("=== khung %s ===" % channel.get("label"))
            try:
                value = socket._evaluate_in(channel, PROBE_JS)
            except Exception as exc:
                print("  lỗi đọc (%s)" % type(exc).__name__)
                continue
            if not isinstance(value, dict):
                print("  không có store hook (hook chưa cài ở khung này)")
                continue
            stores = value.get("stores") or []
            if not stores:
                print("  store rỗng")
            for store in stores:
                print("  [%s] socket=%s states=%s" % (
                    store.get("label"), store.get("sockets"), store.get("states")))
                print("     urls: %s" % (store.get("urls") or [])[:4])
                print("     protocol đã thấy: %s" % (store.get("protocols_seen") or {}))
                print("     đếm mọi protocol: %s" % (store.get("seen") or {}))
                print("     sent (lệnh đã gửi): %d dòng"
                      % len(store.get("sent") or []))
                for line in (store.get("sent") or [])[-4:]:
                    print("        > %s" % str(line)[:300])
                replies = store.get("recent22") or []
                print("     phản hồi 22: %d" % len(replies))
                for item in replies[-4:]:
                    print("        < ok=%s serial=%s socket=%s"
                          % (item.get("ok"), item.get("serial"), item.get("socket")))
                    if item.get("raw"):
                        print("          raw: %s" % str(item.get("raw"))[:400])
                    elif item.get("data") is not None:
                        print("          data: %s" % json.dumps(
                            item.get("data"), ensure_ascii=False)[:400])
                for item in (store.get("recent21") or [])[-1:]:
                    data = item.get("data") or {}
                    print("     khung 21 cuối: socket=%s group=%s gameNo=%s round=%s"
                          % (item.get("socket"), data.get("groupID"),
                             data.get("gameNo"), data.get("gameNoRound")))
                if store.get("errors"):
                    print("     errors: %s" % store.get("errors"))
    finally:
        socket._close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
