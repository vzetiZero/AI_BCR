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
│  │  ├─ method.py           kế hoạch cược, so chip, ladder (không lọc bàn)
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
nhầm. Danh sách bàn bên dưới chỉ để **xem** (số tay, bên mạnh, mã bàn), không bấm được.

Một lần bấm chạy đúng chuỗi:

1. Gắn Chrome CDP 9222 (chưa mở thì mở Chrome profile rồi yêu cầu đăng nhập + mở
   sảnh, bấm lại nút).
2. Nhận tab sảnh → quét toàn bộ danh sách (cuộn hết, gom mọi ô bàn đã render).
3. Mở **ô bàn đầu tiên có mã bàn**. Không còn chấm điểm/lọc cầu, không bỏ bàn Siêu
   Tốc Độ, không đối chiếu shoe.
4. Bấm đúng ô bàn bằng pointer event thật, chờ URL có `table_id`.
5. Xác nhận chỉ cần đọc được cầu trong bàn (không kiểm tra ổn định/cùng shoe).
6. **Dừng ở đây**: bàn đã vào nhưng **chưa đặt cược** (`sim.running = false`,
   `pending = null`, không arm). Trạng thái ghi `Đã vào bàn … CHƯA đặt cược`.

Không có bàn nào có mã bàn trong sảnh thì tool tự quét lại (lượt đầu ngay, sau đó mỗi
15 giây) tới khi hết `ENTER_VALID_TIMEOUT = 240` giây thì báo lỗi và dừng chế độ. Nút
**Dừng tự quét** trên băng trạng thái huỷ ngay (`table.cancelEnter`).

Bật **Chạy tool** khi đang đứng ở bàn đã xác nhận thì chuyển thẳng sang theo dõi, vẫn
chờ hết tay hiện tại rồi mới chốt kế hoạch và (nếu bật `auto_place`) mới bấm chip.
Bật tool khi chưa có bàn thì tool tự chạy chuỗi "vào bàn" rồi theo dõi.

## Vào bàn không điều kiện

Đã bỏ **toàn bộ** logic điều kiện vào bàn:

- Không còn `check_table` / 5 điều kiện (khoảng tay, bệt tối đa, cầu 3-3, lệch B/P
  min/max) — `aibcr/models/method.py` chỉ còn `table_stats` + `strong_side`.
- Không bỏ bàn Siêu Tốc Độ, không đối chiếu cầu WebSocket/DOM, không chờ dữ liệu
  "ổn định", không kiểm tra `game_type`, không kiểm tra cùng shoe.
- Chỉ cần ô bàn có mã bàn là mở; nếu lúc đọc bàn chưa có cầu thì chờ tay đầu tiên.

Bên mạnh vẫn được tính để đánh khối: bên nhiều tay hơn trên toàn shoe; **B = P thì
lấy Banker (bên đỏ)**.

Chỉ còn **ngưỡng thoát khi đang chơi**: bệt hiện tại chạm `live_exit_streak` (mặc
định 7 tay) thì rời bàn — đây là điều kiện rời bàn, không phải lọc bàn.

## Đọc bàn live + dự đoán (site Petalive BCR)

Ngoài engine Evolution ở trên, dự án có bộ đọc **WebSocket thật** của site
Petalive (`w-plms*.petalive.live/live-games/bcr/...`) trong `aibcr/live/`:

| File | Việc |
| --- | --- |
| `cards.py` | id lá 1..52 → rank/điểm; `Shoe` đếm bài còn lại |
| `decode.py` | tách khung `;game;…;onGetGameResult;0;0;{json}` |
| `reader.py` | gắn CDP, phát `on_round` / `on_state` |
| `predict.py` | Monte Carlo + Markov + EV |
| `ai.py` | gọi API AI (tuỳ chọn) để diễn giải |

Mã hoá lá bài đã đối chiếu **129/129 ván thật**:
`rank = (id-1)//4 + 1`, điểm = rank nếu ≤ 9, còn lại 0 (10/J/Q/K).

```
python tools/dump_ws_frames.py --seconds 180     # gắn Chrome CDP đang mở, ghi khung
python tools/cdp_capture.py --url "<link bàn>"   # tự mở Chrome CDP riêng rồi ghi
python tools/bcr_live.py --trials 20000          # đọc live + in dự đoán từng ván
python tools/bcr_live.py --ai                    # thêm phần diễn giải AI (cần BCR_AI_KEY)
```

Mỗi ván in lá bài + điểm, cửa thắng, và xác suất/EV cho ván kế. Dự đoán chạy local
20k ván Monte Carlo ≈ 0,15 giây (100k ≈ 0,8 giây), đủ nhanh cho cửa sổ cược 25 giây.

AI API (OpenAI-compatible) **không** tham gia quyết định: số liệu do `predict.py`
tính, AI chỉ viết lời giải thích. Đặt `BCR_AI_KEY`/`BCR_AI_MODEL`/`BCR_AI_URL` trong
biến môi trường; không lưu key vào repo.

Lưu ý trung thực: EV của Baccarat luôn âm ở mọi cửa khi shoe cân bằng, nên
`predict` trả `playable = false` và khuyến nghị bỏ tay; card counting chỉ có ý nghĩa
khi shoe lệch mạnh (hiếm) và không đảm bảo thắng.

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
python -m unittest tests.test_engine -v           # engine JSON-RPC + vào bàn + không lọc cầu
python -m unittest tests.test_enter_valid_flow -v # harness pipeline chọn bàn (Chrome giả)
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
