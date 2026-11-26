# -*- coding: utf-8 -*-
"""Hằng số dùng chung: thời gian chờ, ngưỡng dữ liệu, nhận diện bàn đặc biệt.

Mọi tầng (Model · Adapter · Controller · Service) import từ đây để nói cùng một
thang số; không chứa màu sắc hay thiết lập giao diện.
"""

from pathlib import Path

# Thư mục gốc dự án (config.py nằm trong aibcr/ nên lên một cấp).
HERE = Path(__file__).resolve().parents[1]

SCAN_SECONDS = 4
FAST_SCAN = 1.5
LIVE_STALE = 30
ENTRY_WAIT = 75
# Nút “VÀO BÀN HỢP LỆ”: tổng thời gian tối đa để tìm + mở + xác nhận cầu một bàn đạt
# chuẩn, kể cả khi phải quét lại nhiều lượt. Hết hạn thì báo lỗi, không treo UI.
ENTER_VALID_TIMEOUT = 240
# Vòng tự tìm bàn không có trần số lần: nghỉ CYCLE_RETRY_SECONDS giữa hai lượt quét.
CYCLE_RETRY_SECONDS = 15
CHIP_REFRESH = 3
# Gate "chờ ván mới": sau khi chốt lệnh cược, chờ tối đa BET_GATE_TIMEOUT giây để bàn
# dọn hết chip (cả 3 cửa về 0) rồi mới tự bấm — tránh bấm chồng lên ván cũ còn đang
# hiển thị/gom chip. BET_GATE_POLL là nhịp đọc lại trong lúc chờ.
# WM công bố kết quả xong mới mở cửa cược ván kế (~25–35 giây sau), nên phải chờ đủ
# lâu; 20 giây trước đây làm mọi lệnh bị huỷ oan ("Bàn chưa mở cược").
BET_GATE_TIMEOUT = 45.0
BET_GATE_POLL = 0.3
# Cả một lượt đặt cược (chờ bàn dọn chip → chờ cửa cược mở → gửi lệnh → đọc lại chip)
# phải xong TRƯỚC ``PLACE_CONFIRM_TIMEOUT``. Trước đây gate 45s + chờ cửa cược 45s + đọc
# chip 6s = tới 96s, trong khi sim tuyên bố "không đặt được" ở 60s: thread vẫn đặt tiếp và
# một lệnh THẬT có thể vào bàn cho tay đã bị ghi là chưa đặt — tiền không được theo dõi.
# Vì vậy dùng chung một hạn chót: chờ bàn dọn chip ≤ 30s, rồi phần còn lại của 50s dành
# cho chờ cửa cược + gửi lệnh + đọc lại chip — luôn kết thúc trước PLACE_CONFIRM_TIMEOUT.
PLACE_WINDOW = 50.0
PLACE_GATE_TIMEOUT = 30.0
# Vào bàn từ sảnh mất vài giây tải trang, khay chip/ô cược chưa kịp hiện nên lượt bấm
# đầu thường hỏng. Tay chưa đặt được chip thật thì không được tính (xem
# LiveSimulation.mark_placed), nhưng bỏ quá nhiều tay liên tiếp nghĩa là bàn đang đặt
# không được chút nào — dừng để người dùng nhìn thấy thay vì bỏ tay mãi.
PLACE_SKIP_LIMIT = 3
# Trần thời gian một lượt đặt cược còn được coi là "đang chạy" (gate chờ ván mới 20s +
# vài giây bấm/đọc chip). Quá hạn mà chưa có xác nhận thì coi như không đặt được — nhờ
# vậy thread chết giữa chừng không khiến việc tính tay bị treo vĩnh viễn.
PLACE_CONFIRM_TIMEOUT = 60.0
# Bàn xáo lại bài (số tay tụt về 0): dữ liệu shoe mới chưa đủ cho AI, nên rời bàn và
# tìm bàn còn đủ tay. Số tay tụt phải giữ nguyên ít nhất ngần này giây mới coi là xáo
# bài thật — tránh nhầm khi bộ đọc live vừa gắn lại và chưa nạp lịch sử.
SHOE_RESET_CONFIRM_SECONDS = 5.0
# Số tay tối đa vẫn coi là xáo bài sau khi bàn đã từng có nhiều tay. WM đôi khi còn
# báo 1 tay (ván đầu shoe mới) thay vì 0, nên ngưỡng này phải cao hơn 0 để không bỏ lỡ.
SHOE_RESET_MIN_HANDS = 1
# Số lượt săn tối đa khi đang lọc bàn theo ``min_hands``. Không có bàn nào đủ tay sau
# ngần này lượt thì nhận bàn nhiều tay nhất (kèm cảnh báo) để tool không kẹt vô hạn.
MIN_HANDS_HUNT_ROUNDS = 12
# Tab Lịch sử hiển thị bao nhiêu dòng gần nhất của file lịch sử tích luỹ. Toàn bộ dữ
# liệu vẫn còn trong aibcr_history.jsonl để xuất/xem lại.
HISTORY_ROWS_SHOWN = 2000
