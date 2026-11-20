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
│  ├─ config.py              hằng số chung (nhịp quét, gate đặt chip, CDP…)
│  ├─ adapters/              Chrome/Selenium/CDP: đọc DOM, bấm chuột, đặt chip
│  │  ├─ browser.py          mở/gắn Chrome CDP 9222, đọc bàn, đọc số dư
│  │  ├─ lobby_stream.py     đọc sảnh, quét ô bàn, bấm mở bàn bằng chuột thật
│  │  ├─ reader.js           logic đọc Big Road / bead road trong trang
│  │  ├─ table_bets.py       bấm chip, đọc chip đang đặt
│  │  ├─ human_mouse.py      chuyển động chuột "người thật" qua CDP
│  │  ├─ mouse_chromedriver.py  fallback ChromeDriver
│  │  └─ ghost_mouse.py      con trỏ ảo vẽ trong trang
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
3. Mở **ô bàn đầu tiên có mã bàn**. Không chấm điểm/lọc cầu, không bỏ bàn Siêu
   Tốc Độ, không đối chiếu shoe.
4. Bấm đúng ô bàn bằng pointer event thật, chờ URL xác nhận bàn: EVO dùng
   `table_id`, Petalive dùng đường dẫn `/live-games/bcr/<id>`, WM chờ
   `iframe_101` (khung bàn) hiện ra.
5. Xác nhận chỉ cần đọc được cầu trong bàn.
6. **Dừng ở đây**: bàn đã vào nhưng **chưa có lệnh cược**, chưa bấm chip
   (`sim.running = false`, `pending = null`).

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
cấp cầu cho tool và tự nghe nền (`start_wm_watch`) để không bỏ lỡ `protocol 26`
(mỗi ván chỉ gửi một lần).

WM **chưa** tự bấm chip: app chỉ đọc lá bài/cầu, gợi ý cửa và hiện tổng cược 3 cửa
của cả bàn; việc đặt chip vẫn do người dùng bấm trên bàn.

### Đặt cược WM (`wm_bet.py`, `wm_ws.py`)

Port nguyên tắc an toàn từ dự án LONG-LONG cũ (`bcr/adapters/longlong_table.py`) sang bàn
WM, thêm đường WebSocket:

1. **Chia tiền theo mệnh giá khay** (`models/chips.decompose`, quy hoạch động): khay WM
   in `10 / 20 / 50 / 100 / 500 / 1k` theo nghìn đồng nên **1 đơn vị sảnh = 1.000đ**
   (chip `10` = 10.000đ). Không chia khít được thì **không đặt** (không bao giờ đặt thiếu).
2. **Đường nhanh — WebSocket `protocol 22`** (`wm_ws.py`): gửi
   `{"betSerialNumber", "gameNo", "gameNoRound", "betArr": [{"betArea", "addBetMoney"}],
   "commission": 0, "remark": "1,0"}` với `betArea` 1 = Banker, 2 = Player, 3 = Hoà.
   Không phụ thuộc khay chip/toạ độ nên không bị lớp phủ chia bài che. Server trả
   `{"protocol": 22, "bOk": true}` là lệnh đã vào.
   Socket của trang chỉ tạo một lần lúc tải trang, nên app cài hook `WebSocket` và **tải
   lại tab một lần** khi bật tool (sau đó tự mở lại đúng bàn). Sàn đẩy `protocol 21` cho
   nhiều bàn trên cùng socket, nên socket được chọn theo `groupID` khớp bàn đang mở.
3. **Đường dự phòng — bấm chuột thật** (`wm_bet.py`): chờ lớp phủ chia bài tan, chọn
   mệnh giá, bấm chip → bấm ô → **đọc lại số tiền đang treo sau từng chip**; lệch thì
   bấm nút huỷ toàn bộ và báo lỗi; đủ tiền mới bấm xác nhận.
4. **Đọc chip của mình**: `mybet_money` trên từng ô (`read_bets`) trả cùng định dạng
   `table_bets.read` nên `compare_chips` / `table_is_clear` dùng lại được.

Không có bàn nào có mã bàn thì tool tự quét lại (lượt đầu ngay, sau đó mỗi 15 giây)
tới khi hết `ENTER_VALID_TIMEOUT = 240` giây thì báo lỗi. Nút **Dừng tự quét** huỷ
ngay (`table.cancelEnter`).

Bật **Chạy tool** khi đang đứng ở bàn đã xác nhận thì chuyển thẳng sang theo dõi,
vẫn chờ hết tay hiện tại rồi mới nhận lệnh từ UI. Bật tool khi chưa có bàn thì tool
tự chạy chuỗi "vào bàn" rồi theo dõi.

## Đọc bàn live + dự đoán (site Petalive BCR)

Bộ đọc **WebSocket thật** của Petalive (`w-plms*.petalive.live/live-games/bcr/...`)
nằm ở `aibcr/live/`:

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
nhất (`best_side`) và cờ `playable`. `playable = false` khi EV tốt nhất không vượt
`2 × sai số Monte Carlo` — khi đó mọi lệnh AUTO bị từ chối.

## AI trên giao diện Electron

Tab **AI dự đoán** bật/tắt luồng live mà không cần terminal:

1. Chọn nguồn: `off` · `local` (Ollama/llama.cpp, **không cần key**) · `api`
   (OpenAI-compatible, cần key). Endpoint/model mặc định:
   `https://api.vilao.ai/v1/chat/completions` và `chib/deepseek-v4.1-flash`.
2. Bấm **Lưu cấu hình AI**, rồi **Bật AI live**. Engine gắn CDP 9222, đọc WebSocket
   bàn Petalive, cập nhật shoe và gọi AI mỗi N ván (mặc định 5) trong **thread riêng**
   — không chặn vòng quét/đặt chip.
3. Panel hiện: xác suất P/B/T, EV tốt nhất ± sai số, % shoe đã chia, verdict
   (`BỎ TAY` / `Cân nhắc` / `Đánh`), mức tin cậy, rủi ro và giải thích. Nút
   **Phân tích ngay** gọi AI ngay cho ván kế.

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

**AI không tham gia quyết định đặt cược**: AI bị ép trả `BỎ TAY` khi
`playable=false`, và controller chỉ lấy cửa từ `prediction.best_side` — không đọc
`analysis.focus_side` do AI viết. AI cũng không được trả số liệu mới; mọi con số trên
UI đều lấy từ `predict.predict()`.

## Lệnh cược từng tay + tự đặt chip thật

Không còn thang cược hay khối mạnh/yếu. Trong tab **Bàn chơi**, phần **TAY KẾ TIẾP**:

- **Cửa**: `Theo AI (AUTO)` hoặc `P` / `B` / `T`.
- **Chip cố định**: số VN2 đặt cho tay đó (mặc định 100).
- **Lưu lệnh** (`bet.config`): chỉ đổi cấu hình cho tay kế.
- **Tự đặt cược** (`bet.place`): lưu form rồi chốt lệnh ngay.

Hành vi của `bet.place`:

1. Từ chối nếu chưa vào bàn, chưa chạy tool, đang chờ tay hiện tại kết thúc, đang
   bấm lượt khác, hoặc đang tắt `auto_place`.
2. `AUTO`: chỉ lấy cửa khi `predict` cho `playable=true` và có `best_side`. Nếu
   `playable=false` / `best_side=null` thì từ chối với thông báo **BỎ TAY**, kể cả AI
   có nói "Đánh". Muốn tự ép cửa thì chọn P/B/T thay vì AUTO.
3. Chốt lệnh vào `sim.pending`, chờ gate `table_is_clear()` (cả 3 cửa về 0) rồi gọi
   `TB.place_bet(...)` — bấm chip thật trên site, có tuỳ chọn chuột người thật.
4. Chỉ tính lãi/lỗ khi có xác nhận đã bấm chip trong cửa sổ đúng. Tay nào không đặt
   được thì ghi `not_placed`, không đụng vốn; sau `PLACE_SKIP_LIMIT` tay liên tiếp thì
   dừng để người dùng kiểm tra khay chip.
5. `PLACE_CONFIRM_TIMEOUT` / `PLACE_*` trong `aibcr/config.py` giới hạn thời gian gate
   và xác nhận, tránh thread chết làm treo phiên.

Sổ vốn, lịch sử và chốt lời vẫn dùng `aibcr/models/money.py`; dữ liệu lưu ở file
state (không chứa API key).

## Lệnh JSON-RPC

```
ping, state,
chrome.open|attach|close|window|status,
lobby.enter|sweep|back,
tables, table.enterValid|cancelEnter|open|select|leave,
tool.start|stop, scan, chips.read,
live.start|stop|state|analyze, ai.key.set,
bet.config|bet.place,
config.get|set, session.new|reset|recover, log.clear, export
```

UI chỉ gọi `table.enterValid` cho thao tác vào bàn; `table.open`/`table.select` giữ
lại cho script.

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
