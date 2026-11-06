"""Anime-goods reference lookup: games, CDs, anime BDs, light novels.

Free, key-less sources:

* VNDB          visual novels / galge (release date, developer, platforms)
* MusicBrainz   CDs and albums (release date, label, artist)
* AniList       anime / manga / light novels (air date, studio, format)
* Wikipedia ja  general fallback (extract + thumbnail)

Each lookup is a single read-only request, run in parallel, and failures are
reported per source instead of breaking the page.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from typing import Any

UA = "RitaoCompare/1.0 (personal price comparison; contact: local user)"

TYPE_LABELS = {
    "game": "游戏 / 视觉小说",
    "cd": "CD / 音乐",
    "bd": "动画 BD / DVD",
    "book": "书籍 / 轻小说",
    "figure": "手办 / 模型",
    "unknown": "自动识别",
}

_TYPE_WORDS = [
    ("bd", ["blu-ray", "ブルーレイ", "bd-box", "bd box", "bd", "dvd", "円盤", "蓝光", "藍光"]),
    ("cd", ["cd", "アルバム", "シングル", "サウンドトラック", "サントラ", "ost", "ドラマcd",
            "主題歌", "キャラクターソング", "キャラソン", "音楽", "专辑", "專輯", "单曲"]),
    ("book", ["文庫", "単行本", "コミック", "漫画", "漫畫", "ライトノベル", "小説", "小说",
              "ノベル", "画集", "畫集", "設定資料集", "設定集", "ムック", "攻略本", "同人誌",
              "同人志", "轻小说", "輕小說"]),
    ("game", ["ゲーム", "游戏", "遊戲", "ソフト", "ps2", "ps3", "ps4", "ps5", "psp", "psv",
              "ps vita", "switch", "ニンテンドー", "windows", "pcゲーム", "美少女ゲーム",
              "アドベンチャー", "visual novel", "eroge", "エロゲ"]),
    ("figure", ["フィギュア", "figma", "ねんどろいど", "粘土人", "黏土人", "プラモデル",
                "ガンプラ", "手办", "模型", "景品", "プライズ"]),
]


def detect_type(text: str, attrs: dict[str, Any] | None = None) -> str:
    low = (text or "").lower()
    a = attrs or {}
    for key in ("category", "series", "character"):
        low += " " + str(a.get(key) or "").lower()
    for typ, words in _TYPE_WORDS:
        if any(w in low for w in words):
            return typ
    return "unknown"


# ---------------------------------------------------------------- transport
def _openers(proxy: str, proxy_first: bool = False):
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if not (proxy or "").strip():
        return [direct]
    proxied = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy.strip(), "https": proxy.strip()}))
    return [proxied, direct] if proxy_first else [direct, proxied]


def _request(url: str, proxy: str = "", data: dict | None = None,
             timeout: int = 6, proxy_first: bool = False) -> bytes:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/html;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    last: Exception | None = None
    for opener in _openers(proxy, proxy_first):
        try:
            req = urllib.request.Request(url, data=body, headers=headers,
                                         method="POST" if body else "GET")
            with opener.open(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last  # type: ignore[misc]


def _json(url: str, proxy: str = "", data: dict | None = None,
          proxy_first: bool = False) -> Any:
    return json.loads(_request(url, proxy, data, proxy_first=proxy_first)
                      .decode("utf-8", "replace"))


_TYPE_STRIP = [
    "初回限定版", "初回生産限定版", "完全生産限定版", "限定版", "初回版", "限定盤",
    "特装版", "豪華版", "数量限定", "通常版", "blu-ray box", "blu-ray", "bd-box",
    "ブルーレイ", "dvd", "bd", "cd", "アルバム", "シングル", "サウンドトラック",
    "サントラ", "ost", "ゲーム", "ソフト", "文庫", "単行本", "コミック", "漫画",
    "ライトノベル", "小説", "ノベル", "画集", "設定資料集", "設定集", "攻略本",
    "动画", "アニメ", "映画", "蓝光", "藍光", "专辑", "專輯",
]
_GENERIC = {"the", "and", "for", "with", "版", "盘", "盤", "初回", "限定"}


def _clean_query(text: str) -> str:
    q = text or ""
    for w in _TYPE_STRIP:
        q = re.sub(re.escape(w), " ", q, flags=re.I)
    q = re.sub(r"[【\[（(].*?[】\]）)]", " ", q)
    q = re.sub(r"\s+", " ", q).strip(" -_/|:：")
    return q


def _token_hit(title: str, query: str) -> bool:
    """Reject MusicBrainz/Wikipedia fuzzy hits that share no real token."""
    toks = [t for t in re.split(r"[^0-9a-zA-Z\u3040-\u30ff\u4e00-\u9fff]+", query.lower())
            if len(t) >= 3 and t not in _GENERIC]
    if not toks:
        return True
    low = (title or "").lower()
    return any(t in low for t in toks)


# ---------------------------------------------------------------- sources
def _vndb(query: str, proxy: str = "") -> list[dict[str, Any]]:
    payload = {
        "filters": ["search", "=", query],
        "fields": "id,title,alttitle,released,platforms,image.url,description",
        "results": 4,
    }
    d = _json("https://api.vndb.org/kana/vn", proxy, payload)
    out = []
    for r in d.get("results") or []:
        if not _token_hit(r.get("title") or "", query):
            continue
        desc = re.sub(r"\[.*?\]", "", r.get("description") or "")
        desc = re.sub(r"\s+", " ", html.unescape(desc)).strip()
        out.append({
            "source": "VNDB",
            "title": r.get("title") or "",
            "subtitle": r.get("alttitle") or "",
            "date": r.get("released") or "",
            "meta": " / ".join((r.get("platforms") or [])[:5]),
            "image": (r.get("image") or {}).get("url") or "",
            "url": f"https://vndb.org/{r.get('id')}",
            "desc": desc[:220],
            "kind": "game",
        })
    return out


def _musicbrainz(query: str, proxy: str = "") -> list[dict[str, Any]]:
    url = ("https://musicbrainz.org/ws/2/release-group/?query="
           + urllib.parse.quote(f'releasegroup:"{query}"') + "&fmt=json&limit=8")
    d = None
    last: Exception | None = None
    for attempt in range(2):
        try:
            d = _json(url, proxy, proxy_first=True)
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt == 0:
                time.sleep(1.2)
    if d is None:
        raise last  # type: ignore[misc]
    out = []
    for r in d.get("release-groups") or []:
        if not _token_hit(r.get("title") or "", query):
            continue
        rgid = r.get("id") or ""
        artists = ", ".join(a.get("name", "") for a in (r.get("artist-credit") or []))
        out.append({
            "source": "MusicBrainz",
            "title": r.get("title") or "",
            "subtitle": artists,
            "date": r.get("first-release-date") or "",
            "meta": " / ".join(x for x in (r.get("primary-type"), r.get("secondary-types") and
                                           ", ".join(r["secondary-types"])) if x),
            "image": f"https://coverartarchive.org/release-group/{rgid}/front-250" if rgid else "",
            "url": f"https://musicbrainz.org/release-group/{rgid}" if rgid else "",
            "desc": "",
            "kind": "cd",
        })
    return out


_ANILIST = """
query ($q: String, $type: MediaType) {
  Media(search: $q, type: $type) {
    title { romaji native english }
    startDate { year month day }
    episodes chapters volumes
    format
    coverImage { large }
    siteUrl
    studios(isMain: true) { nodes { name } }
  }
}
"""


def _anilist(query: str, proxy: str = "", media_type: str = "ANIME") -> list[dict[str, Any]]:
    try:
        d = _json("https://graphql.anilist.co", proxy,
                  {"query": _ANILIST, "variables": {"q": query, "type": media_type}})
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # "no such entry" is a normal answer
            return []
        raise
    m = (d.get("data") or {}).get("Media") or {}
    if not m:
        return []
    sd = m.get("startDate") or {}
    date = "-".join(str(sd.get(k)) for k in ("year", "month", "day") if sd.get(k))
    titles = m.get("title") or {}
    studios = ", ".join(n.get("name", "") for n in
                        ((m.get("studios") or {}).get("nodes") or []))
    extra = ""
    if media_type == "ANIME":
        extra = f"{m.get('episodes') or '?'} 话"
    else:
        extra = f"{m.get('volumes') or '?'} 卷 / {m.get('chapters') or '?'} 话"
    return [{
        "source": "AniList",
        "title": titles.get("native") or titles.get("romaji") or "",
        "subtitle": titles.get("romaji") if titles.get("native") else titles.get("english", ""),
        "date": date,
        "meta": " / ".join(x for x in (m.get("format"), extra, studios) if x),
        "image": (m.get("coverImage") or {}).get("large") or "",
        "url": m.get("siteUrl") or "",
        "desc": "",
        "kind": "bd" if media_type == "ANIME" else "book",
    }]


def _wikipedia(query: str, proxy: str = "") -> list[dict[str, Any]]:
    wiki_q = _clean_query(query)
    search = _json("https://ja.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": wiki_q,
        "format": "json", "srlimit": 3,
    }), proxy, proxy_first=True)
    hits = [h.get("title") for h in ((search.get("query") or {}).get("search") or []) if h.get("title")]
    if not hits:
        return []
    pages = _json("https://ja.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "prop": "extracts|pageimages", "exintro": 1, "explaintext": 1,
        "pithumbsize": 300, "titles": "|".join(hits[:2]), "format": "json",
        "redirects": 1,
    }), proxy, proxy_first=True)
    out = []
    for page in ((pages.get("query") or {}).get("pages") or {}).values():
        title = page.get("title") or ""
        if not title or not _token_hit(title, wiki_q):
            continue
        out.append({
            "source": "Wikipedia",
            "title": title,
            "subtitle": "日文维基",
            "date": "",
            "meta": "",
            "image": (page.get("thumbnail") or {}).get("source") or "",
            "url": "https://ja.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            "desc": (page.get("extract") or "")[:260],
            "kind": "book",
        })
    return out


# ---------------------------------------------------------------- entry
def build_links(cn: str, jp: str) -> list[dict[str, str]]:
    q = (jp or cn or "").strip()
    if not q:
        return []
    qq = urllib.parse.quote(q)
    cnq = urllib.parse.quote(cn or q)
    return [
        {"id": "vndb", "name": "VNDB", "url": f"https://vndb.org/v/all?q={qq}"},
        {"id": "vgmdb", "name": "VGMdb", "url": f"https://vgmdb.net/search?q={qq}"},
        {"id": "musicbrainz", "name": "MusicBrainz",
         "url": f"https://musicbrainz.org/search?query={qq}&type=release_group"},
        {"id": "anilist", "name": "AniList",
         "url": f"https://anilist.co/search/anime?search={qq}"},
        {"id": "wiki", "name": "日文维基",
         "url": f"https://ja.wikipedia.org/wiki/Special:Search?search={qq}"},
        {"id": "surugaya", "name": "骏河屋",
         "url": f"https://www.suruga-ya.jp/search?search_word={qq}"},
        {"id": "amazon", "name": "日亚", "url": f"https://www.amazon.co.jp/s?k={qq}"},
        {"id": "bookoff", "name": "BOOKOFF",
         "url": f"https://www.bookoffonline.co.jp/search/?q={qq}"},
        {"id": "mandarake", "name": "Mandarake",
         "url": f"https://order.mandarake.co.jp/order/listPage/list?keyword={qq}"},
        {"id": "taobao", "name": "淘宝", "url": f"https://s.taobao.com/search?q={cnq}"},
    ]


def search(cn: str, jp: str, attrs: dict[str, Any] | None = None,
           proxy: str = "") -> dict[str, Any]:
    cn, jp = (cn or "").strip(), (jp or "").strip()
    a = attrs or {}
    query = (a.get("series") or "").strip() or jp or cn
    # VNDB / Wikipedia index the work, not the SKU: drop edition and media
    # words so "ToHeart2 Blu-ray" becomes "ToHeart2".
    query = _clean_query(query) or _clean_query(jp) or _clean_query(cn)
    if not query:
        return {"ok": False, "type": "unknown", "items": [], "links": [], "errors": []}

    typ = detect_type(f"{cn} {jp}", a)
    jobs: list[tuple[str, Any]] = []

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        if typ in ("game", "unknown"):
            jobs.append(("VNDB", pool.submit(_vndb, query, proxy)))
        if typ == "cd":
            jobs.append(("MusicBrainz", pool.submit(_musicbrainz, query, proxy)))
        if typ in ("bd", "unknown"):
            jobs.append(("AniList", pool.submit(_anilist, query, proxy, "ANIME")))
        if typ == "book":
            jobs.append(("AniList", pool.submit(_anilist, query, proxy, "MANGA")))
        jobs.append(("Wikipedia", pool.submit(_wikipedia, query, proxy)))

        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for name, fut in jobs:
            try:
                items.extend(fut.result())
            except Exception as exc:  # noqa: BLE001
                errors.append({"source": name, "reason": f"{type(exc).__name__}: {exc}"[:140]})

    # The type may only be knowable after a hit (e.g. a VN title has no "game"
    # keyword). Promote the strongest source's kind.
    if typ == "unknown" and items:
        typ = items[0].get("kind") or "unknown"

    return {
        "ok": bool(items),
        "type": typ,
        "type_label": TYPE_LABELS.get(typ, "自动识别"),
        "query": query,
        "items": items[:16],
        "links": build_links(cn, jp),
        "errors": errors,
        "reason": "" if items else "没有查到资料，可直接用下方链接站内搜索",
    }
