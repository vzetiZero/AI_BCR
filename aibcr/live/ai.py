# -*- coding: utf-8 -*-
"""Gọi API AI (tuỳ chọn) để diễn giải kết quả đã tính — không dùng cho đường quyết định.

Nguyên tắc:

    - Số liệu (xác suất, EV, shoe) do ``predict`` tính local; AI chỉ viết lời giải thích.
    - Không có API key thì mọi hàm trả ``{"ok": False, "error": ...}`` và app vẫn chạy.
    - Chỉ dùng ``urllib`` (có sẵn), không thêm thư viện.

Cấu hình qua biến môi trường (không lưu vào repo):

    BCR_AI_KEY      API key
    BCR_AI_MODEL    tên model (mặc định gpt-4o-mini)
    BCR_AI_URL      endpoint chat/completions (mặc định OpenAI)
"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"
TIMEOUT = 20

SYSTEM_PROMPT = (
    "Bạn là trợ lý phân tích Baccarat. Bạn KHÔNG được bịa xác suất: chỉ dùng đúng "
    "các con số được đưa trong JSON. Trả lời tiếng Việt, tối đa 5 câu, nêu: cửa có EV "
    "tốt nhất, mức chênh so với 2 cửa còn lại, rủi ro chính (shoe cạn/ván vừa đổi "
    "nhịp), và khuyến nghị cược hay bỏ tay. Không hứa thắng, không khuyến khích tăng "
    "cược khi EV âm."
)


def config():
    return {
        "key": os.environ.get("BCR_AI_KEY", "").strip(),
        "model": os.environ.get("BCR_AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "url": os.environ.get("BCR_AI_URL", DEFAULT_URL).strip() or DEFAULT_URL,
    }


def available():
    return bool(config()["key"])


def build_prompt(prediction, recent_rounds=None, table=""):
    """Prompt gọn: chỉ số liệu đã tính, không kèm dữ liệu nhạy cảm."""
    payload = {
        "table": table,
        "prediction": {
            key: prediction.get(key) for key in
            ("probabilities", "ev", "best_side", "best_ev", "trials", "recommendation")
        },
        "monte_carlo": prediction.get("monte_carlo"),
        "markov": prediction.get("markov"),
        "shoe": prediction.get("shoe"),
        "recent_rounds": recent_rounds or [],
    }
    return ("Dữ liệu đã tính sẵn (JSON):\n%s\n\nHãy diễn giải và khuyến nghị ngắn gọn."
            % json.dumps(payload, ensure_ascii=False))


def analyze(prediction, recent_rounds=None, table="", timeout=TIMEOUT):
    """Gọi API AI; trả ``{"ok": True, "text": ...}`` hoặc lỗi có thể hiển thị."""
    cfg = config()
    if not cfg["key"]:
        return {"ok": False, "error": "Chưa đặt BCR_AI_KEY nên không gọi AI."}
    if not isinstance(prediction, dict) or not prediction.get("ok"):
        return {"ok": False, "error": "Chưa có kết quả tính toán để phân tích."}
    body = json.dumps({
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(prediction, recent_rounds, table)},
        ],
        "temperature": 0.2,
        "max_tokens": 400,
    }).encode("utf-8")
    request = Request(cfg["url"], data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg["key"],
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        return {"ok": False, "error": "AI trả lỗi HTTP %s." % exc.code}
    except URLError:
        return {"ok": False, "error": "Không kết nối được API AI (mạng/tường lửa)."}
    except (ValueError, OSError):
        return {"ok": False, "error": "Phản hồi AI không đọc được."}
    choices = data.get("choices") or []
    text = ((choices[0] or {}).get("message") or {}).get("content") if choices else ""
    if not text:
        return {"ok": False, "error": "AI không trả nội dung."}
    return {"ok": True, "text": str(text).strip(), "model": cfg["model"]}
