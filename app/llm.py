"""Optional OpenAI-compatible LLM / vision client.

Nothing here is required: without credentials the rule engine in
query_engine.py handles rewriting, and vision degrades to native
image-search entry points. Credentials are only ever sent to the base URL
the user configured themselves; images never leave the machine otherwise.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

IMAGE_DIR = Path(__file__).resolve().parent / "data" / "images"


def configured(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("llm_api_key", "").strip() and cfg.get("llm_base_url", "").strip())


def enabled(cfg: dict[str, Any]) -> bool:
    """Master switch from Settings. Off = not a single model request, ever.

    Credentials may still be stored; they are simply not used, so the tool
    runs entirely on the local rule engine (zero tokens, zero cost).
    """
    return cfg.get("use_llm", True) is not False and configured(cfg)


def vision_available(cfg: dict[str, Any]) -> bool:
    return enabled(cfg) and bool(cfg.get("llm_vision_model", "").strip() or cfg.get("llm_model", "").strip())


def _endpoint(cfg: dict[str, Any]) -> str:
    base = cfg.get("llm_base_url", "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1") and "/v1" not in base:
        base += "/v1"
    return base + "/chat/completions"


def _post(payload: dict[str, Any], cfg: dict[str, Any], timeout: int = 90) -> str:
    url = _endpoint(cfg)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg.get("llm_api_key", "").strip(),
        "User-Agent": "RitaoCompare/3.0",
    }
    proxy = cfg.get("proxy", "").strip()
    opener = (urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})) if proxy
        else urllib.request.build_opener())
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with opener.open(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return data["choices"][0]["message"]["content"]


def _json_from(text: str) -> dict[str, Any]:
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


def rewrite(cn: str, jp: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """CN product title -> 2-3 Japanese marketplace queries + attributes."""
    if not configured(cfg):
        return {"queries": [], "mode": "rules"}
    prompt = (
        "你在帮个人买家把中文商品名改写成日本二手电商（メルカリ / Yahoo!オークション / 駿河屋）"
        "真正会用的搜索词。不要直译中文语序，要抽取品牌、系列、角色、型号、品类，再用日本买家"
        "实际高频的词重写。\n"
        "只输出 JSON：{\"queries\":[\"候选1\",\"候选2\",\"候选3\"],"
        "\"brand\":\"\",\"series\":\"\",\"character\":\"\",\"model\":\"\",\"category\":\"\"}\n"
        "候选1 保留最完整信息，候选2 用日文惯用词，候选3 只留品牌+型号+品类。\n"
        f"中文标题：{cn}\n用户已填的日文关键词：{jp or '（无）'}"
    )
    try:
        out = _json_from(_post({
            "model": cfg.get("llm_model", "").strip() or "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }, cfg, timeout=45))
        qs = [str(q).strip() for q in (out.get("queries") or []) if str(q).strip()][:3]
        return {"queries": qs, "attrs": out, "mode": "llm"}
    except Exception as exc:  # noqa: BLE001
        return {"queries": [], "mode": "rules", "error": f"{type(exc).__name__}: {exc}"[:160]}


VISION_PROMPT = (
    "看图识别这件商品，目的是在日本二手电商和国内二手平台搜索同款。"
    "不要泛泛描述图片，要输出可直接粘贴进搜索框的结构化关键词。\n"
    "只输出 JSON：{\"brand\":\"品牌\",\"series\":\"系列/作品\",\"character\":\"角色\","
    "\"model\":\"型号或比例\",\"category\":\"品类\",\"cn_name\":\"中文商品名\","
    "\"jp_name\":\"日文商品名\",\"condition\":\"成色线索\",\"jp_queries\":[\"日文检索词1\",\"日文检索词2\"],"
    "\"confidence\":0.0,\"notes\":\"识别依据\"}\n"
    "jp_queries 必须用日本电商惯用词（例：黏土人→ねんどろいど、手办→フィギュア、"
    "吧唧→缶バッジ、景品→プライズ）。不确定时 confidence 给低值，不要编造。"
)


def vision(image_b64: str, mime: str, cfg: dict[str, Any]) -> dict[str, Any]:
    if not vision_available(cfg):
        return {"ok": False, "reason": "未配置支持视觉的模型；可用下方各平台原生以图搜图入口"}
    model = cfg.get("llm_vision_model", "").strip() or cfg.get("llm_model", "").strip()
    try:
        content = _json_from(_post({
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ]}],
            "temperature": 0.1,
        }, cfg, timeout=90))
        return {"ok": True, "model": model, "data": content}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {str(exc).splitlines()[0][:140]}"}


def save_image(data_url: str) -> tuple[str, str, str]:
    """Save an uploaded data URL locally. Returns (relative_path, mime, raw_b64)."""
    m = re.match(r"data:(image/[A-Za-z0-9.+-]+);base64,(.+)", data_url or "", re.S)
    if not m:
        raise ValueError("只支持图片 data URL（jpg / png / webp）")
    mime, raw = m.group(1), m.group(2)
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
           "image/gif": ".gif"}.get(mime, ".jpg")
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    import time
    name = f"capture_{int(time.time() * 1000)}{ext}"
    (IMAGE_DIR / name).write_bytes(base64.b64decode(raw))
    return f"data/images/{name}", mime, raw
