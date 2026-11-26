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
lần lúc trang tải. Vì vậy module này chạy một thread giữ kết nối CDP: cài hook
``WebSocket`` cho tab và mọi iframe cùng origin — hoặc cho đúng target iframe khi WM được
nhúng KHÁC ORIGIN (OOPIF, kiểu ``9vnv.com/vn/wm_casino`` bọc ``wmvn.m8810.com``) — rồi
giữ kết nối đó để gửi lệnh và đọc phản hồi. Hook chỉ bọc được socket tạo **sau** khi nó
chạy, nên khi người dùng đã ở trong bàn từ trước (socket cũ) module nạp lại **riêng khung
bàn** một lần — không tải lại tab, không đẩy Chrome về sảnh. Sàn giữ nhiều socket (bàn cũ
chưa đóng), nên socket được chọn theo ``groupID`` khớp bàn đang mở (``#container``) và ưu
tiên socket của iframe bàn.

Đây là **đường đặt cược duy nhất** của app: không có socket thì ``WMBet`` báo lỗi rõ
ràng, không mô phỏng chuột.
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
HOOK_NAME = "__wmHook3"

HOOK_JS = r"""(() => {
    const isTop = (window === window.top);
    const label = isTop ? 'top' : 'iframe';
    if (window.__wmHook3) return 'already';
    const decoder = new TextDecoder('utf-8');
    const hook = {sockets: [], urls: [], protocols: {}, recent21: [], last22: null,
                  sent: [], errors: [], scope: {}, top: isTop};
    window.__wmHook3 = hook;
    // Chạy trong CẢ khung gốc lẫn iframe (Page.addScriptToEvaluateOnNewDocument áp cho
    // mọi frame). Socket đặt cược của WM nằm trong iframe bàn, nên phải hook riêng nó —
    // hook khung gốc không thấy socket đó.
    const onText = (store, handler) => (text) => {
        try {
            const msg = JSON.parse(text);
            const proto = msg && msg.protocol;
            if (typeof proto !== 'number') return;
            handler(proto, msg, text);
        } catch (e) {
            if (store.errors.length < 20) store.errors.push(String(e).slice(0, 80));
        }
    };
    // Dữ liệu khung có thể là string, ArrayBuffer, TypedArray hoặc Blob tuỳ
    // ``binaryType`` của trang — nhận theo cấu trúc để không phụ thuộc realm.
    const decode = (store, data, handler) => {
        if (data === null || data === undefined) return;
        if (typeof data === 'string') { onText(store, handler)(data); return; }
        if (typeof data.byteLength === 'number') {
            try { onText(store, handler)(decoder.decode(new Uint8Array(data))); } catch (e) {}
            return;
        }
        if (typeof data.text === 'function') {
            data.text().then(onText(store, handler)).catch(() => {});
        }
    };
    const record = (store, idx, proto, msg, raw) => {
        store.protocols[idx] = proto;
        // Đếm MỌI protocol đã thấy: ``protocols[idx]`` chỉ giữ số CUỐI của mỗi socket nên
        // không dùng để chẩn đoán được ("hook có thấy protocol 22 không?").
        store.seen = store.seen || {};
        store.seen[proto] = (store.seen[proto] || 0) + 1;
        if (proto === 21) {
            store.recent21.push({socket: idx, at: Date.now(), data: msg.data});
            if (store.recent21.length > 60) store.recent21.shift();
        } else if (proto === 22) {
            // Phản hồi lệnh cược: mirror khác nhau đặt ``bOk``/``betSerialNumber`` ở cấp
            // ngoài HOẶC trong ``data``. Chép sẵn cả hai dạng để Python không phải đoán
            // — đọc sai chỗ coi như lệnh thành công là "bị từ chối" và tool đặt lại.
            const pick = (key) => (msg[key] !== undefined) ? msg[key]
                : ((msg.data && msg.data[key] !== undefined) ? msg.data[key] : null);
            const entry = {socket: idx, at: Date.now(), data: msg.data,
                           ok: pick('bOk'), serial: pick('betSerialNumber')};
            // Giữ khung THÔ: khi định dạng khác dự đoán, đây là thứ duy nhất cho biết
            // sàn thật trả gì (dùng để chẩn đoán, không dùng để quyết định đặt cược).
            if (typeof raw === 'string') entry.raw = raw.slice(0, 400);
            store.last22 = entry;
            // Giữ lịch sử ngắn: phản hồi của lệnh trước không bị lệnh sau ghi đè trước
            // khi vòng chờ đọc kịp.
            store.recent22 = store.recent22 || [];
            store.recent22.push(entry);
            if (store.recent22.length > 12) store.recent22.shift();
        }
    };
    const make = (win, store, tag) => {
        if (!win) return;
        const Native = win.WebSocket;
        if (!Native) return;
        const indexOf = (ws) => store.sockets.indexOf(ws);
        function Wrapped(url, protocols) {
            const ws = protocols === undefined ? new Native(url) : new Native(url, protocols);
            try {
                store.scope[store.sockets.length] = tag;
                store.sockets.push(ws);
                store.urls.push(String(url));
                const send = ws.send.bind(ws);
                ws.send = function (data) {
                    try {
                        const idx = indexOf(ws);
                        const note = (text) => {
                            store.sent.push(String(text).slice(0, 240));
                            if (store.sent.length > 60) store.sent.shift();
                        };
                        if (typeof data === 'string') note(data);
                        else if (typeof data.byteLength === 'number') {
                            try { note(decoder.decode(new Uint8Array(data))); } catch (e) {}
                        }
                        store.last_sent = {socket: idx, at: Date.now()};
                    } catch (e) {}
                    return send(data);
                };
                ws.addEventListener('message', function (event) {
                    const idx = indexOf(ws);
                    decode(store, event.data,
                           (proto, msg, raw) => record(store, idx, proto, msg, raw));
                });
            } catch (e) {}
            return ws;
        }
        Wrapped.prototype = Native.prototype;
        ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach((k) => { Wrapped[k] = Native[k]; });
        win.WebSocket = Wrapped;
        // Đánh dấu: constructor của khung đã được bọc. ``adopt_existing`` dựa vào cờ này
        // để không bọc hai lần (bọc chồng sẽ đẩy cùng một socket vào store hai lần).
        store.wrapped = true;
    };
    window.__wmMakeHook = make;
    window.__wmDecode = decode;
    window.__wmRecord = record;
    make(window, hook, label);
    return 'installed:' + label;
})()"""

# Danh sách window (khung hiện tại + mọi iframe CÙNG origin). ``Runtime.queryObjects``
# chỉ tìm được instance trong đúng realm của prototype, nên phải query RIÊNG từng khung:
# hỏi mỗi khung gốc thì socket đặt cược của iframe bàn sẽ bị bỏ sót.
WINDOWS_JS = r"""(() => {
    const list = [window];
    for (const frame of document.querySelectorAll('iframe')) {
        try { if (frame.contentWindow) list.push(frame.contentWindow); } catch (e) {}
    }
    return list;
})()"""

# Gắn socket ĐÃ TỒN TẠI (tạo trước khi cài hook) vào store của chính khung sở hữu nó.
# Đây là chìa khoá cho tình huống "đã vào bàn rồi mới bấm Chạy tool": hook chỉ bọc được
# socket tạo SAU khi chạy, còn socket của bàn đã có sẵn. Thay vì tải lại khung bàn (làm
# nháy bàn, mất trạng thái cược), ta lấy đúng instance đang sống rồi gắn listener đọc
# protocol 21/22. Gửi lệnh không phụ thuộc wrapper ``send`` — ``SEND_JS`` gọi thẳng
# ``socket.send`` — nên chỉ cần giữ tham chiếu socket là đặt cược được.
#
# ``this`` là mảng socket do ``Runtime.queryObjects`` trả về; ``store``/``label`` của
# khung sở hữu được truyền vào qua ``arguments``.
ADOPT_FN = r"""function (store, label) {
    const decoder = new TextDecoder('utf-8');
    const pick = (msg, key) => (msg[key] !== undefined) ? msg[key]
        : ((msg.data && msg.data[key] !== undefined) ? msg.data[key] : null);
    const put = (idx, proto, msg, raw) => {
        store.protocols[idx] = proto;
        store.seen = store.seen || {};
        store.seen[proto] = (store.seen[proto] || 0) + 1;
        if (proto === 21) {
            store.recent21.push({socket: idx, at: Date.now(), data: msg.data});
            if (store.recent21.length > 60) store.recent21.shift();
        } else if (proto === 22) {
            // Xem ``HOOK_JS``: ``bOk``/``betSerialNumber`` có thể ở cấp ngoài hoặc trong
            // ``data`` tuỳ mirror, nên chép sẵn cả hai để bộ khớp không đoán sai.
            const entry = {socket: idx, at: Date.now(), data: msg.data,
                           ok: pick(msg, 'bOk'), serial: pick(msg, 'betSerialNumber')};
            if (typeof raw === 'string') entry.raw = raw.slice(0, 400);
            store.last22 = entry;
            store.recent22 = store.recent22 || [];
            store.recent22.push(entry);
            if (store.recent22.length > 12) store.recent22.shift();
        }
    };
    const onText = (idx, text) => {
        try {
            const msg = JSON.parse(text);
            const proto = msg && msg.protocol;
            if (typeof proto !== 'number') return;
            put(idx, proto, msg, text);
        } catch (e) {
            if (store.errors.length < 20) store.errors.push(String(e).slice(0, 80));
        }
    };
    const onData = (idx, data) => {
        if (data === null || data === undefined) return;
        if (typeof data === 'string') { onText(idx, data); return; }
        if (typeof data.byteLength === 'number') {
            try { onText(idx, decoder.decode(new Uint8Array(data))); } catch (e) {}
            return;
        }
        if (typeof data.text === 'function') {
            data.text().then((text) => onText(idx, text)).catch(() => {});
        }
    };
    let added = 0;
    for (let i = 0; i < this.length; i++) {
        const ws = this[i];
        if (!ws || typeof ws.readyState !== 'number') continue;
        if (store.sockets.indexOf(ws) >= 0) continue;
        const idx = store.sockets.length;
        try {
            ws.addEventListener('message', function (event) {
                onData(idx, event.data);
            });
        } catch (e) { continue; }
        // Bọc ``send`` GIỐNG ``HOOK_JS``. Socket nhận qua ``queryObjects`` trước đây chỉ
        // được gắn listener đọc, nên lệnh gửi đi không được ghi lại: log luôn hiện
        // "sent: 0 dòng" và không cách nào biết app đã gửi khung gì. Ghi lại cũng cho
        // ta thấy ĐÚNG định dạng mà trang tự dùng khi người dùng bấm cược tay.
        try {
            const native = ws.send;
            if (!native.__wmWrapped) {
                const wrapped = function (data) {
                    try {
                        const note = (text) => {
                            store.sent.push(String(text).slice(0, 240));
                            if (store.sent.length > 60) store.sent.shift();
                        };
                        if (typeof data === 'string') note(data);
                        else if (typeof data.byteLength === 'number') {
                            try { note(decoder.decode(new Uint8Array(data))); } catch (e) {}
                        }
                        store.last_sent = {socket: idx, at: Date.now()};
                    } catch (e) {}
                    return native.apply(ws, arguments);
                };
                wrapped.__wmWrapped = true;
                ws.send = wrapped;
            }
        } catch (e) {}
        store.sockets.push(ws);
        store.urls.push(String(ws.url || ''));
        store.scope[idx] = label;
        added += 1;
    }
    if (added) store.adopted = (store.adopted || 0) + added;
    return added;
}"""

# Cài hook vào iframe bàn hiện tại (iframe có thể đã tồn tại trước khi cài hook).
# Quét MỌI iframe cùng origin, không chỉ ``iframe_101``: mirror đổi id/tên khung, và
# bàn có thể được vẽ trong khung khác. Khung khác origin thì truy cập bị chặn → bỏ qua.
INSTALL_TABLE_JS = r"""(() => {
    const factory = window.__wmMakeHook;
    if (typeof factory !== 'function') return 'no_factory';
    const hookFrame = (frame) => {
        let child = null;
        try { child = frame && frame.contentWindow; } catch (e) { return 0; }
        if (!child) return 0;
        try {
            const hook = child.__wmHook3 || {sockets: [], urls: [], protocols: {},
                recent21: [], last22: null, sent: [], errors: [], scope: {}, top: false};
            child.__wmHook3 = hook;
            factory(child, hook, 'iframe');
            return 1;
        } catch (e) { return 0; }
    };
    let hooked = 0;
    for (const frame of document.querySelectorAll('iframe')) hooked += hookFrame(frame);
    return 'ok:' + hooked;
})()"""

READ_JS = r"""(() => {
    // Đọc mã bàn đang mở từ #container: nằm ở khung gốc (bản một-document) hoặc trong
    // iframe bàn (bản nhiều iframe). Dò lần lượt, không chỉ iframe_101.
    const docs = [document];
    for (const frame of document.querySelectorAll('iframe')) {
        try { if (frame.contentDocument) docs.push(frame.contentDocument); } catch (e) {}
    }
    let openGroup = null;
    for (const doc of docs) {
        const box = doc.getElementById('container');
        if (!box) continue;
        const m = /(?:^|\s)group(\d+)/.exec((box.className || '').toString());
        if (m) { openGroup = parseInt(m[1], 10); break; }
    }
    // Hook chạy riêng trong mỗi frame: gộp socket của khung gốc (sảnh) và MỌI iframe
    // cùng origin đã cài hook (bàn có thể không tên iframe_101 ở mirror mới).
    //
    // Nhãn của từng store quyết định việc chọn socket đặt cược, nên không dựa vào "có
    // phải khung gốc" — với WM nhúng KHÁC ORIGIN, cả trang WM lẫn bàn đều KHÔNG phải
    // khung gốc. Thay vào đó nhìn NỘI DUNG: document nào vẽ bàn (khay chip, ô cược, cầu)
    // thì socket của nó là socket bàn -> 'iframe'; còn lại là sảnh -> 'top'.
    const isTableDoc = (doc) => {
        if (!doc) return false;
        try {
            const shown = (el) => {
                if (!el) return false;
                const st = doc.defaultView ? doc.defaultView.getComputedStyle(el) : null;
                const r = el.getBoundingClientRect();
                return (!st || (st.display !== 'none' && st.visibility !== 'hidden'))
                       && r.width > 4 && r.height > 4;
            };
            if (shown(doc.getElementById('chip_box'))) return true;
            if (shown(doc.getElementById('playbetboxBanker'))) return true;
            if (shown(doc.getElementById('bigRoadContainer'))) return true;
            const box = doc.getElementById('container');
            const cls = box ? (box.className || '').toString() : '';
            return shown(box) && /(?:^|\s)game\d+/.test(cls)
                   && /(?:^|\s)group\d+/.test(cls);
        } catch (e) { return false; }
    };
    const stores = [];
    const push = (hook, label) => {
        if (hook && hook.sockets) stores.push({hook: hook, label: label});
    };
    push(window.__wmHook3, isTableDoc(document) ? 'iframe' : 'top');
    for (const frame of document.querySelectorAll('iframe')) {
        let child = null;
        try { child = frame.contentWindow; } catch (e) { continue; }
        if (!child) continue;
        let hook = null;
        let childDoc = null;
        try {
            hook = child.__wmHook3;
            childDoc = child.document;
        } catch (e) { continue; }
        if (hook && hook !== window.__wmHook3) {
            push(hook, isTableDoc(childDoc) ? 'iframe' : 'top');
        }
    }
    if (!stores.length) return {installed: false, open_group: openGroup};
    const merged = {urls: [], states: [], scopes: {}, protocols: {}, recent21: [],
                    last22: null, recent22: [], sent: [], errors: [], offsets: {},
                    seen: {}};
    for (const entry of stores) {
        const hook = entry.hook;
        const base = merged.states.length;
        merged.offsets[entry.label] = base;
        for (let i = 0; i < hook.sockets.length; i++) {
            merged.states.push(hook.sockets[i].readyState);
            merged.scopes[base + i] = entry.label;
            merged.urls.push(hook.urls[i] || '');
        }
        for (const key of Object.keys(hook.protocols || {})) {
            merged.protocols[base + parseInt(key, 10)] = hook.protocols[key];
        }
        for (const key of Object.keys(hook.seen || {})) {
            merged.seen[key] = (merged.seen[key] || 0) + hook.seen[key];
        }
        for (const item of (hook.recent21 || [])) {
            merged.recent21.push({socket: base + item.socket, at: item.at, data: item.data});
        }
        // Phản hồi lệnh cược: giữ theo từng socket để nơi gọi khớp đúng socket đã gửi
        // (``last22`` chỉ là cái mới nhất toàn khung nên dễ nhận nhầm socket khác).
        const replies = (hook.recent22 || (hook.last22 ? [hook.last22] : []));
        for (const item of replies.slice(-12)) {
            merged.recent22.push({socket: base + item.socket, at: item.at,
                                  data: item.data, offset: base, scope: entry.label,
                                  ok: item.ok, serial: item.serial,
                                  raw: item.raw});
        }
        if (hook.last22) {
            const at = hook.last22.at || 0;
            if (!merged.last22 || at > (merged.last22.at || 0)) {
                merged.last22 = {socket: base + hook.last22.socket, at: at,
                                 data: hook.last22.data, scope: entry.label,
                                 offset: base};
            }
        }
        for (const line of (hook.sent || []).slice(-6)) merged.sent.push(line);
    }
    return {installed: true, urls: merged.urls, states: merged.states,
            scopes: merged.scopes, protocols: merged.protocols, seen: merged.seen,
            recent21: merged.recent21, last22: merged.last22,
            recent22: merged.recent22,
            open_group: openGroup, sent: merged.sent.slice(-6),
            errors: merged.errors, offsets: merged.offsets};
})()"""

SEND_JS = r"""(() => {
    const want = %d;
    // Thứ tự store PHẢI khớp READ_JS, nếu không chỉ số socket bị lệch.
    const stores = [];
    if (window.__wmHook3) stores.push({hook: window.__wmHook3, label: 'top'});
    for (const frame of document.querySelectorAll('iframe')) {
        let child = null;
        try { child = frame.contentWindow; } catch (e) { continue; }
        if (!child) continue;
        let hook = null;
        try { hook = child.__wmHook3; } catch (e) { continue; }
        if (hook && hook !== window.__wmHook3) stores.push({hook: hook, label: 'iframe'});
    }
    let offset = 0;
    for (const entry of stores) {
        const count = entry.hook.sockets.length;
        if (want < offset + count) {
            const socket = entry.hook.sockets[want - offset];
            if (!socket) return {ok: false, error: 'no_socket'};
            if (socket.readyState !== 1) return {ok: false, error: 'socket_not_open'};
            try {
                const bytes = new TextEncoder().encode(%s);
                socket.send(bytes.buffer);
                // Trả mốc theo ĐỒNG HỒ CỦA TRANG. Phản hồi 22 được đánh dấu bằng
                // ``Date.now()`` của trang, nên so với ``time.time()`` của Python
                // (hai đồng hồ khác nguồn) từng loại oan phản hồi thật.
                return {ok: true, bytes: bytes.length, scope: entry.label,
                        at: Date.now()};
            } catch (e) {
                return {ok: false, error: String(e).slice(0, 120)};
            }
        }
        offset += count;
    }
    return {ok: false, error: 'no_socket'};
})()"""


def bet_frame(side, amount, game_no, round_no, serial=1):
    """Khung protocol 22 đặt ``amount`` (đơn vị sảnh) vào cửa ``side``."""
    return {"protocol": PROTOCOL_BET,
            "data": {"betSerialNumber": int(serial), "gameNo": game_no,
                     "gameNoRound": round_no,
                     "betArr": [{"betArea": BET_AREA[side], "addBetMoney": int(amount)}],
                     "commission": 0, "remark": BET_REMARK}}


def _preferred(index, url, prefer):
    """Socket này có phải socket "đã chứng minh đặt được" không?

    ``prefer`` có thể là chỉ số (layout hook ổn định) hoặc URL của socket. URL quan
    trọng hơn chỉ số: khi trang mở/đóng một socket (đổi bàn, reconnect) danh sách socket
    đổi thứ tự và chỉ số cũ trỏ sang socket khác — đúng kiểu lỗi "tay đầu đặt được, tay
    sau không" vì tay sau gửi nhầm sang socket sảnh.
    """
    if prefer is None:
        return False
    if isinstance(prefer, str):
        return bool(url) and url == prefer
    try:
        return int(index) == int(prefer)
    except (TypeError, ValueError):
        return False


def _is_dead(index, url, dead):
    """Socket đã từng gửi lệnh mà sàn im lặng (đã xác minh tiền chưa đi)."""
    if not dead:
        return False
    for key in dead:
        if isinstance(key, str):
            if url and key == url:
                return True
        else:
            try:
                if int(key) == int(index):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def current_table(state, want_group=None, prefer=None, silent=None):
    """Socket + khung 21 của ĐÚNG bàn đang mở: ``(index, data, seen_at_ms)`` hoặc ``None``.

    ``want_group`` = ``groupID`` của bàn người dùng đang mở (lấy từ ``wm_open_key``).
    Rất quan trọng với socket sảnh: một socket phát khung 21 của **nhiều bàn**, nên nếu
    chỉ lấy "khung mới nhất" thì có thể chọn nhầm bàn khác và đặt tiền vào đó.

    ``prefer`` = chỉ số socket đã từng trả lời lệnh cược của đúng bàn này. WM nhúng một
    document có thể có nhiều socket cùng nhận khung 21 (socket sảnh + socket bàn); gửi
    lệnh vào socket sảnh thì sàn **im lặng** và app báo ``no_reply`` oan. Ưu tiên socket
    đã chứng minh là nó nhận được phản hồi 22 của bàn mình.

    Thứ tự chọn:
      1. socket của **iframe bàn** cùng origin (layout nhiều iframe) — đường chuẩn;
      2. socket của khung nhúng WM khác origin (OOPIF) — bàn chạy trong target riêng;
      3. nếu không có cả hai thì dùng socket bất kỳ khớp ``groupID`` (layout
         một-document). Sàn có thể từ chối lệnh gửi từ socket sảnh, nhưng im lặng
         khoá cược còn tệ hơn: lệnh bị từ chối được báo rõ và chip không hề đổi.
    """
    frames = (state or {}).get("recent21") or []
    states = (state or {}).get("states") or []
    urls = (state or {}).get("urls") or []
    scopes = (state or {}).get("scopes") or {}
    group = (state or {}).get("open_group")
    if group is None:
        group = want_group
    else:
        # Bàn đang mở (đọc từ DOM) đáng tin hơn ``#container`` đọc trong khung khác:
        # với WM nhúng khác origin, READ_JS của tab mẹ không thấy container của bàn.
        group = want_group if want_group is not None else group
    if group is None:
        # KHÔNG biết bàn nào đang mở. Socket sảnh phát khung 21 của nhiều bàn, nên chọn
        # "khung mới nhất" ở đây là đặt tiền vào bàn người dùng không nhìn thấy. Thà
        # không đặt còn hơn đặt nhầm bàn.
        return None

    def open_socket(index):
        return 0 <= index < len(states) and states[index] == 1

    dead = (state or {}).get("silent") or silent or {}

    def matches(entry, index):
        data = (entry or {}).get("data") or {}
        return data.get("groupID") == group

    def pick(scope, avoid_dead=True):
        best = None
        preferred = None
        fallback_dead = None
        for entry in frames:
            try:
                index = int((entry or {}).get("socket"))
            except (TypeError, ValueError):
                continue
            if not open_socket(index):
                continue
            if scope is not None and scopes.get(str(index), scopes.get(index)) != scope:
                continue
            if not matches(entry, index):
                continue
            at = float((entry or {}).get("at") or 0)
            url = urls[index] if index < len(urls) else ""
            if _preferred(index, url, prefer):
                # Socket đã từng nhận phản hồi 22 của đúng bàn: chắc chắn hơn "mới nhất".
                preferred = (index, (entry or {}).get("data") or {}, at)
                continue
            candidate = (index, (entry or {}).get("data") or {}, at)
            if avoid_dead and _is_dead(index, url, dead):
                # Socket này từng gửi lệnh mà sàn im lặng (đã xác minh tiền chưa đi).
                # Chỉ dùng khi không còn lựa chọn nào khác.
                if fallback_dead is None or at > fallback_dead[2]:
                    fallback_dead = candidate
                continue
            if best is None or at > best[2]:
                best = candidate
        # Socket ưa thích chỉ được dùng khi khung 21 của nó thuộc ĐÚNG ván hiện tại.
        # Một lần trả lời trước đây không bảo đảm socket đó còn nhận khung của ván mới;
        # dùng khung cũ là gửi lệnh vào ván đã đóng (sàn im lặng hoặc từ chối). So theo
        # ván chứ không theo mốc thời gian: cửa sổ cược của một ván kéo dài hàng chục
        # giây nên "khung cũ hơn vài giây" vẫn là ván đang mở.
        if preferred is not None and best is not None:
            now_round = ((best[1] or {}).get("gameNo"), (best[1] or {}).get("gameNoRound"))
            was_round = ((preferred[1] or {}).get("gameNo"),
                         (preferred[1] or {}).get("gameNoRound"))
            known = [value for value in now_round if value is not None]
            if known and now_round != was_round:
                preferred = None
        return preferred or best or fallback_dead

    picked = pick("iframe") or pick("oopif") or pick(None)
    if picked is None and dead:
        # Mọi socket còn sống đều đã từng câm: thà thử lại còn hơn không đặt được tay nào.
        picked = pick("iframe", avoid_dead=False) or pick("oopif", avoid_dead=False) \
            or pick(None, avoid_dead=False)
    return picked


def merge_states(channels):
    """Gộp ảnh hook của NHIỀU khung (tab cổng + target iframe khác origin) làm một.

    ``channels`` là danh sách ``(label, state)``, ``label`` = ``"page"`` hoặc
    ``"oopif"``. Mỗi khung đánh số socket riêng, nên chỉ số được dồn tiếp sau khung
    trước; ``routes``/``locals`` giữ lại vị trí thật để gửi lệnh đúng socket.

    Vì sao phải gộp: khi WM nằm trong iframe KHÁC ORIGIN, ``Network.enable`` và hook của
    tab mẹ **không** thấy WebSocket của bàn. Hook phải chạy trong chính target iframe, và
    chỉ số socket của nó phải cộng dồn với socket của tab mẹ — nếu không lệnh cược sẽ
    nhắm sai socket (hoặc không tìm thấy).
    """
    merged = {"installed": False, "urls": [], "states": [], "scopes": {},
              "protocols": {}, "recent21": [], "last22": None, "recent22": [],
              "open_group": None, "sent": [], "errors": [], "offsets": {},
              "seen": {}, "routes": [], "locals": []}
    for entry in channels or ():
        label, state = (entry or (None, None))
        if not label or not isinstance(state, dict):
            continue
        if state.get("installed"):
            merged["installed"] = True
        base = len(merged["states"])
        merged["offsets"][label] = base
        states = list(state.get("states") or [])
        urls = list(state.get("urls") or [])
        scopes = state.get("scopes") or {}
        for index, value in enumerate(states):
            merged["states"].append(value)
            merged["urls"].append(urls[index] if index < len(urls) else "")
            scope = scopes.get(str(index), scopes.get(index))
            # Socket của khung bàn luôn tính là "iframe": đó là socket đặt được cược.
            # Socket gốc của khung nhúng (WM chạy trong iframe khác origin) là chỗ sàn
            # nhận dữ liệu bàn nhưng CÓ THỂ từ chối lệnh cược — đánh dấu "oopif" để
            # ``current_table`` chỉ dùng khi không có socket iframe nào.
            if scope == "iframe":
                merged["scopes"][base + index] = "iframe"
            elif label != "page":
                merged["scopes"][base + index] = "oopif"
            else:
                merged["scopes"][base + index] = scope
            merged["routes"].append(label)
            merged["locals"].append(index)
        for key, value in (state.get("protocols") or {}).items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            merged["protocols"][base + index] = value
        for key, value in (state.get("seen") or {}).items():
            # ``seen`` = đếm mọi protocol đã thấy, giữ nguyên khoá (không cộng ``base``).
            try:
                merged["seen"][str(key)] = (merged["seen"].get(str(key)) or 0) + int(value)
            except (TypeError, ValueError):
                continue
        for item in (state.get("recent21") or []):
            try:
                index = int((item or {}).get("socket"))
            except (TypeError, ValueError):
                continue
            merged["recent21"].append({"socket": base + index, "at": item.get("at"),
                                       "data": item.get("data")})
        last = state.get("last22") or None
        if isinstance(last, dict):
            at = float(last.get("at") or 0)
            if merged["last22"] is None or at > float(merged["last22"].get("at") or 0):
                # ``offset`` phải là CỘNG DỒN cả gốc của store trong khung (``READ_JS`` ghi
                # ``offset`` = vị trí socket đầu của store) lẫn ``base`` của khung. Chỉ ghi
                # ``base`` thì ``socket - offset`` thành chỉ số TUYỆT ĐỐI trong khung, không
                # còn là chỉ số cục bộ của store, và phản hồi thật bị lọc oan.
                merged["last22"] = dict(last, socket=base + int(last.get("socket") or 0),
                                        scope=label,
                                        offset=base + int(last.get("offset") or 0))
        for item in (state.get("recent22") or []):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("socket"))
            except (TypeError, ValueError):
                continue
            merged["recent22"].append(dict(item, socket=base + index,
                                           offset=base + int(item.get("offset") or 0),
                                           scope=label))
        if merged["open_group"] is None and state.get("open_group") is not None:
            merged["open_group"] = state.get("open_group")
        merged["sent"].extend(list(state.get("sent") or [])[-6:])
        merged["errors"].extend(list(state.get("errors") or []))
    merged["sent"] = merged["sent"][-6:]
    return merged


class WMSocket(threading.Thread):
    """Thread giữ kết nối CDP + hook WebSocket của tab WM, gửi lệnh cược."""

    def __init__(self, session=None, on_status=None, reload_on_start=False):
        super().__init__(daemon=True, name="wm-socket")
        self.session = session or BR.BrowserSession()
        self.on_status = on_status
        self.reload_on_start = reload_on_start
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self._command = None
        self._ws = None
        self._page = None
        self._oopif = None
        self._state = {}
        self._state_at = 0.0
        self._reloaded = False
        self._serial = {"key": None, "value": 0}
        # Socket đã từng nhận phản hồi 22 của đúng bàn này: ``{group: index}``. Dùng để
        # gửi tay kế vào CÙNG socket thay vì "khung 21 mới nhất" — nhiều socket cùng
        # nhận khung 21 nhưng chỉ một cái là socket cược của bàn; gửi vào socket sảnh thì
        # sàn im lặng → ``no_reply`` dù tiền có thể đã bị trừ.
        self._reply_socket = {}
        # Socket đã gửi lệnh mà sàn không trả lời, dù chip cho thấy tiền chưa đi:
        # ``{group: {index: lúc ghi}}``. Tránh lặp lại đúng socket câm ở tay sau.
        self._silent_socket = {}        # Lệch đồng hồ TRANG - Python (ms). Khung 21/22 đánh dấu bằng ``Date.now()`` của
        # trang, nên mọi so sánh tuổi dữ liệu phải quy về cùng một đồng hồ.
        self._skew_ms = None
        self._skew_at = 0.0
        # Bàn đã thử "cứu" socket (nạp lại riêng khung bàn) — tránh nạp lại lặp vô hạn.
        self._rescued_group = None
        self._rescue_at = 0.0
        self._oopif_try_at = 0.0
        # Nhịp thử nhận socket đang sống (queryObjects) — tránh gọi liên tục.
        self._adopt_try_at = 0.0
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

    def _drop_oopif(self):
        """Gỡ target iframe đã chết (rời bàn, khung bị dựng lại) để lần sau bám lại."""
        channel = self._oopif
        self._oopif = None
        self._oopif_try_at = time.monotonic() + 1.0
        if channel is None:
            return
        try:
            channel["ws"].close()
        except Exception:
            pass

    def _channels(self):
        """Các khung đang giữ hook: target iframe (nếu có) trước, rồi tab.

        Thứ tự này quan trọng: khi WM nhúng khác origin thì BÀN nằm trong target iframe,
        còn tab mẹ chỉ thấy khung cổng — đọc bàn phải hỏi target trước.
        """
        out = []
        for channel in (self._oopif, self._page):
            if channel is not None:
                out.append(channel)
        return out

    def _evaluate_in(self, channel, expression, timeout=BR.SCRIPT_TIMEOUT):
        with self.lock:
            result = channel["command"]("Runtime.evaluate", {
                "expression": expression, "returnByValue": True, "silent": True,
            }, timeout)
        return (result or {}).get("result", {}).get("value")

    def evaluate(self, expression, timeout=BR.SCRIPT_TIMEOUT):
        """Chạy JS ở khung đang vẽ BÀN; khung không có bàn thì thử khung kế tiếp.

        Nhờ vậy ``READ_JS``/``CANCEL_JS``/đọc ví chạy đúng cả khi bàn nằm trong iframe
        khác origin (target riêng), không cần lớp gọi biết chi tiết đó.
        """
        first = None
        for channel in self._channels():
            try:
                value = self._evaluate_in(channel, expression, timeout)
            except Exception:
                continue
            if first is None:
                first = value
            if isinstance(value, dict) and value.get("reason"):
                continue
            if value is not None:
                return value
        return first

    def install_everywhere(self):
        """Cài hook cho MỌI khung đang mở (không dừng ở khung đầu như ``evaluate``).

        Khung bàn và khung sảnh cùng cần hook: dừng sớm sẽ khiến socket của sảnh (hoặc
        của bàn) không bao giờ được bắt.
        """
        installed = 0
        for channel in self._channels():
            try:
                result = self._evaluate_in(channel, INSTALL_TABLE_JS)
            except Exception:
                continue
            if isinstance(result, str) and result.startswith("ok"):
                installed += 1
        return installed

    def adopt_existing(self):
        """Bắt socket ĐÃ TẠO TRƯỚC hook của mọi khung — không tải lại gì cả.

        Tình huống thật: người dùng vào bàn trước, rồi mới bấm Chạy tool. Socket của bàn
        đã sống nên hook ``WebSocket`` (chỉ bọc constructor) không bao giờ thấy nó, và
        app báo "chưa bắt được WebSocket" dù đang đứng giữa bàn.

        CDP có ``Runtime.queryObjects``: đưa vào prototype của ``WebSocket`` thì trả về
        MỌI instance đang sống trong context group đó. Ta lấy prototype của từng khung
        (gốc + mọi iframe cùng origin), query riêng từng cái, rồi chạy ``ADOPT_FN``
        trong chính khung sở hữu để gắn listener đọc protocol 21/22.

        Trả tổng số socket vừa nhận (0 = không có socket nào đang sống).
        """
        adopted = 0
        for channel in self._channels():
            try:
                adopted += self._adopt_in(channel)
            except Exception:
                continue
        if adopted:
            # Ảnh chụp cũ (đọc trước khi nhận socket) không có các socket vừa nhận →
            # buộc lần ``state()`` kế tiếp đọc lại ngay.
            self._state_at = 0.0
        return adopted

    def _adopt_in(self, channel):
        """Nhận socket cũ của MỘT khung (tab cổng hoặc target iframe WM).

        ``Runtime.queryObjects`` cần ``prototypeObjectId`` và chỉ thấy instance trong
        đúng realm của prototype đó. Vì vậy với mỗi window (khung gốc + từng iframe
        cùng origin) ta lấy prototype WebSocket **của chính window ấy**, query riêng, rồi
        chạy ``ADOPT_FN`` bằng ``Runtime.callFunctionOn`` trên mảng kết quả — nhờ đó
        ``store`` và nhãn 'top'/'iframe' là của đúng khung sở hữu socket.
        """
        group = "wm-adopt"
        found = 0
        with self.lock:
            channel["command"]("Runtime.enable")
            reply = channel["command"]("Runtime.evaluate", {
                "expression": WINDOWS_JS, "returnByValue": False, "silent": True,
                "objectGroup": group,
            })
            list_id = (reply.get("result") or {}).get("objectId")
            if not list_id:
                return 0
            try:
                length = ((channel["command"]("Runtime.callFunctionOn", {
                    "objectId": list_id, "objectGroup": group,
                    "functionDeclaration": "function(){return this.length;}",
                    "returnByValue": True,
                }).get("result") or {}).get("value"))
                try:
                    length = int(length or 0)
                except (TypeError, ValueError):
                    return 0
                for index in range(min(length, 16)):
                    try:
                        found += self._adopt_window(channel, list_id, index, group)
                    except Exception:
                        # Khung khác origin / context đã chết: bỏ qua khung đó, không làm
                        # hỏng việc nhận socket của các khung còn lại.
                        continue
            finally:
                try:
                    channel["command"]("Runtime.releaseObjectGroup",
                                       {"objectGroup": group})
                except Exception:
                    pass
        return found

    def _adopt_window(self, channel, list_id, index, group):
        """Nhận socket cũ của MỘT window trong danh sách ``WINDOWS_JS``."""
        window_id = ((channel["command"]("Runtime.callFunctionOn", {
            "objectId": list_id, "objectGroup": group,
            "functionDeclaration": "function(i){return this[i];}",
            "arguments": [{"value": index}], "returnByValue": False,
        }).get("result") or {}).get("objectId"))
        if not window_id:
            return 0
        raw = (channel["command"]("Runtime.callFunctionOn", {
            "objectId": window_id, "objectGroup": group,
            "functionDeclaration": (
                "function(){"
                "  return JSON.stringify({top: this === this.top,"
                "    proto: !!(this.WebSocket && this.WebSocket.prototype),"
                "    href: String(this.location && this.location.href || '')});"
                "}"),
            "returnByValue": True,
        }).get("result") or {}).get("value")
        try:
            flags = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return 0
        if not isinstance(flags, dict) or not flags.get("proto"):
            return 0
        # Store của chính window này (hook đã tạo khi cài cho document hiện tại). Nếu
        # chưa có thì tạo — socket vẫn dùng được qua SEND_JS, chỉ thiếu store để đọc.
        # Kèm luôn việc BỌC constructor nếu khung này chưa được bọc: nếu chỉ nhận socket
        # cũ mà không bọc, socket của ván/bàn sau (tạo lại khi trang tự kết nối lại) sẽ
        # không bao giờ được thấy, và tool lại rơi về "chưa bắt được socket".
        store_id = ((channel["command"]("Runtime.callFunctionOn", {
            "objectId": window_id, "objectGroup": group,
            "functionDeclaration": (
                "function(){"
                "  if (!this.__wmHook3) {"
                "    this.__wmHook3 = {sockets: [], urls: [], protocols: {},"
                "      recent21: [], recent22: [], last22: null, sent: [], errors: [],"
                "      scope: {}, top: (this === this.top), adopted: 0};"
                "  }"
                "  const store = this.__wmHook3;"
                "  const make = this.__wmMakeHook;"
                "  if (typeof make === 'function' && !store.wrapped) {"
                "    make(this, store, (this === this.top) ? 'top' : 'iframe');"
                "  }"
                "  return store;"
                "}"),
            "returnByValue": False,
        }).get("result") or {}).get("objectId"))
        if not store_id:
            return 0
        proto_id = ((channel["command"]("Runtime.callFunctionOn", {
            "objectId": window_id, "objectGroup": group,
            "functionDeclaration": "function(){return this.WebSocket.prototype;}",
            "returnByValue": False,
        }).get("result") or {}).get("objectId"))
        if not proto_id:
            return 0
        array_id = ((channel["command"]("Runtime.queryObjects", {
            "prototypeObjectId": proto_id, "objectGroup": group,
        }).get("objects") or {}).get("objectId"))
        if not array_id:
            return 0
        label = "top" if flags.get("top") else "iframe"
        value = (channel["command"]("Runtime.callFunctionOn", {
            "objectId": array_id, "objectGroup": group,
            "functionDeclaration": ADOPT_FN,
            "arguments": [{"objectId": store_id}, {"value": label}],
            "returnByValue": True,
        }).get("result") or {}).get("value")
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def state(self, fresh=False):
        """Ảnh chụp hook của MỌI khung, gộp lại (đọc trực tiếp nếu cần).

        Khung nào không trả lời (target iframe đã chết khi rời bàn) thì bị bỏ và gỡ
        khỏi danh sách, để lần sau bám lại target mới — nếu giữ, trạng thái cũ sẽ khiến
        app tưởng socket vẫn sẵn sàng và gửi lệnh vào hư không.
        """
        with self.lock:
            if fresh or time.time() - self._state_at > 1.0:
                snapshots = []
                dead = []
                for channel in self._channels():
                    try:
                        value = self._evaluate_in(channel, READ_JS)
                    except Exception:
                        dead.append(channel)
                        continue
                    if isinstance(value, dict):
                        snapshots.append((channel["label"], value))
                for channel in dead:
                    if channel is self._oopif:
                        self._drop_oopif()
                if snapshots:
                    self._state = merge_states(snapshots)
                    self._state_at = time.time()
                else:
                    # Không khung nào trả lời: bỏ cache để không báo "sẵn sàng" sai.
                    self._state = {}
                    self._state_at = 0.0
                    if dead:
                        self.last_error = "Không đọc được hook WS của khung nào."
        return dict(self._state or {})

    def ready(self):
        state = self.state()
        return current_table(state) is not None

    # ------------------------------------------------------------------ kết nối
    def _install_new_document_hook(self, channel):
        """Đăng ký hook cho MỌI document mới của khung VÀ cài ngay cho document hiện tại.

        ``Page.addScriptToEvaluateOnNewDocument`` chỉ áp cho document tải **sau** — với
        WM nhúng khác origin, document WM đã tải xong từ trước, nên nếu chỉ đăng ký thì
        socket của bàn (tạo khi vào bàn, trong chính document đó) không bao giờ được bắt.
        Vì vậy phải chạy ``HOOK_JS`` ngay trong document đang sống.
        """
        registered = False
        try:
            channel["command"]("Page.addScriptToEvaluateOnNewDocument",
                              {"source": HOOK_JS, "runImmediately": True})
            registered = True
        except Exception:
            registered = False
        if not registered:
            # Chrome cũ không biết ``runImmediately``: vẫn đăng ký cho document mới.
            try:
                channel["command"]("Page.addScriptToEvaluateOnNewDocument",
                                  {"source": HOOK_JS})
            except Exception:
                pass
        try:
            channel["command"]("Runtime.evaluate", {"expression": HOOK_JS,
                                                   "returnByValue": True, "silent": True})
        except Exception:
            pass

    def _attach_page(self):
        """Mở kết nối CDP tới tab và cài hook cho tab + mọi khung cùng origin."""
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
        channel = {"label": "page", "command": command, "ws": ws}
        self._page = channel
        command("Page.enable")
        command("Runtime.enable")
        self._install_new_document_hook(channel)
        return True

    def _attach_oopif(self):
        """Gắn vào target iframe WM khi WM nhúng KHÁC ORIGIN; ``None`` nếu không có.

        Vì sao cần: tab cổng chỉ thấy iframe ``wmvn.m8810.com`` như một hộp đen —
        ``iframe.contentDocument`` bị chặn và ``Network.enable`` của tab mẹ không nhận
        được WebSocket bên trong. Bàn thật (và socket đặt cược) nằm trong target riêng,
        nên hook phải chạy Ở ĐÓ. Target được nhớ trong ``session.wm_oopif_target`` khi
        luồng quét nhận ra WM; nếu chưa có thì tự dò.
        """
        if self._oopif is not None:
            return self._oopif
        from aibcr.adapters import wm_targets as WT

        page_id = str((self.session and getattr(self.session, "wm_page_id", "")) or "")
        target_id = str(getattr(self.session, "wm_oopif_target", "") or "")
        if not target_id:
            try:
                frame, parent = WT.find_wm_frame_target(BR.CDP_PORT)
            except Exception:
                frame, parent = None, None
            if not frame:
                return None
            target_id = str(frame.get("id") or "")
            if (frame.get("type") == "page"
                    or target_id == page_id
                    or (parent and parent.get("id") == target_id)):
                # WM đang mở thành TAB (không phải iframe nhúng): tab đó đã được gắn ở
                # ``_attach_page``. Gắn thêm sẽ đếm trùng socket của cùng một document.
                return None
        if not target_id or target_id == page_id:
            return None
        try:
            socket_ = WT.open_frame_socket(BR.CDP_PORT, target_id=target_id,
                                           timeout=BR.SCRIPT_TIMEOUT)
        except Exception:
            # Target đã chết (khung dựng lại / đã rời bàn): xoá ký ức để luồng quét nhận
            # target mới, thay vì bám mãi vào id không còn tồn tại.
            if self.session is not None and \
                    str(getattr(self.session, "wm_oopif_target", "") or "") == target_id:
                self.session.wm_oopif_target = ""
            return None
        try:
            socket_.command("Page.enable")
            socket_.command("Runtime.enable")
        except Exception:
            try:
                socket_.close()
            except Exception:
                pass
            return None

        def frame_command(method, params=None, timeout=BR.SCRIPT_TIMEOUT):
            # ``FrameSocket.command`` chỉ nhận (method, params); timeout đặt riêng.
            socket_.settimeout(timeout)
            return socket_.command(method, params)

        channel = {"label": "oopif", "command": frame_command, "ws": socket_,
                   "target": target_id}
        try:
            self._install_new_document_hook(channel)
            self._evaluate_in(channel, INSTALL_TABLE_JS)
        except Exception:
            pass
        self._oopif = channel
        return channel

    def _connect(self):
        """Kết nối tab + (nếu WM nhúng khác origin) target iframe của bàn."""
        if not self._attach_page():
            return False
        self._attach_oopif()
        return True

    def _close(self):
        self._command = None
        self._page = None
        channels = [self._oopif]
        self._oopif = None
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        self._ws = None
        for channel in channels:
            if channel is None:
                continue
            try:
                channel["ws"].close()
            except Exception:
                pass

    def wait_ready(self, timeout=20.0):
        """Chờ hook thấy socket của bàn đang mở."""
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() < deadline:
            if self.ready():
                self.ready_at = time.time()
                return True
            time.sleep(0.5)
        return False

    def rescue_table(self, timeout=18.0):
        """Giữ bàn nguyên trạng và CỨU socket cũ bằng ``Runtime.queryObjects``.

        Việc reload khung WM có thể làm mất giao diện/trạng thái cược và không an toàn
        với mọi layout, nên đường cứu duy nhất là nhận chính instance ``WebSocket`` đang
        sống (xem ``adopt_existing``) — không tải lại bất cứ thứ gì.
        """
        if self.ready():
            return True
        state = self.state(fresh=True)
        group = state.get("open_group")
        # Nhận socket đang sống (bàn mở trước hook). Chặn nhịp để không queryObjects
        # liên tục mỗi 0,5 giây khi đang đứng ở sảnh.
        adopted = 0
        if time.monotonic() >= self._adopt_try_at:
            self._adopt_try_at = time.monotonic() + 3.0
            try:
                adopted = self.adopt_existing()
            except Exception:
                adopted = 0
        if adopted and self.wait_ready(min(3.0, max(1.0, float(timeout)))):
            self._rescued_group = group
            self._status("Đã nhận socket đang sống của bàn (không tải lại) · "
                         "đặt cược đi qua WebSocket.", "win")
            return True
        if group is None:
            # Đang ở sảnh (hoặc khung bàn đang tải lại): hook đã cài sẵn, socket của bàn
            # sẽ được bắt khi bàn mở ra. Không nạp lại gì để tránh nháy bàn — đồng thời
            # xoá mốc "đã cứu" để lần vào lại CÙNG bàn vẫn được thử lại một lần.
            self._rescued_group = None
            return False
        if self._rescued_group == group:
            # Đã thử cứu bàn này rồi: không spam log mỗi vòng 0,5 giây.
            return False
        self._rescued_group = group
        self._status("Chưa bắt được WebSocket của bàn đã mở. Giữ nguyên giao diện; "
                     "đặt cược qua tool đang tạm khóa.", "warn")
        return False

    def prepare(self, timeout=25.0):
        """Kết nối + cài hook + nhận socket cũ của bàn đang mở.

        Thứ tự: cài hook cho các khung hiện có → nhận socket ĐÃ SỐNG (người dùng vào bàn
        trước rồi mới bấm Chạy tool là tình huống thường gặp) → nếu vẫn không có thì giữ
        nguyên trang và báo chưa sẵn sàng. Không bao giờ tải lại tab hay khung bàn.
        """
        if not self._connect():
            return False
        if self.wait_ready(3.0):
            return True
        try:
            self.install_everywhere()
            if self.ready():
                return True
        except Exception:
            pass
        try:
            if self.adopt_existing() and self.wait_ready(3.0):
                return True
        except Exception:
            pass
        self.rescue_table(timeout)
        return self.wait_ready(min(5.0, timeout))

    # ------------------------------------------------------------------ vòng nền
    def run(self):
        """Vòng nền: giữ hook sống và bắt socket khi bàn được mở lại.

        Không tự tải lại tab hay khung bàn. Nếu socket của bàn có trước hook thì đặt cược
        tạm khóa và giữ giao diện nguyên trạng.
        """
        warned = False
        while not self.stop_event.is_set():
            try:
                if self._command is None:
                    self._connect()
                    try:
                        self.install_everywhere()
                    except Exception:
                        pass
                self.state(fresh=True)
                if not self.ready():
                    # Bàn có thể vừa được tách thành target riêng (iframe khác origin):
                    # bám vào target iframe để hook chạy đúng chỗ bàn sống.
                    if self._oopif is None and time.monotonic() >= self._oopif_try_at:
                        self._oopif_try_at = time.monotonic() + 3.0
                        try:
                            self._attach_oopif()
                        except Exception:
                            pass
                    try:
                        self.install_everywhere()
                    except Exception:
                        pass
                    # Socket của bàn có thể đã sống TRƯỚC khi hook chạy (người dùng vào
                    # bàn rồi mới bật tool) — ``rescue_table`` nhận thẳng instance đang
                    # sống, không reload. Nó tự chặn nhịp nên không gọi dồn dập.
                    self.rescue_table()
                if self.ready():
                    if warned:
                        self._status("Socket WM sẵn sàng · đặt cược gửi thẳng qua WebSocket.",
                                     "win")
                        warned = False
                elif not warned:
                    self._status("Chưa bắt được socket WM — đặt cược tạm khoá tới khi "
                                 "socket sẵn sàng.", "info")
                    warned = True
                self.stop_event.wait(0.5)
            except Exception as exc:
                self.last_error = "Mất kết nối socket WM (%s)." % type(exc).__name__
                self._close()
                self.stop_event.wait(5.0)

    def close(self):
        self.stop_event.set()
        self._close()

    # ------------------------------------------------------------------ đặt cược
    def _page_now(self):
        """Giờ của TRANG (ms) — nguồn thời gian của mọi khung protocol 21/22.

        Trả ``None`` nếu chưa đọc được (khung chết). Lệch đồng hồ được tính lại mỗi 30
        giây; giữa hai lần đo, dùng ``time.monotonic`` để tuổi dữ liệu vẫn tăng đúng.
        """
        monotonic = time.monotonic()
        if self._skew_ms is not None and monotonic - self._skew_at < 30.0:
            base, elapsed = self._skew_ms
            return base + (monotonic - elapsed) * 1000.0
        channel = self._page
        if channel is None or self._command is None:
            return None
        try:
            value = self._evaluate_in(channel, "Date.now()")
        except Exception:
            return None
        try:
            page_now = float(value)
        except (TypeError, ValueError):
            return None
        self._skew_ms = (page_now, monotonic)
        self._skew_at = monotonic
        return page_now

    def _age_ms(self, seen_at):
        """Tuổi của mốc ``at`` (đồng hồ trang) tính theo giờ TRANG, đơn vị ms.

        Trả ``None`` khi không xác định được (không đọc được giờ trang) — nơi gọi phải
        coi là "không kết luận được", không được mặc định là cũ.
        """
        try:
            seen = float(seen_at or 0)
        except (TypeError, ValueError):
            return None
        if not seen:
            return None
        now = self._page_now()
        if now is None:
            return None
        return now - seen

    def _socket_url(self, index):
        """URL của socket thứ ``index`` — khoá ổn định hơn chỉ số khi danh sách đổi."""
        try:
            urls = (self.state() or {}).get("urls") or []
            if 0 <= int(index) < len(urls):
                return str(urls[int(index)] or "")
        except (TypeError, ValueError):
            pass
        return ""

    def _remember_socket(self, group, index, url=""):
        """Nhớ socket đã trả lời lệnh của bàn: ưu tiên URL, giữ chỉ số làm dự phòng."""
        self._reply_socket[group] = url or index
        return self._reply_socket[group]

    def _forget_socket(self, group):
        self._reply_socket.pop(group, None)

    def mark_unresponsive(self, detail=None):
        """Ghi nhớ socket vừa gửi lệnh mà sàn KHÔNG trả lời, để tay sau chọn socket khác.

        Chỉ gọi khi đã đọc chip thật và thấy tiền CHƯA đi — tức là ta biết chắc lệnh
        không tới nơi. Nhờ vậy lần đặt sau không lặp lại đúng sai lầm đó thay vì bỏ cả tay.
        """
        detail = detail or {}
        try:
            group = int(detail.get("group"))
            index = int(detail.get("socket"))
        except (TypeError, ValueError):
            return False
        self._reply_socket.pop(group, None)
        url = ""
        try:
            url = self._socket_url(int(index))
        except (TypeError, ValueError):
            url = ""
        dead = self._silent_socket.setdefault(group, {})
        # Khoá theo URL khi biết: chỉ số socket đổi khi trang reconnect.
        dead[url or index] = time.time()
        # Chỉ giữ 10 phút: socket có thể được tạo lại sau khi trang tự kết nối lại.
        cutoff = time.time() - 600.0
        for key in list(dead):
            if dead[key] < cutoff:
                dead.pop(key, None)
        return True

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

        # Mã bàn đang mở (``game_group``), do watcher đọc từ DOM bàn đang hiển thị.
        open_key = str(getattr(self.session, "wm_open_key", "") or "")
        want_group = None
        if open_key:
            try:
                want_group = int(open_key.split("_")[-1])
            except (TypeError, ValueError):
                want_group = None
        state = self.state(fresh=True)
        prefer = self._reply_socket.get(int(want_group)) if want_group is not None else None
        silent = self._silent_socket.get(int(want_group)) if want_group is not None else None
        current = current_table(state, want_group, prefer=prefer, silent=silent)
        if current is None:
            # Lần cuối thử cứu: socket của bàn có thể đã sống trước hook (người dùng vào
            # bàn rồi mới bấm Chạy tool). Nhận nó và thử lại một lần — không tải lại gì.
            try:
                if self.adopt_existing():
                    state = self.state(fresh=True)
                    current = current_table(state, want_group, prefer=prefer,
                                            silent=silent)
            except Exception:
                current = None
        if current is None:
            if want_group is not None:
                # Không có khung 21 nào của ĐÚNG bàn đang mở. Không được đoán bàn khác:
                # socket sảnh phát khung 21 của nhiều bàn, đặt nhầm là mất tiền thật.
                return {"ok": False, "error": "table_mismatch",
                        "detail": {"open_key": open_key, "want_group": want_group,
                                   "note": "Chưa thấy khung 21 của bàn đang mở (group %d) — "
                                           "không gửi lệnh để tránh đặt nhầm bàn."
                                           % want_group}}
            return {"ok": False, "error": "no_socket"}
        socket_index, data, seen_at = current
        game_no, round_no = data.get("gameNo"), data.get("gameNoRound")
        if game_no is None or round_no is None:
            return {"ok": False, "error": "Chưa biết ván hiện tại của bàn."}
        # Mã bàn: khoá nhận phản hồi của sàn WM (khung thật không có betSerialNumber).
        table_group = data.get("groupID")
        if table_group is None and want_group is None:
            return {"ok": False, "error": "unknown_table",
                    "detail": {"note": "Không xác định được bàn đang mở — không gửi lệnh."}}
        age = self._age_ms(seen_at)
        if age is not None and age > 15000:
            return {"ok": False, "error": "Dữ liệu bàn đã cũ (%.1fs) — chưa gửi."
                                          % (age / 1000.0)}
        # Không tự đoán pha cược bằng ``tableStatus``: server trả ``bOk: false`` khi cửa
        # cược đã đóng, đó mới là căn cứ.
        serial = self._next_serial(game_no, round_no)
        frame = bet_frame(side, amount, game_no, round_no, serial=serial)
        payload = json.dumps(json.dumps(frame, ensure_ascii=False))
        routes = list(state.get("routes") or [])
        locals_ = list(state.get("locals") or [])
        route = routes[socket_index] if socket_index < len(routes) else "page"
        local_index = locals_[socket_index] if socket_index < len(locals_) else socket_index
        channel = self._oopif if (route != "page" and self._oopif) else self._page
        if channel is None:
            return {"ok": False, "error": "not_connected"}
        sent_at = self._page_now() or (time.time() * 1000.0)
        with self.lock:
            try:
                result = self._evaluate_in(channel, SEND_JS % (int(local_index), payload))
            except Exception as exc:
                return {"ok": False, "error": "send_failed", "frame": frame,
                        "detail": type(exc).__name__}
            if not isinstance(result, dict) or not result.get("ok"):
                return {"ok": False,
                        "error": (result or {}).get("error") or "send_failed",
                        "frame": frame}
            # Mốc thời gian phải lấy từ ĐỒNG HỒ CỦA TRANG (``SEND_JS`` trả ``at``), vì
            # ``recent22`` cũng đánh dấu bằng ``Date.now()`` của trang. Trước đây dùng
            # ``time.time()`` của Python: lệch múi/đồng hồ giữa hai tiến trình làm mọi
            # phản hồi thật bị coi là "cũ" và loại sạch → báo no_reply dù sàn đã trả lời.
            try:
                sent_at = float(result.get("at") or 0)
            except (TypeError, ValueError):
                sent_at = 0.0
            if not sent_at:
                sent_at = self._page_now() or (time.time() * 1000.0)
            deadline = time.monotonic() + max(1.0, float(wait_seconds))
            # Thời gian chờ vẽ/đẩy phản hồi của trang: so đồng hồ trang với nhau nên chỉ
            # cần dung sai nhỏ; vẫn cho phép phản hồi đến trước mốc một chút vì
            # ``Date.now()`` của trang và của listener có thể lệch trong cùng một ms.
            grace_ms = 3000.0
            while time.monotonic() < deadline:
                reply = None
                for item in self._channels():
                    try:
                        info = self._evaluate_in(item, READ_JS) or {}
                    except Exception:
                        continue
                    candidates = list(info.get("recent22") or [])
                    if info.get("last22"):
                        candidates.append(info["last22"])
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        try:
                            at = float(candidate.get("at") or 0)
                        except (TypeError, ValueError):
                            continue
                        # Chỉ nhận phản hồi đến quanh/sau lúc gửi. Chừa ``grace_ms`` vì
                        # phản hồi có thể được ghi nhận cùng mili-giây với lệnh gửi.
                        if at < sent_at - grace_ms:
                            continue
                        # Khớp 1 — theo ``betSerialNumber``: chỉ một số mirror dội lại
                        # trường này. Nếu có thì đây là khoá chính xác nhất.
                        if self._same_serial(candidate, serial):
                            reply = candidate
                            break
                        if candidate.get("serial") is not None:
                            continue
                        body = candidate.get("data") or {}
                        if body.get("betSerialNumber") is not None:
                            continue
                        # Khớp 2 — theo ``groupID``: ĐÂY LÀ DẠNG THẬT của sàn WM. Đã bắt
                        # được khung phản hồi thật khi cược tay thành công:
                        #   {"gameID":101,"groupID":506,"areaID":506001,
                        #    "areaType":85,"bOk":true}
                        # Không có betSerialNumber, không có gameNo. Nhờ ``groupID`` ta vẫn
                        # biết chắc phản hồi thuộc bàn đang mở, không phụ thuộc chỉ số
                        # socket (socket nhận qua ``queryObjects`` bị lệch số).
                        group_here = body.get("groupID")
                        if group_here is not None and table_group is not None:
                            try:
                                same_table = int(group_here) == int(table_group)
                            except (TypeError, ValueError):
                                same_table = False
                            if not same_table:
                                continue
                            reply = candidate
                            break
                        # Khớp 3 — dự phòng cho mirror khác: phản hồi không mang serial
                        # cũng không mang groupID thì đòi đúng ván VÀ đúng socket đã gửi.
                        if body.get("gameNo") is not None and body.get("gameNo") != game_no:
                            continue
                        if (body.get("gameNoRound") is not None
                                and body.get("gameNoRound") != round_no):
                            continue
                        raw_socket = candidate.get("socket")
                        if raw_socket is not None:
                            try:
                                local_in_channel = (int(raw_socket)
                                                    - int(candidate.get("offset") or 0))
                            except (TypeError, ValueError):
                                continue
                            if local_in_channel != int(local_index):
                                continue
                        if reply is None or at > float(reply.get("at") or 0):
                            reply = candidate
                    if reply is not None:
                        break
                if reply is not None:
                    body = reply.get("data") or {}
                    # Socket đã nhận phản hồi của bàn này = socket CƯỢC thật của bàn. Nhớ
                    # lại để tay kế gửi đúng chỗ, thay vì chọn "khung 21 mới nhất" (nhiều
                    # socket cùng nhận khung 21, gửi vào socket sảnh thì sàn im lặng).
                    # Khoá theo URL vì chỉ số socket đổi khi trang mở/đóng kết nối.
                    if table_group is not None:
                        url = self._socket_url(int(socket_index))
                        self._remember_socket(int(table_group), int(socket_index), url)
                    # ``ok``/``serial`` được hook chép từ cả cấp ngoài lẫn ``data``; ``None``
                    # = khung không mang trường đó (mirror cũ), khi đó mới xét ``body``.
                    ok_flag = reply.get("ok")
                    if ok_flag is None:
                        ok_flag = body.get("bOk")
                    return {"ok": bool(ok_flag), "frame": frame, "reply": body,
                            "error": "" if ok_flag else "server_rejected"}
                time.sleep(0.15)
        # Hết giờ: trả kèm bằng chứng để lần sau đọc log là biết ngay vì sao không khớp
        # (định dạng phản hồi đổi? hook không thấy khung 22? chỉ số socket sai?).
        try:
            snapshot = self.state(fresh=True) or {}
        except Exception:
            snapshot = {}
        seen = sorted({str(value) for value in (snapshot.get("protocols") or {}).values()})
        # Gửi xong mà không khớp được phản hồi: bỏ ký ức socket cũ vì nó không còn đáng
        # tin (có thể lệnh đã đi vào socket sảnh — sàn im lặng). Vẫn giữ ``sent`` để nơi
        # gọi kiểm tra chip thật trước khi kết luận hỏng.
        if want_group is not None:
            try:
                self._forget_socket(int(want_group))
            except (TypeError, ValueError):
                pass
        urls = snapshot.get("urls") or []
        sent_url = (str(urls[int(socket_index)]) if 0 <= int(socket_index) < len(urls)
                    else "")
        # Bằng chứng cho lần chạy sau: mọi khung 22 đã thấy (socket, groupID, bOk, thời điểm
        # so với lúc gửi) để biết phản hồi đến từ socket nào, hoặc sàn có trả lời không.
        replies = []
        for item in (snapshot.get("recent22") or [])[-6:]:
            if not isinstance(item, dict):
                continue
            body = item.get("data") or {}
            try:
                lag = round(float(item.get("at") or 0) - sent_at)
            except (TypeError, ValueError):
                lag = None
            replies.append({"socket": item.get("socket"), "group": body.get("groupID"),
                            "bOk": body.get("bOk", item.get("ok")), "lagMs": lag,
                            "raw": str(body)[:160]})
        return {"ok": False, "frame": frame, "error": "no_reply", "sent": True,
                "detail": {"group": table_group, "serial": serial,
                           "socket": socket_index, "url": sent_url, "at": sent_at,
                           "sentLocalIndex": local_index, "route": route,
                           "recent22": len(snapshot.get("recent22") or []),
                           "replies22": replies,
                           "seen": snapshot.get("seen") or {},
                           "protocols": seen[:12]}}

    @staticmethod
    def _same_serial(candidate, serial):
        """Phản hồi có đúng ``betSerialNumber`` của lệnh vừa gửi không.

        Hook chép sẵn ``serial`` (đã gộp cả cấp ngoài và trong ``data``); nếu khung không
        mang trường này thì đọc thẳng từ ``data``.
        """
        value = candidate.get("serial")
        if value is None:
            value = ((candidate.get("data") or {}).get("betSerialNumber"))
        try:
            return int(value) == int(serial)
        except (TypeError, ValueError):
            return False

# ------------------------------------------------------------------ dùng chung
def install_hook(session):
    """Cài hook WebSocket cho tab, cho mọi khung đang mở và cho target iframe WM.

    Dùng khi người dùng đang đứng trong bàn: không tải lại tab hay iframe. Socket đã tạo
    trước hook được nhận lại bằng ``adopt_existing`` (``Runtime.queryObjects``) nên vẫn
    đặt cược được mà không phải mở lại bàn.
    """
    helper = WMSocket(session)
    ok = False
    try:
        ok = bool(helper._attach_page())
        helper._attach_oopif()
        try:
            helper.install_everywhere()
        except Exception:
            pass
        try:
            helper.adopt_existing()
        except Exception:
            pass
    except Exception:
        return False
    finally:
        helper._close()
    return ok


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
