"""Auto-dò bìa manga bằng vision LLM (opt-in `--auto-cover`).

Bản scanlation hay chèn banner nhóm dịch + bìa-sau TRƯỚC bìa thật → cover_index=1
trỏ nhầm. Module này gửi vài trang ĐẦU (đã lọc min_px, downscale) cho vision model
hỏi: "trang nào là BÌA TRƯỚC thật?" → trả index 1-based TRÊN list đã lọc (khớp
cover_index lúc build). Model không thấy bìa thật (vd tập bắt đầu giữa truyện) →
trả null → caller fallback cover_index=1.

KHÔNG phải pipeline mặc định: chỉ chạy khi user bật `--auto-cover` (cần
OPENROUTER_API_KEY). Mặc định manga vẫn $0 / không cần key.

Tái dùng hạ tầng LLM có sẵn (KHÔNG dựng lại): context_prepass._encode_sample
(downscale+b64), _strip_json_fence; ocr._is_transient/estimate_cost/OPENROUTER_URL.
Mirror hình dạng _post_context_once/_extract_with_retry của context_prepass.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import error as urlerr
from urllib import request as urlreq

from . import context_prepass, ocr

# Số trang đầu gửi cho model — đủ vượt qua banner+bìa-sau (Pluto: 2 trang) tới bìa
# thật mà vẫn rẻ. Tăng nếu nguồn chèn nhiều trang rác hơn trước bìa.
MAX_DETECT_PAGES = 5
DETECT_MAX_TOKENS = 500

COVER_PROMPT = """Bạn xem vài trang ĐẦU của một tập truyện tranh (manga/comic) đã quét.
Mỗi ảnh có nhãn TÊN FILE ngay TRƯỚC nó (dòng "[Trang N: page_xxx]").

Nhiệm vụ: tìm BÌA TRƯỚC THẬT của tập — trang có ẢNH MINH HOẠ BÌA + tên truyện/số tập
in trang trí, là mặt ngoài cuốn sách. BỎ QUA: banner/thông báo của nhóm dịch
(scanlation), trang quảng cáo, BÌA SAU (mã vạch/ISBN/giá), trang trắng.

Nếu KHÔNG trang nào trong số đã cho là bìa trước thật (vd tập bắt đầu giữa truyện,
chỉ có trang nội dung) → trả cover_index = null. KHÔNG đoán bừa.

Trả về DUY NHẤT một JSON object (không giải thích ngoài, không ```json wrapper):
{"cover_index": <số thứ tự N của ảnh bìa theo nhãn "[Trang N: ...]", hoặc null>,
 "reason": "vì sao chọn trang đó / vì sao không có bìa"}
Số N đếm theo THỨ TỰ ẢNH ĐƯỢC GỬI (1 = ảnh đầu tiên), KHÔNG theo số in trên trang."""


def _post_cover_once(api_key: str, model: str, samples, max_tokens: int) -> tuple[str, dict]:
    """1 POST đa-ảnh (text prompt + N×[nhãn + image_url]). `samples`: list (b64,mime,name).

    Mirror context_prepass._post_context_once. Raises RuntimeError trên HTTP/empty
    (marker để ocr._is_transient bắt → retry)."""
    content: list[dict] = [{"type": "text", "text": COVER_PROMPT}]
    for i, (b64, mime, name) in enumerate(samples, 1):
        content.append({"type": "text", "text": f"[Trang {i}: {name}]"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    req = urlreq.Request(
        ocr.OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/phucnt/scan-to-ebook",
            "X-Title": "scan-to-ebook",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urlreq.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except urlerr.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:  # noqa: BLE001
            err_body = "<unreadable>"
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {err_body}") from exc
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"malformed response (JSON parse): {exc} | body[:200]={raw[:200]!r}") from exc
    latency = time.time() - t0
    if "choices" not in body or not body["choices"]:
        err = body.get("error", body)
        raise RuntimeError(f"no choices in response: {json.dumps(err)[:300]}")
    text = body["choices"][0].get("message", {}).get("content")
    if text is None or not text.strip():
        raise RuntimeError("empty content from cover detect")
    return text, {"latency_s": round(latency, 2), "usage": body.get("usage", {})}


def _parse_cover(content: str, n_samples: int) -> int | None:
    """Parse JSON → cover_index hợp lệ (1..n_samples) hoặc None.

    cover_index ngoài [1,n] hoặc phi số → coi như None (không có bìa → fallback)."""
    ctx = json.loads(context_prepass._strip_json_fence(content))
    if not isinstance(ctx, dict):
        raise RuntimeError("cover detect JSON không phải object")
    idx = ctx.get("cover_index")
    if idx is None:
        return None
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return None
    return idx if 1 <= idx <= n_samples else None


def detect_cover_index(
    api_key: str, model: str, img_dir: Path, min_px: int, retries: int = 2
) -> tuple[int, dict]:
    """Dò bìa → (cover_index 1-based trên list ĐÃ lọc min_px, info).

    Gửi MAX_DETECT_PAGES trang đầu của filtered_pages (cùng thứ tự build dùng) cho
    vision model. Model trả null hoặc index ngoài khoảng → fallback 1. info gồm
    chosen/reason/cost_usd/from_model để caller log. Retry transient (mirror prepass).
    """
    from . import epub3_fixed_layout

    pages = epub3_fixed_layout.filtered_pages(img_dir, min_px)
    if not pages:
        raise RuntimeError("auto-cover: không có trang hợp lệ để dò bìa")
    sample_paths = [p for p, _ in pages[:MAX_DETECT_PAGES]]
    samples = [(*context_prepass._encode_sample(p), p.name) for p in sample_paths]

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            content, meta = _post_cover_once(api_key, model, samples, DETECT_MAX_TOKENS)
            idx = _parse_cover(content, len(samples))
            usage = meta.get("usage", {})
            cost = ocr.estimate_cost(
                model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
            )
            chosen = idx if idx is not None else 1
            return chosen, {
                "chosen": chosen,
                "from_model": idx is not None,
                "reason": _safe_reason(content),
                "cost_usd": round(cost, 4),
            }
        except RuntimeError as exc:
            last_exc = exc
            if not ocr._is_transient(str(exc)) or attempt == retries:
                raise
            time.sleep(2 ** attempt + attempt * 0.5)
    assert last_exc is not None
    raise last_exc


def _safe_reason(content: str) -> str:
    """Lấy field reason để log; parse fail → cắt content thô (chỉ để người đọc)."""
    try:
        ctx = json.loads(context_prepass._strip_json_fence(content))
        if isinstance(ctx, dict) and ctx.get("reason"):
            return str(ctx["reason"])[:200]
    except json.JSONDecodeError:
        pass
    return content.strip()[:200]
