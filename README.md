# AI_BCR Desktop

Tool **Baccarat** (một trò duy nhất) theo kiến trúc MVC: engine Python đọc bàn qua
Chrome CDP, giao diện Electron chỉ vẽ và gửi lệnh. Dự án **không có LONG-LONG** và
**không còn phương pháp chia khối/thang cược cũ**: mỗi tay được cấu hình trực tiếp
trên UI (cửa + số chip cố định) hoặc lấy cửa từ kết quả `predict`.

> **BẮT BUỘC — push GitHub sau MỖI lần cập nhật hoặc sửa đổi**
>
> Remote: `https://github.com/vzetiZero/AI_BCR.git` (nhánh `main`).
> Mọi thay đổi (code, cấu hình mặc định, tài liệu) phải được đẩy lên ngay trong cùng
> lần làm việc, không để tồn đọng. Chạy test trước khi push.
>
> ```
> push_update.bat                          # commit "Update <thời gian>" rồi push
> push_update.bat "mô tả thay đổi"         # commit kèm mô tả
> powershell -ExecutionPolicy Bypass -File .\push_update.ps1 "mô tả"
> ```
>
> Script `push_update.ps1` chạy `git add -A` (tôn trọng `.gitignore`:
> `chrome_profile*`, `aibcr_state*.json`, `drivers`, `__pycache__`…), commit rồi
> `git push origin main`. Không kết thúc công việc khi commit còn cục bộ.

## Cấu trúc

```
AI_BCR/
├─ aibcr/                    backend Python (MVC)
│  ├─ config.py              hằng số chung (nhịp quét, gate đặt cược, CDP…)
│  ├─ adapters/              Chrome/Selenium/CDP: đọc DOM, bấm trong trang, đặt cược
│  │  ├─ browser.py          mở/gắn Chrome CDP 9222, đọc bàn, đọc số dư
│  │  ├─ lobby_stream.py     đọc sảnh, quét ô bàn, mở bàn bằng element.click()
│  │  ├─ reader.js           logic đọc Big Road / bead road trong trang
│  │  ├─ table_bets.py       đọc chip đang đặt (chỉ đọc, không bấm)
│  │  ├─ wm_bet.py           đặt cược WM qua WebSocket (protocol 22)
│  │  ├─ wm_ws.py            giữ socket của bàn WM, gửi lệnh cược
│  ├─ live/                  bộ đọc WebSocket Petalive + dự đoán + AI
│  │  ├─ cards.py            id lá 1..52 → điểm, đếm bài còn lại
│  │  ├─ decode.py           tách khung `onGetGameResult`
│  │  ├─ reader.py           gắn CDP, phát on_round/on_state
│  │  ├─ predict.py          Monte Carlo + Markov + EV (nguồn số liệu)
│  │  ├─ ai.py               gọi AI local/API, chỉ diễn giải
│  │  └─ session.py          trạng thái một bàn, chạy AI ở thread riêng
│  ├─ models/                logic thuần, không I/O
│  │  ├─ engine.py           trạng thái ván, SIDE_NAME
│  │  ├─ money.py            sổ tiền, định dạng
│  │  ├─ chips.py            mệnh giá chip
│  │  └─ live_sim.py         vòng đời phiên, đối chiếu chip, ghi lãi/lỗ
│  ├─ controllers/
│  │  └─ baccarat.py         BaccaratController - composition root
│  └─ service/engine.py      transport JSON-RPC stdin/stdout
├─ ui/                       Electron
│  ├─ main.js                spawn engine, IPC, cửa sổ
│  ├─ preload.js             contextBridge `window.aibcr`
│  └─ renderer/              index.html / app.js / styles.css
├─ tests/                    unittest + node --test
├─ tools/                    script chạy tay (đọc WebSocket, soi CDP, kiểm chọn bàn)
├─ Start.bat                 mở app (tự cài Electron nếu thiếu)
├─ install-toan-bo.bat       cài Python deps + Electron
└─ push_update.bat/.ps1      commit + push lên GitHub
```

Luồng một lệnh: Electron → `preload.js` → `ui/main.js` → `aibcr.service.engine`
→ controller → adapter/model, rồi sự kiện `state`/`log` trả ngược lên giao diện.

## Cài đặt và chạy

```
install-toan-bo.bat      # lần đầu: pip deps + Electron
Start.bat                # mở app
```

Chạy engine bằng tay (CLI):

```
python -m aibcr --game baccarat
python -m aibcr.service.engine --game baccarat
```

Cấu hình Chrome: cổng CDP **9222**, profile `chrome_profile/`, file phiên
`aibcr_state_baccarat.json` (xem `aibcr/service/engine.py::GAMES`).

## Một nút duy nhất: VÀO BÀN HỢP LỆ

Tab **Bàn chơi** có một nút thao tác bàn: **VÀO BÀN HỢP LỆ**
(`table.enterValid`). Các nút cũ (Gắn Chrome / Nhận tab sảnh / Quét sảnh / Đọc lại
bàn / Rời bàn / Đóng Chrome) đã bị bỏ để tránh bấm nhầm. Danh sách bàn bên dưới chỉ
để **xem**, không bấm được.

Một lần bấm chạy đúng chuỗi:

1. Gắn Chrome CDP 9222 (chưa mở thì mở Chrome profile rồi yêu cầu đăng nhập + mở
   sảnh, bấm lại nút).
2. Nhận tab sảnh → quét toàn bộ danh sách (cuộn hết, gom mọi ô bàn đã render).
   Nhận cả sảnh EVO (`/frontend/evo/r3`), sảnh Petalive (`/live-games/lobby/bcr`) và
   sảnh **WM/M88** (tab có `iframe_109`/`iframe_101`); với Petalive, cầu được đọc
   trực tiếp từ ô bàn, còn WM đọc lịch sử + lá bài qua WebSocket nhị phân.

   **Cổng vào không bị giới hạn**: `start_url` chỉ cần là HTTPS công khai, nên dùng được
   bất kỳ cổng/mirror nào, kể cả bản mobile như
   `https://m.9vnv.com/vn/wm_casino?from=casino`. Nhưng trang **casino bọc ngoài không
   phải sảnh** — nó không chứa danh sách bàn. Phải bấm vào **sảnh Baccarat bên trong**
   (WM đặt sảnh trong `iframe_109`, bàn trong `iframe_101`) rồi mới bấm “VÀO BÀN HỢP LỆ”.

   **WM nhúng trong iframe khác origin (OOPIF)**: nhiều cổng bọc WM bằng iframe trỏ sang
   máy chủ khác, ví dụ `9vnv.com/vn/wm_casino` → `wmvn.m8810.com/?sid=…&co=wm`. Khi đó
   Chrome chặn `iframe.contentDocument` **và** tách iframe thành target riêng, nên cả dò
   DOM lẫn `Network.enable` trên tab mẹ đều không thấy WM. App xử lý bằng
   `aibcr/adapters/wm_targets.py`: `Target.getTargets` → chọn target iframe WM →
   `Target.attachToTarget(flatten)` → chạy `WM_DETECT_JS` và `Network.enable` **trong
   target đó**.

   **Đứng ở trang cổng là quét được luôn** — không phải copy link iframe ra tab mới.
   Cách làm: lấy **chính URL trong iframe** (`wmvn.m8810.com/?sid=…&co=wm`) và mở nó
   ngay trong tab đó (`BrowserSession.lift_wm_iframe_to_tab`). Sau bước này tab là WM
   "thật" nên đọc cầu, đọc **số dư ví** và đặt cược qua WebSocket chạy nguyên logic cũ,
   không cần nhánh iframe nào. Chỉ đổi URL của tab đang mở (không mở tab mới, không đóng
   gì), chỉ khi URL iframe là HTTPS công khai và trông giống WM.

   **URL cuối cùng là WM, nhưng `start_url` là CỔNG của người dùng.** Sau khi đưa iframe
   ra tab, app suy ra trang gốc của cổng và ghi vào `start_url`:

   - ưu tiên URL tab mẹ đang mở (`https://miso88-7.com/w/game-lobby/casino` →
     `https://miso88-7.com/w/`);
   - nếu không còn tab mẹ thì lấy tham số `returnurl` trong URL WM;
   - `wm_url` (chứa `sid` dùng một lần) được lưu riêng để tham khảo, **không** dùng làm
     `start_url` vì mở lại sẽ hỏng;
   - `start_url_locked = true` khi người dùng tự đặt link → bước tự học **không ghi đè**
     (họ có thể dùng mirror khác với `returnurl` của link WM).

   Nếu WM không mở được, app điều hướng tab về `portal_url` **một lần** (chống lặp 60
   giây) để lấy `sid` mới rồi ghi nhớ lại.

   **Nhận diện sảnh/bàn theo TRẠNG THÁI HIỂN THỊ**, không chỉ sự tồn tại: layout
   một-document (không có `iframe_101/109`) giữ `#game_list_box` trong DOM nhưng đặt
   `visibility: hidden` khi đang ở bàn. Detector cũ vì thế luôn trả `lobby`, nên app
   không bao giờ thấy "đã vào bàn". Nay `#exit_game_btn` và `#userBalanceBox` đang hiện
   là dấu hiệu bàn.

   **Đọc cầu khi tab mở sẵn:** CDP chỉ phát khung WebSocket của socket được tạo **sau**
   `Network.enable`. Vì vậy `WMReader` nạp lại riêng `iframe_109` (sảnh) sau 6 giây nếu
   chưa thấy khung nào — khung sảnh mang protocol 25/26 (kết quả + lịch sử), và nạp nó
   không đụng `iframe_101` nên bàn đang mở vẫn nguyên.

   **Số dư ví:** `WM_WALLET_JS` đọc `#userBalanceBox` lần lượt trong `iframe_101`,
   `iframe_109`, rồi khung gốc, nên đọc được **cả khi đang ở sảnh** (trước đây chỉ đọc
   khi ở trong bàn và báo "chưa đọc được").

   WM nhận diện **theo nội dung trước, theo id iframe sau**: `#game_list_box` / ô
   `groupBac_<game>_<group>_<type>` cho sảnh, `#container` có `gameNNN groupNNN` /
   `#bigRoadContainer` / `#userBalanceBox` cho bàn. Nhờ vậy mirror hoặc bản mobile đổi
   id iframe vẫn nhận được. `WM_DETECT_JS` trả thêm `evidence` (đã thấy dấu hiệu gì) và
   `title` để log/diagnose nói rõ thay vì chỉ báo “không thấy”.
3. Mở **ô bàn đầu tiên có mã bàn**. Không chấm điểm/lọc cầu, không bỏ bàn Siêu
   Tốc Độ, không đối chiếu shoe.
4. Bấm đúng ô bàn bằng `element.click()` **ngay trong trang** (không gửi sự kiện chuột
   CDP), chờ URL xác nhận bàn: EVO dùng
   `table_id`, Petalive dùng đường dẫn `/live-games/bcr/<id>`, WM chờ
   `iframe_101` (khung bàn) hiện ra.
5. Xác nhận chỉ cần đọc được cầu trong bàn.
6. **Dừng ở đây**: bàn đã vào nhưng **chưa có lệnh cược**, chưa đặt cược
   (`sim.running = false`, `pending = null`).

### Chẩn đoán “Chưa thấy tab sảnh Baccarat”

Thông báo này nghĩa là app **không tìm thấy sảnh trong Chrome do app mở**, không phải
lỗi đăng nhập. Ba nguyên nhân thường gặp:

1. Chrome đang xem **trang casino bọc ngoài** (`wm_casino`) chứ chưa bấm vào sảnh
   Baccarat bên trong.
2. Sảnh đang mở trong **cửa sổ Chrome khác**, không phải cửa sổ app mở (cổng CDP 9222).
   Hai cửa sổ cùng tài khoản còn làm site báo “Bạn đã đăng xuất”.
3. Sảnh còn đang tải, iframe danh sách bàn chưa render.

Dùng **Cấu hình → Kiểm tra tab Chrome** (`lobby.diagnose`): app liệt kê từng tab kèm
kết luận `Sảnh EVO / Petalive / WM — dùng được` hay `Không phải sảnh/bàn Baccarat` và
các dấu hiệu đã thấy (`iframe_109`, `game_list_box`, `groupBac_…`).

### Bàn xáo lại bài (số tay về 0)

Shoe mới có quá ít ván nên xác suất/EV của `predict` gần như không có cơ sở: lịch sử
shoe trước không dùng lại được. Vì vậy tool theo dõi số tay của **chính bàn đang đặt**:

- Số tay tụt về 0 và **giữ nguyên 0** ít nhất `SHOE_RESET_CONFIRM_SECONDS` (5 giây) →
  coi là xáo bài thật. Mốc đỉnh phải từng > 0, nên lúc bộ đọc live vừa gắn lại (nạp
  lịch sử chưa xong, tạm báo 0 tay) **không** bị nhầm.
- Khi đó tool `lobby_back()` về sảnh, dừng phiên và bật chế độ săn `min_hands_hunt`:
  chỉ vào bàn đang có **≥ `min_hands` tay** (mặc định 15). Số lượt quét tối đa
  `MIN_HANDS_HUNT_ROUNDS` (12); quá số đó mà không có bàn đủ tay thì vào bàn nhiều tay
  nhất kèm cảnh báo để không kẹt vô hạn.
- **`min_hands` áp cho MỌI lần vào bàn**, không chỉ sau xáo bài: bấm “VÀO BÀN HỢP LỆ”
  hay tool tự tìm đều bỏ bàn chưa đủ tay. Trong nhóm hợp lệ, app chọn bàn **nhiều tay
  nhất**. Có chốt an toàn sau khi mở: nếu bàn vừa mở vẫn ít tay hơn `min_hands` (sau 8
  giây cho lịch sử nạp) thì bỏ và tìm bàn khác.
- Khi rời bàn vì xáo bài, **cầu cũ của bàn đó bị xoá khỏi danh sách** — trước đây cầu
  40 tay còn sót lại khiến bộ lọc tưởng bàn vẫn đủ tay và vào lại chính bàn chỉ còn 1-2
  tay.
- Trong lúc săn lại, `_follow_open_table` không bám lại bàn vừa rời và
  `_tool_retry_enter` không mở bàn bất kể số tay.
- Tắt bằng `leave_on_shoe_reset = false`; `min_hands = 0` thì không lọc số tay.

Cấu hình trong tab **Cấu hình → Tùy chọn vận hành**: checkbox *Bàn xáo bài (0 tay) thì
rời bàn tìm bàn khác* và ô *Số tay tối thiểu của bàn khi vào lại*.

### Sảnh WM (M88)

`aibcr/adapters/wm.py` giải mã dữ liệu thật của WM đã đối chiếu với 783 ván:

- `protocol 26` — lịch sử từng bàn: mã kết quả + `resultContent` chứa danh sách lá
  của Banker (`b:`) và Player (`p:`).
- `protocol 25` — ván vừa ngã: `dtCard` khoá `1,3,5` là Player, `2,4,6` là Banker.
- `protocol 24` — từng lá được chia (dùng cho AI live).
- mã kết quả: `code & 3` → 1 = Banker, 2 = Player, 0 = Hoà; bit 3/4 là đôi Banker/
  Player; bit 5/6 là có/không rút lá thứ 3; bit 7/8 là natural.
- id lá 1..80, mỗi chất 20 mã: `rank = (id - 1) % 20 + 1` (14..20 là mã trống).

Bàn đang mở được nhận ra qua class `game101 group<id>` của `#container` trong
`iframe_101`, nên app chỉ đọc đúng bàn đang xem dù sảnh gửi kết quả của mọi bàn trên
cùng một socket. `aibcr/live/wm_reader.py` cấp lá bài cho AI live; `browser.scan()`
cấp cầu cho tool và tự nghe nền (`start_wm_watch`).

Vào bàn là nạp ngay lịch sử `protocol 26` (tối đa 400 ván của shoe) nên AI có xác
suất từ tay đầu thay vì phải chờ vài ván; shoe dự phòng chỉ trừ **40 ván cuối** vì
shoe thật đã chia hết từ lâu. WM phát lại `protocol 26` sau **mỗi** ván, thường
tới **muộn hơn** `protocol 25`; `LiveSession` bỏ ván trùng theo `(gameNo, gameNoRound)`
(nếu thiếu thì theo bộ lá) và ghép lịch sử theo đúng số tay, nên cầu không bị cộng
đôi và “Tay N” luôn bằng tay thật của bàn.

`protocol 25` **không** mang số tay. Bộ đọc gán số tay cho kết quả theo **lịch sử
`protocol 26`** (khớp bộ lá, không thì = ván cuối + 1), không lấy số tay của
`protocol 21`, vì WM phát lại vòng cược vừa xong sau khi ván đã ngã — nhận bản phát
lại đó sẽ làm “Tay N” lùi và lệnh cược bị arm lại cho tay cũ. Đổi shoe (`gameNo`
khác) thì xoá cầu cũ, số tay đếm lại từ đầu.

Khi AI live đang chạy, cầu WebSocket là nguồn chuẩn cho bàn đang theo dõi: lượt CDP
bắt đầu trước khi ván mới về (cầu ngắn hơn) bị bỏ qua thay vì ghi đè, tránh báo
“Cầu đổi nguồn” oan và huỷ lệnh đang chờ đặt cược.

WM đặt cược theo cấu hình (mục “Đặt cược WM” bên dưới) qua WebSocket; app không tự đặt
khi chưa bật tool.

### Đặt cược WM (`wm_bet.py`, `wm_ws.py`)

Đặt cược WM đi **100% qua WebSocket** — app không mô phỏng chuột ở bất kỳ đâu:

1. **Chia tiền theo mệnh giá khay** (`models/chips.decompose`, quy hoạch động): khay WM
   in `10 / 20 / 50 / 100 / 500 / 1k` theo nghìn đồng nên **1 đơn vị sảnh = 1.000đ**
   (chip `10` = 10.000đ). Không chia khít được thì **không đặt** (không bao giờ đặt thiếu),
   trừ chế độ ALL-IN được bật rõ ràng.
2. **Gửi lệnh — WebSocket `protocol 22`** (`wm_ws.py`), dùng hook đã bắt được socket của
   **iframe bàn**: `{"betSerialNumber", "gameNo", "gameNoRound",
   "betArr": [{"betArea", "addBetMoney"}], "commission": 0, "remark": "1,0"}` với
   `betArea` 1 = Banker, 2 = Player, 3 = Hoà. Server trả `{"protocol": 22, "bOk": true}` là
   lệnh đã vào; `bOk: false` thì báo lỗi — **không rơi về bấm chuột**.
   Hook được cài bằng `Page.addScriptToEvaluateOnNewDocument`, **không bao giờ tải lại
   tab** (tải lại sẽ đẩy Chrome về sảnh và mất bàn đang đứng). Socket chọn theo `groupID`
   khớp bàn đang mở **và** phải thuộc iframe bàn — socket khung gốc nhận được dữ liệu bàn
   nhưng server từ chối lệnh cược gửi từ đó.
3. **Xác minh** bằng số chip thật đang treo trên bàn (`mybet_money`): lệch là huỷ ngay và
   báo lỗi, không giữ lệnh sai.
4. **Đọc chip của mình**: `mybet_money` trên từng ô (`read_bets`) trả cùng định dạng
   `table_bets.read` nên `compare_chips` / `table_is_clear` dùng lại được.
5. **EVO chỉ đọc**: bảng cầu và số chip đang trên bàn vẫn đọc bình thường, nhưng đặt cược
   EVO bị khoá (báo “Đặt cược EVO đã khoá — chỉ hỗ trợ đặt qua WebSocket (WM)”).

Không có bàn nào có mã bàn thì tool tự quét lại (lượt đầu ngay, sau đó mỗi 15 giây)
tới khi hết `ENTER_VALID_TIMEOUT = 240` giây thì báo lỗi. Nút **Dừng tự quét** huỷ
ngay (`table.cancelEnter`).

### Quy tắc một nút (chuẩn hiện tại)

Người dùng chỉ cần mở Chrome đứng ở sảnh, rồi bấm **Chạy tool**:

1. tool tự gắn Chrome/ mở tab, nhận sảnh, quét và mở bàn đầu tiên có mã bàn;
2. tự theo dõi cầu và **tự đặt cược từng tay** theo cấu hình (cửa `AUTO/P/B/T` + số đơn
   vị); mỗi tay chỉ arm một lần (khoá theo chuỗi cầu), chờ bàn dọn chip rồi mới bấm.
   WM công bố kết quả xong mới mở cửa cược ván kế (~25–35 giây sau) nên gate chờ
   tối đa `BET_GATE_TIMEOUT = 45` giây trước khi bấm;
3. nếu chưa vào được bàn (Chrome vừa mở, chưa đăng nhập) tool tự thử lại mỗi 15 giây;
4. bấm **Dừng tool** → dừng phiên và tự rời bàn về sảnh.

Cửa **AUTO** đánh đúng **cửa có xác suất ra cao nhất** do `predict` tính ở mỗi tay
(không phụ thuộc `playable`), nên tay nào có dự đoán là tay đó được đặt. Muốn tự ép cửa
thì chọn cố định P/B/T. Số tiền: **1 đơn vị = 1.000đ** (WM in khay chip theo nghìn
đồng), mặc định `10` = 10.000đ, đổi ngay bằng các nút chip nhanh `10/20/50/100/500/1k`.

### Nguồn chọn cửa khi để AUTO

`auto_side_source` quyết định cửa cho lệnh AUTO:

| Giá trị | Cách chọn cửa |
|---|---|
| `hybrid` (**mặc định**) | Cầu đọc được rõ thì **đi theo cầu** (bệt → nối cửa, 1:1 → đảo cửa); nhịp lộn xộn mới theo xác suất. Nhờ vậy cầu bệt Player 6 tay sẽ đặt Player, không bị đặt ngược sang Banker. |
| `road` | Chỉ bám nhịp cầu đang ra; không đọc được nhịp rõ thì vẫn theo xác suất. |
| `probability` | Luôn cửa có xác suất ra cao nhất. Trên shoe 8 bộ, Banker thắng ~46% và Player ~44%, nên chế độ này chọn **Banker ở phần lớn tay** — kể cả khi cầu đang bệt Player. Chỉ chọn khi người dùng muốn vậy. |

`predict` trả thêm `road_follow` (side/pattern/reason) nhưng **không trộn** nó vào
`probabilities`: baccarat là trò độc lập nên "cầu" không tạo lợi thế thật, đây chỉ là
cách bám nhịp khi người dùng chủ động chọn. Chọn ở tab **Bàn chơi → TAY KẾ TIẾP →
Khi cửa là AUTO, lấy cửa theo**.

Mức cược mỗi tay đi qua **cơ chế mức cược** (mục “Lệnh cược từng tay”): `flat` giữ
nguyên mức gốc, `win_repeat_loss_double` thắng quay về mức gốc · thua gấp đôi, có trần
`bet_max_stake` và luôn bị chặn bởi số dư phiên.

## Đọc bàn live + dự đoán

Bộ đọc **WebSocket thật** nằm ở `aibcr/live/` — WM đọc khung nhị phân (protocol 21/24/
25/26), Petalive đọc khung text. Cả hai cùng đổ vào `LiveSession`:

```
python tools/dump_ws_frames.py --seconds 180     # gắn Chrome CDP đang mở, ghi khung
python tools/cdp_capture.py --url "<link bàn>"   # tự mở Chrome CDP riêng rồi ghi
python tools/bcr_live.py --trials 20000          # đọc live + in dự đoán từng ván
python tools/bcr_live.py --ai                    # thêm phần diễn giải AI (cần key)
```

Mã hoá lá bài đã đối chiếu **129/129 ván thật**:
`rank = (id-1)//4 + 1`, điểm = rank nếu ≤ 9, còn lại 0 (10/J/Q/K). Dự đoán chạy local
20k ván Monte Carlo ≈ 0,15 giây, đủ nhanh cho cửa sổ cược ~25 giây.

**Số liệu là của `predict.py`**: xác suất P/B/T, EV, sai số, shoe còn lại, cửa tốt
nhất (`best_side`), cửa có **xác suất cao nhất** (`top_side`) và cờ `playable`.
`playable = false` khi EV tốt nhất không vượt `2 × sai số Monte Carlo` — chỉ là nhãn
cảnh báo, không còn chặn lệnh AUTO.

**Dạng cầu** (`pattern_stats`): đếm số tay mỗi cửa, độ dài chuỗi đang chạy, và tỉ lệ
cửa kế tiếp của những lần trước có **cùng cửa + cùng độ dài chuỗi** (cần ≥ 8 mẫu mới
dùng). Tín hiệu này **chỉ để hiển thị/mô tả**, không trộn vào xác suất: lịch sử ngắn
không chứng minh được cầu bệt hay 1-1 sẽ lặp lại, nên 1-1 không tạo lợi thế toán học.
Xác suất P/B/T chỉ đến từ bộ bài còn lại (Monte Carlo) + Markov phụ trợ 15%.


## AI trên giao diện Electron

Tab **AI dự đoán** bật/tắt luồng live mà không cần terminal:

1. Chọn nguồn: `off` · `local` (Ollama/llama.cpp, **không cần key**) · `api`
   (OpenAI-compatible, cần key). Endpoint/model mặc định:
   `https://api.vilao.ai/v1/chat/completions` và `chib/deepseek-v4.1-flash`.
2. Bấm **Lưu cấu hình AI**, rồi **Bật AI live**. Engine gắn CDP 9222, đọc WebSocket
   bàn Petalive, cập nhật shoe và gọi AI mỗi N ván (mặc định 5) trong **thread riêng**
   — không chặn vòng quét/đặt cược.
3. Panel hiện: xác suất P/B/T, EV tốt nhất ± sai số, % shoe đã chia, verdict
   (`BỎ TAY` / `Cân nhắc` / `Đánh`), mức tin cậy, rủi ro và giải thích. Nút
   **Phân tích ngay** gọi AI ngay cho ván kế.

Card **Bàn đang theo dõi** cập nhật theo từng ván WebSocket (không chờ lượt quét):

- **Tay hiện tại**: số tay lấy từ `gameNoRound` của bàn WM (Petalive đếm theo lịch
  sử) nên không còn đứng ở tay cũ; lịch sử cũ được nạp khi vào bàn nên xác suất có
  ngay từ tay đầu, và ván phát lại không bị cộng đôi;
- **Big Road**: sơ đồ cột 6 hàng đúng quy ước (lặp cửa xuống dòng, đổi cửa mở cột,
  đủ 6 hàng thì đi sang phải), dựng từ `road.big_road()`;
- **Dạng cầu**: dãy chuỗi gần nhất + câu `pattern_note` (số tay mỗi cửa, chuỗi tương
  tự trong lịch sử, cửa có xác suất cao nhất).

**Key AI chỉ có 2 nguồn, không bao giờ nằm trong repo:**

- Biến môi trường `BCR_AI_KEY` (đặt trước khi chạy `Start.bat`), ví dụ PowerShell:

  ```
  $env:BCR_AI_KEY = "<key của bạn>"
  .\Start.bat
  ```

- Ô **API key tuỳ biến** trong tab AI: engine nhận qua `ai.key.set` và giữ **trong
  RAM của tiến trình**. Ô nhập bị xoá ngay sau khi bấm Lưu; key không vào `config`,
  file state, log, snapshot hay JSON-RPC trả về. Bấm **Xoá key** để quay về env.

Các biến tuỳ chọn khác: `BCR_AI_MODE`, `BCR_AI_MODEL`, `BCR_AI_URL`, `BCR_AI_TIMEOUT`.

Nếu từng dán key vào code/chat/bug report thì coi như key đã lộ: **xoay key đó** ở
nhà cung cấp. Không dán giá trị key vào README, test, log hay issue.

**AI không tham gia quyết định đặt cược**: AI chỉ viết nhận định/giải thích; controller
chỉ lấy cửa từ số liệu `predict` (`top_side` = xác suất cao nhất, hoặc `road_follow` khi
`auto_side_source` là `road`/`hybrid`) — không đọc `analysis.focus_side` do AI viết. AI
cũng không được trả số liệu mới; mọi con số trên UI đều lấy từ `predict.predict()`.

## Lệnh cược từng tay + tự đặt cược

Không còn thang cược hay khối mạnh/yếu. Trong tab **Bàn chơi**, phần **TAY KẾ TIẾP**:

- **Cửa**: `Theo AI (AUTO)` hoặc `P` / `B` / `T`.
- **Mức gốc mỗi tay**: số đơn vị cấu hình cho tay đầu (mặc định 10 đv = 10.000đ).
- **Cơ chế mức cược** (`bet_progression`):
  - `flat` — **Cược cố định mỗi tay**: luôn đúng mức gốc, không đổi theo thắng/thua.
- `win_repeat_loss_double` — **Thắng về mức gốc · thua gấp đôi**: tay trước thắng thì
  tay sau **quay lại đúng mức gốc đã cấu hình** (cấu hình 20 → thắng ở mức 80 vẫn đặt
  lại 20, không giữ mức 80); tay trước thua thì gấp đôi mức vừa đặt. Hoà (push) giữ
  nguyên mức; tay **không đặt được cược** thì **bắt đầu lại từ mức gốc**.
  - **Mức cược tối đa** (`bet_max_stake`, 0 = không giới hạn): chặn trần khi gấp đôi; mức
  cược luôn bị chặn thêm bởi số dư phiên để không gửi lệnh vượt tiền thật.
  - **All-in khi số dư cuối không chia khít chip**: ví còn 636 đv mà lệnh cần 640 thì
    đặt **630** (500+100+20+10 = mức lớn nhất ghép được bằng mệnh giá khay,
    `chips.affordable`) thay vì bỏ tay. Chỉ bật khi lệnh bị **số dư** cắt; bị
    `bet_max_stake` cắt thì vẫn tôn trọng trần cấu hình.
- **Lưu lệnh** (`bet.config`): lưu cửa + mức gốc + cơ chế + mức tối đa cho tay kế.
- **Tự đặt cược** (`bet.place`): lưu form rồi chốt lệnh ngay.

**Thang cược tính theo SỐ TIỀN THẬT đã lên bàn**, không theo số dự định:

- `LiveSimulation.mark_placed(placed_stake=…)` nhận số sàn xác nhận; `settled_stake()`
  dùng số này cho cả sổ vốn và thang cược. Không có thì mới dùng số trong lệnh.
- Nhờ vậy lệnh 200 mà trần bàn/ví chỉ nhận 100 thì: lãi/lỗ tính trên **100** (thắng
  Banker = +95, không phải +190), lịch sử ghi 100, và thắng thì tay sau về đúng mức gốc.
- Khi sàn nhận ít hơn lệnh, `place.status` có `short = true` cùng `placed_stake` /
  `requested_stake`, và UI/log nói rõ *“sàn chỉ nhận 100/200 đv — đã tính theo 100 đv
  thực tế”*.


Cơ chế mức cược chỉ đọc **kết quả thật của tay đã đặt được cược** (`LiveSimulation`
ghi `progress_stake`/`last_outcome` khi kết toán); đổi mức gốc hoặc cơ chế thì thang
cược bắt đầu lại từ mức mới. Mỗi tay chỉ arm một lần, công thức thuần nằm ở
`models/live_sim.py::progress_stake`.

Hành vi của `bet.place`:

1. Từ chối nếu chưa vào bàn, chưa chạy tool, đang chờ tay hiện tại kết thúc, đang
   đặt lượt khác, hoặc đang tắt `auto_place`.
2. `AUTO`: lấy cửa theo `auto_side_source` (mặc định `top_side` = cửa có xác suất ra
   cao nhất; `road`/`hybrid` dùng nhịp cầu). Nếu snapshot cũ không có `top_side` thì tự
   tính từ `probabilities`. Chỉ từ chối khi không có cửa nào. AI có nói gì cũng không
   đổi cửa.
3. Chốt lệnh vào `sim.pending`, chờ gate `table_is_clear()` (cả 3 cửa về 0) rồi gọi
   `WMBet(...).place(...)` — gửi lệnh cược qua WebSocket (protocol 22). EVO chỉ đọc:
   bấm đặt cược EVO sẽ báo “Đặt cược EVO đã khoá — chỉ hỗ trợ đặt qua WebSocket (WM)”.
4. Chỉ tính lãi/lỗ khi có xác nhận chip đã lên bàn trong cửa sổ đúng. Tay nào không đặt
   được thì ghi `not_placed`, không đụng vốn; sau `PLACE_SKIP_LIMIT` tay liên tiếp thì
   dừng để người dùng kiểm tra bàn.
5. `PLACE_CONFIRM_TIMEOUT` / `PLACE_*` trong `aibcr/config.py` giới hạn thời gian gate
   và xác nhận, tránh thread chết làm treo phiên.

Sổ vốn, lịch sử và chốt lời vẫn dùng `aibcr/models/money.py`; dữ liệu lưu ở file
state (không chứa API key).

### Âm báo (chuông KPI + còi sự cố)

`ui/renderer/alarm.js` tổng hợp âm thanh bằng Web Audio (không cần file media):

- **Chuông thành công** — hợp âm đi lên C5-E5-G5-C6 (sine + harmonic nhẹ) khi **đủ KPI**:
  `money.blocked[0] == "TAKE_PROFIT"` hoặc `table.take_profit_done`.
- **Còi sự cố** — 4 xung quét 850↔1300Hz khi có lỗi thật (lệch tiền, không đặt được
  chip, mất kết nối/đọc cầu, hết vốn, xung đột cầu, mất phiên…).

Quy tắc phát:

- Chỉ kêu khi **trạng thái đổi**; render đầu tiên sau khi mở app chỉ ghi mốc, nên mở app
  lúc phiên đã chốt lời sẵn không bị giật mình.
- `level === "error"` là tín hiệu **chính** — lỗi mới thêm ở backend tự hú còi mà không
  phải sửa danh sách mẫu. Danh sách `ALARM_PATTERNS` chỉ mở rộng cho thông điệp
  `warn`/`info` mang tính sự cố.
- `ALARM_SUPPRESS` chặn các lần dừng **hợp lệ** (dừng tool, rời bàn, chốt lời) để không
  hú oan; danh sách này cố tình hẹp để không nuốt mất sự cố thật.
- Cùng nội dung lỗi: chờ `ALARM_THROTTLE_MS` (2,5s) mới kêu lại. Lỗi **khác** nội dung
  chỉ cần cách `ALARM_DISTINCT_GAP_MS` (0,8s), nên sự cố thứ hai không bị nuốt.
- Backend ghi log `error` cho mọi trạng thái tạm dừng do sự cố (`_log_sim_pause`), vì
  `sim.note` không đi qua event — nếu chỉ đặt note thì UI im lặng.
- Bật/tắt và âm lượng nhớ trong `localStorage`; có nút **Thử lỗi** và **Thử thành công**.

### Lịch sử cược lưu bền vững (tab Lịch sử)

Mỗi tay đã kết toán được ghi nối thêm (append-only JSONL) vào `aibcr_history.jsonl`
cạnh file state — xem `aibcr/models/history.py`. Vì vậy:

- Mở lại app **không mất lịch sử**: `session.reset` / `session.new` chỉ tạo sổ vốn mới,
  file lịch sử giữ nguyên. State file vẫn chỉ có 500 dòng cuối, nhưng file JSONL tích
  luỹ đủ mọi tay.
- Ghi trùng được chặn bằng khoá `(armed_at, table_key, hand)` nên gọi lưu nhiều lần
  (mỗi lần đổi state) không sinh bản ghi trùng, và nạp lại file vẫn giữ đúng khoá vì
  `armed_at` được lưu cùng dòng.
- Tab Lịch sử hiển thị **tích luỹ** (tối đa `HISTORY_ROWS_SHOWN` dòng gần nhất) kèm
  dải thống kê: tổng tay, thắng/thua/hoà, tỉ lệ thắng, tổng cược, lãi/lỗ tích luỹ.
- **Xuất file** (`history.export`) với `format` = `csv` (có BOM để Excel đọc đúng tiếng
  Việt), `json`, hoặc `txt` (báo cáo cột cố định kèm phần tổng hợp). Nội dung lấy từ
  file tích luỹ chứ không phải 200 dòng đang hiển thị.
- **Reset lịch sử** (`history.reset`) là đường duy nhất xoá dữ liệu, và luôn chép bản
  sao sang `aibcr-history-<thời điểm>.jsonl` trước khi xoá để còn đối chiếu. UI hỏi xác
  nhận kèm số dòng sẽ xoá.

## Lệnh JSON-RPC

```
ping, state,
chrome.open|attach|close|window|status,
    lobby.enter|sweep|back, lobby.diagnose,
tables, table.enterValid|cancelEnter|open|select|leave,
tool.start|stop, scan, chips.read,
live.start|stop|state|analyze, ai.key.set,
bet.config|bet.place,
config.get|set, session.new|reset|recover, history.reset|export, log.clear, export
```

UI chỉ gọi `table.enterValid` cho thao tác vào bàn; `table.open`/`table.select` giữ
lại cho script.

Giao diện dùng **bản nháp cho mọi ô cấu hình**: gõ/đổi giá trị nào thì ô đó giữ nguyên
bản nháp cho tới khi bấm nút Lưu tương ứng, kể cả khi engine đẩy snapshot mới (2
giây/lần); nhờ vậy đổi ô này không làm mất giá trị đang sửa ở ô khác. Lưu xong có
**hộp thoại xác nhận** liệt kê đúng những gì vừa lưu (`flat` cũng như tiến trình).

## Kiểm thử

```
python -m unittest discover -s tests -t . -q      # toàn bộ test Python
python -m unittest tests.test_engine -v           # engine JSON-RPC + vào bàn + lệnh cược
python -m unittest tests.test_enter_valid_flow -v # harness pipeline chọn bàn (Chrome giả)
python -m unittest tests.test_live_ai -v          # AI, key RAM, bet.place, chặn override
python -m unittest tests.test_architecture -v     # ranh giới tầng MVC, không LONG-LONG
node tests/test_reader.cjs                        # test logic đọc bàn (node --test)
```

Kiểm tra thực tế ở mức chọn bàn (Chrome đang mở sảnh, CDP 9222):

```
python tools/kiem_tra_chon_ban.py                  # đọc sảnh, in danh sách bàn
python tools/kiem_tra_chon_ban.py --vao-ban        # bấm ô bàn đầu tiên có mã + chờ đọc
                                                    # được cầu, KHÔNG đặt cược
```

## Ghi chú

- **Push GitHub bắt buộc sau mỗi lần cập nhật/sửa đổi** —
  `https://github.com/vzetiZero/AI_BCR.git`, nhánh `main`. Chạy test trước khi push;
  nếu push lỗi (mạng, xác thực) thì sửa và push lại.
- **Đóng Chrome bằng tay**: app dò cổng CDP (mỗi 2 giây, cache 3 giây) nên phát hiện
  ngay, tạm dừng phiên và xoá danh sách bàn cũ. Bấm **VÀO BÀN HỢP LỆ** để vào lại —
  không cần khởi động lại app.
- Không có LONG-LONG: `aibcr/__init__.py`, `service/engine.py::GAMES` và
  `tests/test_architecture.py` đều chặn ký hiệu LONG-LONG quay lại.
