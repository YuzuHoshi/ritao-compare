"""开箱 / 评测视频检索.

Three sources, no API key and no login:

* Bilibili   public web search API (works from mainland networks)
* YouTube    the public results page, parsed from ytInitialData
* niconico   public snapshot search API

The important part is *query building*. A full Chinese product title returns
generic 手办 videos; short queries such as "莱莎 手办" return the actual figma
reviews. Results are then filtered with the same identity matcher used for
prices, and gameplay / walkthrough videos are dropped.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from typing import Any

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_TAG = re.compile(r"<[^>]+>")

# Walkthrough / guide videos mention the same character but are useless when
# deciding whether to buy a figure.
VIDEO_EXCLUDE = (
    "攻略", "采集", "指南", "实况", "通关", "剧情", "材料", "教学", "速通",
    "解说", "实机", "试玩", "实況", "流程", "白金", "刷钱", "配装", "build",
)
# Signals that a video is actually about the physical product.
VIDEO_KEEP = (
    "开箱", "開封", "测评", "评测", "レビュー", "手办", "フィギュア", "figma",
    "粘土人", "ねんどろいど", "模型", "プラモデル", "可动", "可動", "紹介",
    "unboxing", "review", "figure",
)


def _openers(proxy: str, proxy_first: bool = False):
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if not (proxy or "").strip():
        return [direct]
    proxied = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy.strip(), "https": proxy.strip()}))
    return [proxied, direct] if proxy_first else [direct, proxied]


def _get_text(url: str, proxy: str = "", referer: str = "", timeout: int = 12,
              proxy_first: bool = False, cookie: str = "") -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
    }
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie
    last: Exception | None = None
    for opener in _openers(proxy, proxy_first):
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last  # type: ignore[misc]


def _get_json(url: str, proxy: str = "", referer: str = "", timeout: int = 10,
              proxy_first: bool = False) -> dict[str, Any]:
    return json.loads(_get_text(url, proxy, referer, timeout, proxy_first))


def _clean_title(raw: str) -> str:
    return html.unescape(_TAG.sub("", raw or "")).strip()


def _views(n: Any) -> int:
    if isinstance(n, (int, float)):
        return int(n)
    s = str(n or "").replace(",", "").strip()
    m = re.search(r"([\d.]+)\s*([万萬億亿])?", s)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2)
    if unit in ("万", "萬"):
        val *= 10000
    elif unit in ("亿", "億"):
        val *= 100000000
    return int(val)


def _mmss(sec: Any) -> str:
    try:
        s = int(sec)
    except (TypeError, ValueError):
        return ""
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------- sources
def _bilibili(keyword: str, proxy: str = "") -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "search_type": "video", "keyword": keyword,
        "page": 1, "page_size": 12, "order": "totalrank",
    })
    # Bilibili rejects oversea proxy exits (412); 412 is also its soft limit.
    last: Exception | None = None
    data: dict[str, Any] | None = None
    for attempt in range(2):
        try:
            data = _get_json("https://api.bilibili.com/x/web-interface/search/type?" + params,
                             proxy="", referer="https://search.bilibili.com/")
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt == 0:
                import time
                time.sleep(1.6)
    if data is None:
        raise last  # type: ignore[misc]
    if data.get("code") not in (0, None):
        raise RuntimeError(f"bilibili code {data.get('code')}: {data.get('message')}")
    out = []
    for r in ((data.get("data") or {}).get("result") or []):
        bvid = r.get("bvid") or ""
        if not bvid:
            continue
        pic = r.get("pic") or ""
        if pic.startswith("//"):
            pic = "https:" + pic
        out.append({
            "platform": "哔哩哔哩",
            "title": _clean_title(r.get("title")),
            "url": f"https://www.bilibili.com/video/{bvid}",
            "image": pic,
            "author": _clean_title(r.get("author")),
            "views": _views(r.get("play")),
            "duration": r.get("duration") or "",
            "date": _date(r.get("pubdate")),
            "source": "bilibili",
        })
    return out


def _date(ts: Any) -> str:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return ""


def _youtube(keyword: str, proxy: str = "") -> list[dict[str, Any]]:
    """Parse the public results page; no API key needed."""
    url = ("https://www.youtube.com/results?search_query="
           + urllib.parse.quote(keyword) + "&hl=ja&gl=JP")
    page = _get_text(url, proxy, referer="https://www.youtube.com/",
                     proxy_first=True, cookie="CONSENT=YES+1")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", page, re.S)
    if not m:
        raise RuntimeError("ytInitialData not found")
    data = json.loads(m.group(1))

    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            v = node.get("videoRenderer")
            if isinstance(v, dict):
                title = "".join(p.get("text", "") for p in
                                (v.get("title", {}).get("runs") or []))
                vid = v.get("videoId")
                if vid and title:
                    thumbs = (v.get("thumbnail", {}).get("thumbnails") or [{}])
                    out.append({
                        "platform": "YouTube",
                        "title": title,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "image": (thumbs[-1] or {}).get("url", ""),
                        "author": ((v.get("ownerText", {}).get("runs") or [{}])[0]).get("text", ""),
                        "views": _views((v.get("viewCountText", {}) or {}).get("simpleText", "")),
                        "duration": (v.get("lengthText", {}) or {}).get("simpleText", ""),
                        "date": (v.get("publishedTimeText", {}) or {}).get("simpleText", ""),
                        "source": "youtube",
                    })
            for val in node.values():
                walk(val)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(data)
    return out


def _niconico(keyword: str, proxy: str = "") -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "q": keyword,
        "targets": "title,description,tags",
        "fields": "contentId,title,thumbnailUrl,viewCounter,lengthSeconds,startTime",
        "_sort": "-viewCounter", "_limit": 10, "_context": "ritao-compare",
    })
    data = _get_json(
        "https://api.search.nicovideo.jp/api/v2/snapshot/video/contents/search?" + params,
        proxy=proxy, referer="https://www.nicovideo.jp/", proxy_first=True)
    out = []
    for r in data.get("data") or []:
        cid = r.get("contentId") or ""
        if not cid:
            continue
        out.append({
            "platform": "niconico",
            "title": r.get("title") or "",
            "url": f"https://www.nicovideo.jp/watch/{cid}",
            "image": r.get("thumbnailUrl") or "",
            "author": "",
            "views": _views(r.get("viewCounter")),
            "duration": _mmss(r.get("lengthSeconds")),
            "date": (r.get("startTime") or "")[:10],
            "source": "niconico",
        })
    return out


# ---------------------------------------------------------------- queries
def _query_plan(cn: str, jp: str, attrs: dict[str, Any] | None,
                cn_query: str = "") -> tuple[list[str], list[str], dict[str, Any]]:
    import query_engine as qe
    if not attrs:
        attrs = qe.build_candidates(cn, jp, []).get("attrs") or {}
    char = attrs.get("character") or ""
    series = attrs.get("series") or ""
    cat = attrs.get("category") or ""

    override = (cn_query or "").strip()
    cn_q = override or qe.cn_query(cn, attrs)
    cn_queries: list[str] = []
    if cn_q and len(cn_q) >= 2:
        cn_queries.append(cn_q)
    # A shorter "series + category" variant catches reviews titled without the
    # character name. Skip it when the user edited the query by hand: they get
    # exactly what they typed.
    if not override:
        series_alias = ""
        for alias in qe.dicts.aliases_for(series):
            if re.search(r"[\u4e00-\u9fff]", alias) and alias in (cn or ""):
                series_alias = alias
                break
        word = qe.dicts.CATEGORY_CN_WORD.get(cat, "")
        if not word:
            for cn_w, jp_w in qe.dicts.CATEGORY_MAP_V2:
                if qe._key(jp_w) == qe._key(cat):
                    word = cn_w
                    break
        if series_alias and word and series_alias not in cn_q:
            cn_queries.append(f"{series_alias} {word}")
    if not cn_queries and cn:
        cn_queries.append(" ".join(cn.split()[:2]))

    jp_name = char or series
    jp_queries: list[str] = []
    if jp_name:
        jp_queries.append(f"{jp_name} レビュー")
    return list(dict.fromkeys(cn_queries))[:2], jp_queries[:1], attrs


def _relevant(title: str, attrs: dict[str, Any]) -> tuple[bool, int]:
    import query_engine as qe
    score, _ = qe.judge(title, attrs)
    low = (title or "").lower()
    has_keep = any(k.lower() in low for k in VIDEO_KEEP)
    has_bad = any(k.lower() in low for k in VIDEO_EXCLUDE)
    if has_bad and not has_keep:
        return False, score
    # Either the identity matched, or it is clearly a figure video.
    return bool(score > 0 or has_keep), score


# ---------------------------------------------------------------- entry
def build_links(cn: str, jp: str) -> list[dict[str, str]]:
    q_cn = (cn or "").strip()
    q_jp = (jp or "").strip() or q_cn
    out = []
    if q_cn:
        out.append({"id": "bilibili", "name": "哔哩哔哩 · 搜索",
                    "url": "https://search.bilibili.com/all?keyword="
                           + urllib.parse.quote(q_cn)})
    if q_jp:
        out.append({"id": "youtube", "name": "YouTube · 搜索",
                    "url": "https://www.youtube.com/results?search_query="
                           + urllib.parse.quote(q_jp)})
        out.append({"id": "niconico", "name": "niconico · 搜索",
                    "url": "https://www.nicovideo.jp/search/"
                           + urllib.parse.quote(q_jp)})
    return out


def search(cn: str, jp: str, proxy: str = "",
           attrs: dict[str, Any] | None = None,
           cn_query: str = "") -> dict[str, Any]:
    """One read-only lookup per explicit click; never raises.

    cn_query is the manually edited Chinese query from the plan dialog; when
    present it wins over the rule-generated one.
    """
    cn, jp = (cn or "").strip(), (jp or "").strip()
    if not cn and not jp:
        return {"ok": False, "items": [], "links": [], "errors": [],
                "queries": {"cn": [], "jp": ""}, "reason": "缺少关键词"}

    cn_queries, jp_queries, attrs = _query_plan(cn, jp, attrs, cn_query)
    jp_query = jp_queries[0] if jp_queries else ""

    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    from concurrent.futures import ThreadPoolExecutor
    jobs: list[tuple[str, Any]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        if cn_queries:
            jobs.append(("哔哩哔哩", pool.submit(_bilibili, cn_queries[0], proxy)))
        if jp_query:
            jobs.append(("YouTube", pool.submit(_youtube, jp_query, proxy)))
            jobs.append(("niconico", pool.submit(_niconico, jp_query, proxy)))
        for name, fut in jobs:
            try:
                items.extend(fut.result())
            except Exception as exc:  # noqa: BLE001
                msg = f"{type(exc).__name__}: {exc}"
                # niconico regularly blocks datacenter / proxy exits; the site
                # link is still offered, so this is not worth an alarming note.
                if name == "niconico" and "403" in msg:
                    continue
                errors.append({"source": name, "reason": msg[:160]})

    best: dict[str, dict[str, Any]] = {}
    for it in items:
        key = (it.get("url") or "").split("&")[0]
        if key and key not in best:
            best[key] = it

    ranked: list[dict[str, Any]] = []
    for it in best.values():
        ok, score = _relevant(it.get("title", ""), attrs)
        it["match"] = score
        if ok:
            ranked.append(it)

    def sort_key(x: dict[str, Any]):
        low = (x.get("title") or "").lower()
        keep = sum(1 for k in VIDEO_KEEP if k.lower() in low)
        return (-(x.get("match") or 0), -keep, -(x.get("views") or 0))

    ranked.sort(key=sort_key)

    # If strict filtering leaves nothing, show the closest unfiltered results
    # rather than an empty page; the links are still below.
    if not ranked and best:
        ranked = sorted(best.values(), key=lambda x: -(x.get("views") or 0))[:12]

    return {
        "ok": bool(ranked),
        "items": ranked[:24],
        "links": build_links(cn, jp),
        "errors": errors,
        "queries": {"cn": cn_queries, "jp": jp_query},
        "reason": "" if ranked else "没有找到相关视频，可直接点下方站内搜索",
    }
