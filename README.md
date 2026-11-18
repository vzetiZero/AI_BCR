# AI_BCR Desktop

Tool **Baccarat** (một trò duy nhất) theo kiến trúc MVC: engine Python đọc bàn qua
Chrome CDP, giao diện Electron chỉ vẽ và gửi lệnh. Toàn bộ logic nghiệp vụ của bản
BCR gốc được giữ nguyên — **không có LONG-LONG**.

> **BẮT BUỘC — push GitHub sau MỖI lần cập nhật hoặc sửa đổi**
>
> Remote: `https://github.com/vzetiZero/AI_BCR.git` (nhánh `main`).
> Mọi thay đổi (code, cấu hình mặc định, tài liệu) phải được đẩy lên ngay trong cùng
> lần làm việc, không để tồn đọng.
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
│  ├─ config.py              hằng số chung (nhịp quét, ngưỡng, đường dẫn)
│  ├─ adapters/              Chrome/Selenium/CDP: đọc DOM, bấm chuột, đặt chip
│  │  ├─ browser.py          mở/gắn Chrome CDP 9222, đọc bàn, đọc số dư
│  │  ├─ lobby_stream.py     đọc sảnh, quét ô bàn, bấm mở bàn bằng chuột thật
│  │  ├─ reader.js           logic đọc Big Road / bead road trong trang
│  │  ├─ table_bets.py       bấm chip, đọc chip đang đặt
│  │  ├─ human_mouse.py      chuyển động chuột "người thật" qua CDP
│  │  ├─ mouse_chromedriver.py  fallback ChromeDriver
│  │  └─ ghost_mouse.py      con trỏ ảo vẽ trong trang
│  ├─ controllers/
│  │  └─ baccarat.py         BaccaratController - composition root
│  ├─ models/                logic thuần, không I/O
│  │  ├─ engine.py           trạng thái ván, SIDE_NAME
│  │  ├─ method.py           lọc bàn, kế hoạch cược, so chip, ladder
│  │  ├─ money.py            sổ tiền, định dạng
│  │  ├─ chips.py            mệnh giá chip
│  │  └─ live_sim.py         LiveSimulation + cổng kiểm tra dữ liệu
│  └─ service/engine.py      transport JSON-RPC stdin/stdout
├─ ui/                       Electron
│  ├─ main.js                spawn engine, IPC, cửa sổ
│  ├─ preload.js             contextBridge `window.aibcr`
│  └─ renderer/              index.html / app.js / styles.css
├─ tests/                    unittest + node --test
├─ tools/                    script chạy tay (backtest, kiểm tra chọn bàn)
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

Tab **Bàn chơi** chỉ có một nút thao tác bàn: **VÀO BÀN HỢP LỆ**
(`table.enterValid`). Các nút cũ (Gắn Chrome / Nhận tab sảnh / Quét sảnh / Đọc lại
bàn / Rời bàn / Đóng Chrome) và nút chọn bàn theo từng dòng đã bị bỏ để tránh bấm
nhầm. Danh sách bàn bên dưới chỉ để **xem** (đạt/loại + lý do), không bấm được.

Một lần bấm chạy đúng chuỗi:

1. Gắn Chrome CDP 9222 (chưa mở thì mở Chrome profile rồi yêu cầu đăng nhập + mở
   sảnh, bấm lại nút).
2. Nhận tab sảnh → quét toàn bộ danh sách (cuộn hết, gom mọi ô bàn đã render).
3. Chấm từng bàn bằng 5 điều kiện + 4 cổng kỹ thuật, bỏ bàn Siêu Tốc Độ và bàn vừa
   rời; chọn bàn "giữa khung" (`|số tay − 30|` nhỏ nhất).
4. Bấm đúng ô bàn bằng pointer event thật, chờ URL có `table_id`.
5. Xác nhận cầu trong bàn: 2 lần đọc giống nhau, `source=evo-bead`, cùng shoe với
   cầu đã lọc ở sảnh.
6. **Dừng ở đây**: bàn đã vào & đã xác nhận nhưng **chưa đặt cược**
   (`sim.running = false`, `pending = null`, không arm). Trạng thái ghi
   `Đã vào bàn hợp lệ … CHƯA đặt cược`.

Không có bàn đạt thì tool tự quét lại (lượt đầu ngay, sau đó mỗi 15 giây) tới khi hết
`ENTER_VALID_TIMEOUT = 240` giây thì báo lỗi và dừng chế độ. Nút **Dừng tự quét**
trên băng trạng thái huỷ ngay (`table.cancelEnter`).

Bật **Chạy tool** khi đang đứng ở bàn đã xác nhận thì chuyển thẳng sang theo dõi, vẫn
chờ hết tay hiện tại rồi mới chốt kế hoạch và (nếu bật `auto_place`) mới bấm chip.
Bật tool khi chưa có bàn thì tool tự chạy chuỗi "vào bàn hợp lệ" rồi theo dõi.

## Luật lọc bàn Baccarat

`aibcr/models/method.py::check_table` — phải đạt cả 5 điều kiện:

1. Số tay đã đi trong khoảng `min_hands`–`max_hands` (mặc định 10–50)
2. Không có vệt dài hơn `max_streak` (mặc định 3 tay; tay Hoà không tính)
3. Chưa từng có cầu 3-3 (hai vệt 3 tay liền nhau)
4. Chênh lệch B/P **≥ `min_lead`** (mặc định 3)
5. Chênh lệch B/P **≤ `max_lead`** (mặc định 5; `0`/trống = không giới hạn)

Bên mạnh = bên nhiều tay hơn trên toàn shoe; **B = P thì ưu tiên bên đỏ (Banker)**
(`tie_side = "B"`, đổi được sang `"P"` hoặc `"run"` = theo vệt).

Trước khi tính cầu, danh sách bàn còn phải qua cổng kỹ thuật: cầu WebSocket khớp
cầu DOM (`ws_confirmed_letters`/`screen_stable ≥ 2`), dữ liệu ≤ 30 giây, không xung
đột, đúng `game_type = baccarat`, có mã bàn, và bỏ qua bàn Siêu Tốc Độ.

## Chia thang theo vốn

Cấu hình → **Vốn & thang**: nhập *Mức gốc*, *Vốn phiên*, chọn cách chia
(`ladder_mode`) rồi lưu. Quy tắc chia theo hệ số: mỗi bậc = bậc trước × hệ số, làm
tròn lên bội 10 đv, chỉ thêm bậc khi **tổng vốn còn nằm trong vốn phiên**.

## Lệnh JSON-RPC

```
ping, state,
chrome.open|attach|close|window|status,
lobby.enter|sweep|back,
tables, table.enterValid|cancelEnter|open|select|leave,
tool.start|stop, scan, chips.read,
config.get|set, session.new|reset|recover, log.clear, export
```

UI chỉ gọi `table.enterValid` cho thao tác vào bàn; `table.open`/`table.select` giữ
lại cho script.

## Kiểm thử

```
python -m unittest discover -s tests -t . -q      # toàn bộ test Python
python -m unittest tests.test_engine -v           # engine JSON-RPC + lọc bàn + nút vào bàn
python -m unittest tests.test_enter_valid_flow -v # harness pipeline chọn bàn (Chrome giả)
python -m unittest tests.test_architecture -v     # ranh giới tầng MVC, không LONG-LONG
node tests/test_reader.cjs                        # test logic đọc bàn (node --test)
```

Kiểm tra thực tế ở mức chọn bàn (Chrome đang mở sảnh, CDP 9222):

```
python tools/kiem_tra_chon_ban.py                  # đọc sảnh, in bảng ĐẠT/loại + lý do
python tools/kiem_tra_chon_ban.py --vao-ban        # bấm ô bàn đầu tiên ĐẠT + xác nhận
                                                   # cầu, KHÔNG đặt cược
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
