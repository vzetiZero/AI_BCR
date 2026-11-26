# -*- coding: utf-8 -*-
"""Gọi AI local hoặc API (OpenAI-compatible) để diễn giải kết quả đã tính.

Nguyên tắc giữ nguyên:

    - Số liệu (xác suất, EV, shoe) do ``predict`` tính local; AI **không** được bịa
      hay sửa số, và **không** được đổi quyết định ``playable``.
    - Thiếu cấu hình thì mọi hàm trả ``{"ok": False, "error": ...}`` và app vẫn chạy.
    - Chỉ dùng ``urllib`` (có sẵn), không thêm thư viện.
    - Không lưu key vào repo/env file/state: key chỉ đọc từ ``BCR_AI_KEY``, override RAM
      từ UI (``ai.key.set``), hoặc file cấu hình của công cụ khác trên máy
      (``discover_credentials`` — chỉ đọc lúc chạy, không chép giá trị đi đâu).

Chế độ (``BCR_AI_MODE`` hoặc truyền qua ``cfg`` từ UI):

    off     không gọi AI
    local   endpoint localhost (Ollama, llama.cpp…), **không cần key**
    api     endpoint OpenAI-compatible, cần ``BCR_AI_KEY``

Đường lùi (``fallback_*``): **chỉ** dùng khi provider chính lỗi thật (401/403/429/mất
mạng). Chính chạy được thì không gọi dự phòng, nên không tốn thêm token. Mặc định tắt.

Biến môi trường:

    BCR_AI_MODE             off | local | api (để trống = tự suy từ key/URL)
    BCR_AI_KEY              API key (chỉ cần cho api) — KHÔNG lưu vào repo
    BCR_AI_MODEL            tên model (mặc định deepseek-v4.1-flash)
    BCR_AI_URL              endpoint chat/completions (mặc định https://api.vilao.ai/...)
    BCR_AI_TIMEOUT          timeout giây (mặc định 8 cho UI)
    BCR_AI_DISCOVER         ``0`` để tắt tự dò key trong cấu hình công cụ khác
    BCR_AI_FALLBACK_*       mode/url/model/key của đường lùi
"""

import json
import os
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

DEFAULT_URL = "https://api.vilao.ai/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4.1-flash"
# Endpoint local mặc định của đường lùi (Ollama/llama.cpp/…). Không tốn token.
DEFAULT_FALLBACK_URL = "http://127.0.0.1:11434/v1/chat/completions"
TIMEOUT = 20           # giữ tương thích với CLI cũ
DEFAULT_TIMEOUT = 8    # cửa cược Petalive ~25s nên UI dùng 8s
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
MODES = ("off", "local", "api")
VERDICTS = ("BỎ TAY", "Cân nhắc", "Đánh")
CONFIDENCES = ("thấp", "trung bình", "cao")

SYSTEM_PROMPT = (
    "Bạn là trợ lý phân tích Baccarat. Bạn KHÔNG được bịa xác suất: chỉ dùng đúng "
    "các con số được đưa trong JSON. Trả lời tiếng Việt, tối đa 5 câu, nêu: cửa có EV "
    "tốt nhất, mức chênh so với 2 cửa còn lại, rủi ro chính (shoe cạn/ván vừa đổi "
    "nhịp), và khuyến nghị cược hay bỏ tay. Không hứa thắng, không khuyến khích tăng "
    "cược khi EV âm."
)

SYSTEM_PROMPT_JSON = (
    "Bạn là trợ lý phân tích Baccarat. Dữ liệu đã được tính sẵn bằng Monte Carlo; "
    "KHÔNG được bịa hoặc sửa số liệu. Chỉ trả một đối tượng JSON hợp lệ, không markdown, "
    "gồm đúng các khoá: verdict (một trong 'BỎ TAY'|'Cân nhắc'|'Đánh'), "
    "focus_side (một trong 'P'|'B'|'T' hoặc null), confidence (một trong "
    "'thấp'|'trung bình'|'cao'), risk (chuỗi tối đa 200 ký tự), explanation "
    "(chuỗi tối đa 400 ký tự). Nếu computed.playable là false thì verdict BẮT BUỘC "
    "là 'BỎ TAY', confidence là 'thấp'. Không hứa thắng, không khuyến khích tăng cược "
    "khi EV âm. Trả tiếng Việt."
)


def _text(value, limit):
    """Khai thác chuỗi an toàn, cắt độ dài để UI không phình."""
    if value is None:
        return ""
    return str(value).strip()[:max(1, int(limit))]


# Nơi OpenCode/AI agent lưu cấu hình provider. CHỈ ĐỌC lúc chạy để dùng lại đúng key
# mà app khác trên máy đang dùng; KHÔNG BAO GIỜ chép giá trị key vào repo, state, log
# hay snapshot. Người dùng có thể tắt bằng ``BCR_AI_DISCOVER=0``.
DISCOVER_PATHS = (
    "~/.config/opencode/opencode.json",
    "~/.config/opencode/opencode.jsonc",
    "~/AppData/Roaming/opencode/opencode.json",
    "~/.opencode/opencode.json",
)
# Base URL được ưu tiên khi tự dò (đúng provider người dùng đang dùng).
DISCOVER_HINT = "vilao"
# Cache kết quả tự dò: khoá là (path, mtime_ns, size) của từng file cấu hình.
_DISCOVER_CACHE = {}


def _read_json_loose(path):
    """Đọc JSON, chấp nhận JSONC (bỏ comment ``//`` ngoài chuỗi)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read(400000)
    except (OSError, UnicodeError):
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    out = []
    inside = False
    escape = False
    index = 0
    while index < len(text):
        char = text[index]
        if inside:
            out.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                inside = False
            index += 1
            continue
        if char == '"':
            inside = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        out.append(char)
        index += 1
    try:
        data = json.loads("".join(out))
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def discover_credentials():
    """Tìm provider OpenAI-compatible đang cấu hình sẵn trên máy.

    Trả ``{"key", "url", "model", "models", "source"}`` hoặc ``{}``. Chỉ đọc file cấu
    hình của công cụ khác (OpenCode); không ghi, không log giá trị key.

    Kết quả được cache theo ``(đường dẫn, mtime, kích thước)`` vì ``snapshot()`` gọi
    hàm này vài lần mỗi giây; file cấu hình đổi thì cache tự mất hiệu lực.
    """
    if _text(os.environ.get("BCR_AI_DISCOVER"), 8) in ("0", "off", "false", "no"):
        return {}
    signature = []
    for pattern in DISCOVER_PATHS:
        path = os.path.expanduser(pattern)
        try:
            info = os.stat(path)
        except OSError:
            signature.append((path, None, None))
            continue
        signature.append((path, info.st_mtime_ns, info.st_size))
    cache_key = tuple(signature)
    cached = _DISCOVER_CACHE.get("entry")
    if cached is not None and cached[0] == cache_key:
        return dict(cached[1])
    result = _discover_uncached()
    _DISCOVER_CACHE["entry"] = (cache_key, dict(result))
    return result


def _discover_uncached():
    found = []
    for pattern in DISCOVER_PATHS:
        path = os.path.expanduser(pattern)
        if not os.path.isfile(path):
            continue
        data = _read_json_loose(path)
        if not data:
            continue
        providers = data.get("providers")
        if not isinstance(providers, dict):
            continue
        for name, item in providers.items():
            if not isinstance(item, dict):
                continue
            settings = item.get("settings") if isinstance(item.get("settings"), dict) else {}
            key = _text(settings.get("apiKey") or item.get("apiKey"), 300)
            base = _text(settings.get("baseURL") or settings.get("baseUrl")
                         or item.get("baseURL") or item.get("baseUrl"), 300)
            if not key or not base:
                continue
            if not base.startswith(("http://", "https://")):
                continue
            models = []
            registry = item.get("models")
            if isinstance(registry, dict):
                models = [_text(ident, 120) for ident in registry.keys() if ident]
            preferred = str(name).lower() == DISCOVER_HINT
            found.append({
                "key": key, "url": base, "models": models,
                "model": models[0] if models else "",
                "source": "%s:%s" % (os.path.basename(path), name),
                "preferred": preferred,
            })
    if not found:
        return {}
    # Ưu tiên provider được nhắc tới trong ``DISCOVER_HINT`` (vilao), sau đó theo thứ tự
    # file để hành vi ổn định.
    preferred = [row for row in found if row.get("preferred")]
    picked = (preferred or found)[0]
    return {key: value for key, value in picked.items() if key != "preferred"}


def is_local_url(url):
    """True nếu endpoint trỏ tới máy local (Ollama/llama.cpp…)."""
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS


def completion_url(url):
    """Accept a provider base URL or a complete chat-completions endpoint."""
    raw = _text(url, 300).rstrip("/")
    try:
        parts = urlparse(raw)
    except ValueError:
        return ""
    if (parts.scheme not in ("http", "https") or not parts.netloc
            or parts.username is not None or parts.password is not None):
        return ""
    try:
        _ = parts.port
    except ValueError:
        return ""
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions" if path else "/v1/chat/completions"
    return urlunparse(parts._replace(path=path))


def models_url(url):
    """``.../v1/models`` suy ra từ base URL hoặc endpoint chat/completions."""
    complete = completion_url(url)
    if not complete:
        return ""
    return complete[:-len("/chat/completions")] + "/models"


_OVERRIDE_ALIASES = {
    "ai_mode": "mode", "ai_url": "url", "ai_model": "model", "ai_key": "key",
    "ai_timeout": "timeout",
    "ai_fallback_mode": "fallback_mode", "ai_fallback_url": "fallback_url",
    "ai_fallback_model": "fallback_model", "ai_fallback_key": "fallback_key",
}


def normalize_overrides(overrides):
    """Nhận cả dạng ``ai_*`` (cấu hình lưu trong state) lẫn dạng ngắn (``mode``…).

    Cấu hình của tab AI được lưu với tiền tố ``ai_`` (``ai_mode``, ``ai_url``…), còn
    ``config()`` dùng tên ngắn. Không chuẩn hoá ở đây thì cùng một dict đọc ra
    ``mode: off`` dù state ghi ``ai_mode: api`` — app tưởng AI đã tắt.
    """
    if not isinstance(overrides, dict):
        return {}
    out = dict(overrides)
    for alias, short in _OVERRIDE_ALIASES.items():
        if short not in out and alias in out:
            out[short] = overrides[alias]
    return out


def config(overrides=None):
    """Cấu hình hiệu lực: UI override > env > key tự dò trên máy > mặc định."""
    override = normalize_overrides(overrides)
    mode = _text(override.get("mode") or os.environ.get("BCR_AI_MODE"), 16).lower()
    # Không có key từ UI/env thì dùng key mà công cụ AI trên máy đang cấu hình sẵn.
    # Đây chỉ là ĐỌC lúc chạy: giá trị không được ghi vào repo/state/log/snapshot.
    discovered = {} if _text(override.get("key"), 300) else discover_credentials()
    if mode not in MODES:
        # Tương thích CLI cũ: có key -> api; endpoint local -> local; else off.
        url = (_text(override.get("url") or os.environ.get("BCR_AI_URL"), 300)
               or discovered.get("url") or DEFAULT_URL)
        key = (_text(override.get("key") or os.environ.get("BCR_AI_KEY"), 300)
               or discovered.get("key") or "")
        mode = "api" if key else ("local" if is_local_url(url) else "off")
    raw_url = (_text(override.get("url") or os.environ.get("BCR_AI_URL"), 300)
               or discovered.get("url") or DEFAULT_URL)
    url = completion_url(raw_url)
    model = (_text(override.get("model") or os.environ.get("BCR_AI_MODEL"), 80)
             or discovered.get("model") or DEFAULT_MODEL)
    key = (_text(override.get("key") or os.environ.get("BCR_AI_KEY"), 300)
           or discovered.get("key") or "")
    # Nhãn nguồn key — chỉ để hiển thị, KHÔNG chứa giá trị key.
    if _text(override.get("key"), 300):
        key_source = "ui"
    elif _text(os.environ.get("BCR_AI_KEY"), 300):
        key_source = "env:BCR_AI_KEY"
    elif discovered.get("key"):
        key_source = "discovered:%s" % (discovered.get("source") or "local")
    else:
        key_source = ""
    try:
        timeout = int(float(override.get("timeout") or os.environ.get("BCR_AI_TIMEOUT")
                            or DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    # Đường lùi: chỉ dùng khi provider chính lỗi (hết token/mất mạng). Mặc định là
    # endpoint local (Ollama) vì nó không tốn token; để trống = không có đường lùi.
    fallback_mode = _text(override.get("fallback_mode")
                          or os.environ.get("BCR_AI_FALLBACK_MODE"), 16).lower()
    if fallback_mode not in MODES:
        # Mặc định TẮT: không tự đoán người dùng có Ollama hay không. Bật trong tab AI
        # hoặc đặt BCR_AI_FALLBACK_MODE=local.
        fallback_mode = "off"
    fallback_url = completion_url(_text(override.get("fallback_url")
                                        or os.environ.get("BCR_AI_FALLBACK_URL"), 300)
                                  or DEFAULT_FALLBACK_URL)
    fallback_model = _text(override.get("fallback_model")
                           or os.environ.get("BCR_AI_FALLBACK_MODEL"), 80)
    # Key riêng cho đường lùi: cho phép provider dự phòng khác hẳn (khác hãng).
    fallback_key = _text(override.get("fallback_key")
                         or os.environ.get("BCR_AI_FALLBACK_KEY"), 300)
    if fallback_mode == "api" and not fallback_key and is_local_url(fallback_url):
        fallback_mode = "local"
    return {
        "mode": mode, "url": url, "model": model, "key": key,
        "key_source": key_source,
        "timeout": min(30, max(2, timeout)),
        "fallback_mode": fallback_mode, "fallback_url": fallback_url,
        "fallback_model": fallback_model or model,
        "fallback_key": fallback_key,
    }


def available(cfg=None):
    """Provider CHÍNH có đủ cấu hình để gọi không (chưa nói key còn hạn).

    Đây là con số UI hiển thị, nên phải nói thật: có đường lùi **không** làm provider
    chính thành "có sẵn". Dùng ``usable`` để biết AI có dùng được bằng bất kỳ đường nào.
    """
    settings = cfg or config()
    return _primary_usable(settings)


def usable(cfg=None):
    """AI có dùng được không: provider chính, hoặc đường lùi khi chính không dùng được."""
    settings = cfg or config()
    if settings.get("mode") == "off":
        # Người dùng chọn tắt: không gọi chính lẫn lùi.
        return False
    return _primary_usable(settings) or _fallback_settings(settings) is not None


def _primary_usable(settings):
    """Provider chính có đủ cấu hình để gọi không (chưa nói tới key còn hạn)."""
    mode = settings.get("mode")
    if mode == "local":
        return bool(settings.get("url"))
    if mode == "api":
        return bool(settings.get("key"))
    return False


def _config_error(settings):
    """Lỗi dễ hiểu, có tên biến môi trường để người dùng tự sửa."""
    mode = settings.get("mode")
    if mode == "api":
        return "Chưa cấu hình AI: đặt BCR_AI_KEY hoặc nhập key trong tab AI."
    if mode == "local":
        return "Chưa cấu hình AI local: kiểm tra BCR_AI_URL."
    return "Chưa bật AI: chọn chế độ local hoặc api."


def build_prompt(prediction, recent_rounds=None, table="", state="", time_remains=None,
                 history="", json_mode=False):
    """Prompt gọn: chỉ số liệu đã tính, không kèm dữ liệu nhạy cảm."""
    markov = prediction.get("markov") if isinstance(prediction.get("markov"), dict) else {}
    payload = {
        "table": str(table or ""),
        "state": str(state or ""),
        "time_remains": time_remains,
        "history_recent": str(history or "")[-60:],
        "computed": {
            "probabilities": prediction.get("probabilities"),
            "ev": prediction.get("ev"),
            "ev_uncertainty": prediction.get("ev_uncertainty"),
            "best_side": prediction.get("best_side"),
            "least_bad_side": prediction.get("least_bad_side"),
            "top_side": prediction.get("top_side"),
            "top_probability": prediction.get("top_probability"),
            "pattern_note": prediction.get("pattern_note"),
            "playable": bool(prediction.get("playable")),
            "recommendation": prediction.get("recommendation"),
            "monte_carlo": prediction.get("monte_carlo"),
            "markov": {key: markov.get(key) for key in
                       ("state", "transitions", "p_player", "p_banker", "p_tie")},
            "shoe": prediction.get("shoe"),
        },
        "recent_rounds": recent_rounds or [],
    }
    tail = ("Trả về JSON theo đúng schema yêu cầu."
            if json_mode else "Hãy diễn giải và khuyến nghị ngắn gọn.")
    return ("Dữ liệu đã tính sẵn (JSON) — KHÔNG được bịa hoặc sửa số:\n%s\n\n%s"
            % (json.dumps(payload, ensure_ascii=False), tail))


def _request(body, settings, timeout):
    headers = {"Content-Type": "application/json"}
    # Không có key thì KHÔNG gửi header ``Bearer `` rỗng: endpoint local (Ollama)
    # không cần key và một số server từ chối header rỗng.
    if settings.get("key"):
        headers["Authorization"] = "Bearer " + settings["key"]
    request = Request(settings["url"], data=json.dumps(body).encode("utf-8"),
                      method="POST", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace")), None
    except HTTPError as exc:
        return None, "AI trả lỗi HTTP %s." % exc.code
    except URLError:
        return None, "Không kết nối được API AI (mạng/tường lửa)."
    except (ValueError, OSError):
        return None, "Phản hồi AI không đọc được."


def _content(data):
    choices = (data or {}).get("choices") or []
    message = (choices[0] or {}).get("message") if choices else None
    return str((message or {}).get("content") or "").strip()


def check_model(cfg=None, timeout=12):
    """Make a tiny OpenAI-compatible request to verify URL, key, and model."""
    settings = config(cfg)
    if not settings["model"]:
        return {"ok": False, "error": "Nhập tên model trước khi kiểm tra."}
    if settings["mode"] == "off":
        return {"ok": False, "error": "Chọn chế độ API hoặc Local trước khi kiểm tra."}
    if settings["mode"] == "api" and not settings["key"]:
        return {"ok": False, "error": "Nhập API key hoặc đặt biến BCR_AI_KEY."}
    if not settings["url"]:
        return {"ok": False, "error": "Base URL không hợp lệ."}

    body = {
        "model": settings["model"],
        "messages": [{"role": "user", "content": "Reply with OK."}],
        # 4 token là quá ít với model reasoning: nó tiêu hết ngân sách cho phần suy
        # luận và trả ``content`` RỖNG (``finish_reason=length``), khiến bước kiểm tra
        # báo "model không trả nội dung" dù kết nối hoàn toàn tốt. 64 token đủ cho
        # model thường lẫn reasoning.
        "max_tokens": 64,
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if settings["key"]:
        headers["Authorization"] = "Bearer " + settings["key"]
    request = Request(settings["url"], data=json.dumps(body).encode("utf-8"),
                      method="POST", headers=headers)
    try:
        with urlopen(request, timeout=max(2, min(20, int(timeout)))) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        messages = {
            400: "Đã tới API nhưng provider từ chối model/yêu cầu (HTTP 400).",
            401: "API key không hợp lệ hoặc đã hết hạn (HTTP 401).",
            403: "API key không có quyền dùng model này (HTTP 403).",
            404: "Không tìm thấy endpoint hoặc model (HTTP 404).",
            429: "API nhận được yêu cầu nhưng đang giới hạn tốc độ/hết quota (HTTP 429).",
        }
        return {"ok": False, "code": exc.code, "error": messages.get(
            exc.code, "API trả lỗi HTTP %d." % exc.code)}
    except URLError:
        return {"ok": False, "error": "Không kết nối được Base URL (mạng/tường lửa)."}
    except (ValueError, OSError, TypeError):
        return {"ok": False, "error": "Phản hồi từ API không đúng định dạng JSON."}

    if not isinstance(data, dict) or data.get("error"):
        return {"ok": False, "error": "Provider trả phản hồi lỗi; kiểm tra model và định dạng API."}
    choice = ((data.get("choices") or [{}])[0] or {})
    message = choice.get("message") or {}
    # Model reasoning có thể trả ``content`` rỗng nhưng vẫn là phản hồi THÀNH CÔNG
    # (``finish_reason`` = stop/length và có ``reasoning_content``). Chỉ khi provider
    # trả về không có gì cả mới coi là thất bại.
    content = _content(data)
    reasoned = bool(message.get("reasoning_content") or message.get("reasoning"))
    if not content and not reasoned and not choice.get("finish_reason"):
        return {"ok": False, "error": "API đã phản hồi nhưng model không trả nội dung kiểm tra."}
    returned_model = _text(data.get("model"), 80)
    return {"ok": True, "model": returned_model or settings["model"],
            "message": "Kết nối thành công · model %s hoạt động." %
                       (returned_model or settings["model"])}


def list_models(cfg=None, timeout=12):
    """Liệt kê model mà API key hiện tại dùng được (``GET /v1/models``).

    Dùng để UI đổ danh sách model cho người dùng chọn thay vì bắt gõ tay tên model.
    Không có key vẫn thử được: một số endpoint local (Ollama) không cần key.
    """
    settings = config(cfg)
    if not settings["url"]:
        return {"ok": False, "error": "Base URL không hợp lệ."}
    if settings["mode"] == "api" and not settings["key"]:
        return {"ok": False, "error": "Nhập API key trước khi tải danh sách model."}
    url = models_url(settings["url"])
    if not url:
        return {"ok": False, "error": "Base URL không hợp lệ."}
    headers = {"Accept": "application/json"}
    if settings["key"]:
        headers["Authorization"] = "Bearer " + settings["key"]
    request = Request(url, method="GET", headers=headers)
    try:
        with urlopen(request, timeout=max(2, min(20, int(timeout)))) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        messages = {
            401: "API key không hợp lệ hoặc đã hết hạn (HTTP 401).",
            403: "API key không có quyền liệt kê model (HTTP 403).",
            404: "Provider không có endpoint /models (HTTP 404).",
            429: "Đang giới hạn tốc độ/hết quota (HTTP 429).",
        }
        return {"ok": False, "code": exc.code, "error": messages.get(
            exc.code, "Không tải được danh sách model (HTTP %d)." % exc.code)}
    except URLError:
        return {"ok": False, "error": "Không kết nối được Base URL (mạng/tường lửa)."}
    except (ValueError, OSError, TypeError):
        return {"ok": False, "error": "Danh sách model trả về không phải JSON hợp lệ."}

    rows = []
    if isinstance(data, dict):
        items = data.get("data")
        if not isinstance(items, list):
            items = data.get("models") if isinstance(data.get("models"), list) else []
        for item in items:
            if isinstance(item, str):
                rows.append({"id": _text(item, 120), "name": ""})
                continue
            if not isinstance(item, dict):
                continue
            ident = _text(item.get("id") or item.get("name") or item.get("model"), 120)
            if not ident:
                continue
            rows.append({"id": ident, "name": _text(item.get("name") or "", 120),
                         "owned_by": _text(item.get("owned_by") or "", 60)})
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                rows.append({"id": _text(item, 120), "name": ""})
            elif isinstance(item, dict):
                ident = _text(item.get("id") or item.get("name"), 120)
                if ident:
                    rows.append({"id": ident, "name": _text(item.get("name") or "", 120)})
    rows = [row for row in rows if row["id"]]
    rows.sort(key=lambda row: row["id"].lower())
    if not rows:
        return {"ok": False, "error": "API không trả model nào cho key này."}
    return {"ok": True, "models": rows, "count": len(rows), "url": url}


def analyze(prediction, recent_rounds=None, table="", timeout=TIMEOUT, cfg=None):
    """Gọi API AI; trả ``{"ok": True, "text": ...}`` hoặc lỗi có thể hiển thị."""
    settings = config(cfg)
    if not usable(settings):
        return {"ok": False, "error": _config_error(settings)}
    if not isinstance(prediction, dict) or not prediction.get("ok"):
        return {"ok": False, "error": "Chưa có kết quả tính toán để phân tích."}
    body = {
        "model": settings["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(prediction, recent_rounds, table)},
        ],
        "temperature": 0.2,
        "max_tokens": 400,
    }
    data, error, used = _request_with_fallback(body, settings, timeout or settings["timeout"])
    if error:
        return {"ok": False, "error": error}
    text = _content(data)
    if not text:
        return {"ok": False, "error": "AI không trả nội dung."}
    model_used = settings["fallback_model"] if used == "fallback" else settings["model"]
    return {"ok": True, "text": text, "model": model_used, "via": used}


def _request_with_fallback(body, settings, timeout):
    """Gọi provider chính; chỉ khi lỗi mới thử đường lùi (local).

    Trả ``(data, error, used)`` với ``used`` là ``"primary"`` | ``"fallback"``.
    Chỉ lỗi mạng/quota/mất key mới chuyển đường lùi — lỗi 400 do payload sai thì thử
    lại cũng vô ích, nên báo luôn.
    """
    # Chính chưa đủ cấu hình (chưa nhập key) thì đi thẳng đường lùi, không gửi request
    # chắc chắn hỏng tới provider.
    if not _primary_usable(settings):
        fallback = _fallback_settings(settings)
        if fallback is None:
            return None, _config_error(settings), "primary"
        retry = dict(body)
        retry["model"] = fallback["model"]
        data, fallback_error = _request(retry, fallback, timeout)
        if not fallback_error:
            return data, None, "fallback"
        return None, "%s · đường lùi cũng lỗi: %s" % (
            _config_error(settings), fallback_error), "primary"
    data, error = _request(body, settings, timeout)
    if not error:
        return data, None, "primary"
    if "400" in error:
        # Lỗi payload (vd. model local không hỗ trợ ``response_format``): thử lại cùng
        # provider sau khi bỏ khoá đó mới là cách sửa đúng, không phải đổi provider.
        return None, error, "primary"
    fallback = _fallback_settings(settings)
    if fallback is None:
        return None, error, "primary"
    retry = dict(body)
    retry["model"] = fallback["model"]
    data, fallback_error = _request(retry, fallback, timeout)
    if not fallback_error:
        return data, None, "fallback"
    # Ưu tiên thông báo của provider chính, kèm dấu hiệu đường lùi cũng hỏng.
    return None, "%s · đường lùi cũng lỗi: %s" % (error, fallback_error), "primary"


def _fallback_settings(settings):
    """Cấu hình đường lùi đủ dùng, hoặc ``None`` nếu không dùng được."""
    mode = str(settings.get("fallback_mode") or "off").lower()
    if mode not in ("local", "api"):
        return None
    url = str(settings.get("fallback_url") or "")
    if not url:
        return None
    if not _endpoint_alive(url):
        # Cổng không có gì lắng nghe (vd. chưa bật Ollama): coi như không có đường lùi.
        # Nếu không kiểm tra việc này, mỗi lần provider chính lỗi app phải chờ thêm
        # trọn một timeout cho endpoint chết — đúng lúc người dùng cần phản hồi nhanh.
        return None
    key = str(settings.get("fallback_key") or "")
    if mode == "api" and not key:
        return None
    return {"url": url, "model": str(settings.get("fallback_model") or ""),
            "key": key, "mode": mode, "timeout": settings.get("timeout")}


# Kết quả dò endpoint: {url: (mở được?, lúc kiểm tra)}. Cache ngắn để không mở TCP
# ở mỗi ván, nhưng vẫn phát hiện người dùng vừa bật/tắt Ollama.
_ALIVE_CACHE = {}
_ALIVE_TTL = 30.0


def _endpoint_alive(url, ttl=_ALIVE_TTL):
    """Dò nhanh cổng TCP của endpoint (không gửi HTTP), có cache.

    Chỉ dùng cho endpoint local: gọi ra Internet để "dò" là không cần thiết và có thể
    bị coi là hành vi lạ.
    """
    if not url:
        return False
    if not is_local_url(url):
        return True
    now = time.time()
    cached = _ALIVE_CACHE.get(url)
    if cached is not None and (now - cached[1]) < ttl:
        return cached[0]
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    alive = False
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=0.4)
        alive = True
    except OSError:
        alive = False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    _ALIVE_CACHE[url] = (alive, now)
    return alive


def analyze_structured(prediction, recent_rounds=None, table="", state="",
                       time_remains=None, history="", timeout=None, cfg=None):
    """Nhận định dạng JSON cho UI. Không bao giờ trả số liệu do AI tự bịa.

    Model local cũ có thể không hỗ trợ ``response_format``: nếu API trả 400 thì tự
    retry không dùng khoá này, rồi fallback sang text nếu JSON vẫn hỏng.
    """
    settings = config(cfg)
    if not usable(settings):
        return {"ok": False, "error": _config_error(settings)}
    if not isinstance(prediction, dict) or not prediction.get("ok"):
        return {"ok": False, "error": "Chưa có kết quả tính toán để phân tích."}
    playable = bool(prediction.get("playable"))
    focus_fallback = prediction.get("best_side") if playable else prediction.get("least_bad_side")
    body = {
        "model": settings["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_JSON},
            {"role": "user", "content": build_prompt(
                prediction, recent_rounds, table, state, time_remains, history,
                json_mode=True)},
        ],
        "temperature": 0.1,
        "max_tokens": 320,
        "response_format": {"type": "json_object"},
    }
    data, error, used = _request_with_fallback(body, settings,
                                               timeout or settings["timeout"])
    if error and "400" in error:
        body.pop("response_format", None)
        data, error, used = _request_with_fallback(body, settings,
                                                    timeout or settings["timeout"])
    if error:
        return {"ok": False, "error": error}
    content = _content(data)
    if not content:
        # Model reasoning (deepseek-v4.1-flash…) có thể trả ``content`` rỗng khi ngân
        # sách token bị phần suy luận dùng hết. Thử đọc phần suy luận để vẫn có nhận
        # định thay vì báo lỗi; đánh dấu nguồn là ``reasoning`` để UI biết.
        choice = ((data.get("choices") or [{}])[0] or {})
        message = choice.get("message") or {}
        content = _text(message.get("reasoning_content") or message.get("reasoning"), 450)
    if not content:
        return {"ok": False, "error": "AI không trả nội dung."}
    model_used = settings["fallback_model"] if used == "fallback" else settings["model"]

    obj = None
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            obj = parsed
    except (ValueError, TypeError):
        obj = None

    if obj is None:
        return {
            "ok": True, "verdict": "Cân nhắc" if playable else "BỎ TAY",
            "focus_side": focus_fallback if focus_fallback in ("P", "B", "T") else None,
            "confidence": "thấp", "risk": "", "explanation": _text(content, 450),
            "playable": playable, "model": model_used, "source": "text",
            "via": used,
        }

    verdict = _text(obj.get("verdict"), 24)
    if verdict not in VERDICTS:
        verdict = "Cân nhắc" if playable else "BỎ TAY"
    focus = obj.get("focus_side")
    if focus not in ("P", "B", "T"):
        focus = focus_fallback if focus_fallback in ("P", "B", "T") else None
    confidence = _text(obj.get("confidence"), 16)
    if confidence not in CONFIDENCES:
        confidence = "thấp"
    # Khoá an toàn: AI không được phép đổi quyết định đã tính bằng EV.
    if not playable:
        verdict, confidence = "BỎ TAY", "thấp"
        focus = focus_fallback if focus_fallback in ("P", "B", "T") else None
    return {
        "ok": True,
        "verdict": verdict,
        "focus_side": focus,
        "confidence": confidence,
        "risk": _text(obj.get("risk"), 220),
        "explanation": _text(obj.get("explanation"), 450),
        "playable": playable,
        "model": model_used,
        "source": "json",
        "via": used,
    }
