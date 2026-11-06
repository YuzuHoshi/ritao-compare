"""Data-source layer for the personal Ritao price-companion.

Tier A: deep links (zero network access from us, zero ban risk).
Tier B: official free APIs (Rakuten Web Service, new openapi platform).
Tier C: browser_probe.py -- the user's own logged-in browser, opt-in.
Extras: Yahoo Auctions' own public 落札相場 page (no account needed),
        Hpoi figure database, and figure-maker deep links.

All functions keep an MCP-shaped signature so the module can be wrapped into
an MCP server later without rewriting.
"""
from __future__ import annotations

import html
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import dicts  # noqa: E402
import query_engine as qe  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

PLATFORMS: list[dict[str, str]] = [
    dict(id="xianyu", name="闲鱼", region="国内", kind="二手在售", group="A",
         tpl="https://www.goofish.com/search?q={q}",
         hint="国内二手在售价，判断是不是国内买更划算"),
    dict(id="mercari", name="煤炉 Mercari", region="日本", kind="二手在售", group="A",
         tpl="https://jp.mercari.com/search?keyword={q}",
         hint="日本二手主力，日文关键词命中率更高"),
    dict(id="mercari_sold", name="煤炉 · 已售 SOLD", region="日本", kind="成交价", group="A",
         tpl="https://jp.mercari.com/search?keyword={q}&status=sold",
         hint="登录后筛选「売り切れ」看已成交价"),
    dict(id="surugaya", name="骏河屋", region="日本", kind="中古在售", group="A",
         tpl="https://www.suruga-ya.jp/search?search_word={q}",
         hint="中古手办 / 谷子 / 游戏，日文关键词效果更好"),
    dict(id="yahoo_auction", name="雅虎日拍", region="日本", kind="拍卖在售", group="A",
         tpl="https://auctions.yahoo.co.jp/search/search?p={q}&ei=utf-8",
         hint="在拍商品，注意含税与运费"),
    dict(id="yahoo_closed", name="雅虎 · 落札相場(官方)", region="日本", kind="成交价", group="AB",
         tpl="https://auctions.yahoo.co.jp/closedsearch/closedsearch/{q}/0/",
         hint="官方 120 天落札相場：最安 / 平均 / 最高 / 件数，免登录"),
    dict(id="aucfan", name="AucFan 成交数据库", region="日本", kind="成交价", group="A",
         tpl="https://aucfan.com/search1/q-{q}/",
         hint="聚合雅虎/煤炉等历史落札，游客可用；部分数据需免费账号"),
    dict(id="rakuten", name="乐天市场", region="日本", kind="新品", group="AB",
         tpl="https://search.rakuten.co.jp/search/mall/{q}/",
         hint="新品定价基准；配好官方凭证后直接显示价格"),
    dict(id="amazon_jp", name="日本亚马逊", region="日本", kind="新品", group="A",
         tpl="https://www.amazon.co.jp/s?k={q}", hint="新品定价基准"),
    dict(id="mandarake", name="Mandarake まんだらけ", region="日本", kind="中古在售", group="A",
         tpl="https://order.mandarake.co.jp/order/listPage/list?keyword={q}",
         hint="日本最大中古动漫店：手办 / 同人 / 玩具，库存与成色标注清楚"),
    dict(id="bookoff", name="BOOKOFF ONLINE", region="日本", kind="中古在售", group="A",
         tpl="https://www.bookoffonline.co.jp/search/?q={q}",
         hint="日本二手连锁：漫画 / CD / DVD / 游戏 / 画集，价格低"),
    dict(id="taobao", name="淘宝", region="国内", kind="新品/二手", group="A",
         tpl="https://s.taobao.com/search?q={q}",
         hint="国内全新与代理版价格；需要登录淘宝账号"),
    dict(id="kakaku", name="价格.com", region="日本", kind="新品比价", group="A",
         tpl="https://kakaku.com/search_results/{q}/", hint="3C / 家电 / 相机比价"),
    dict(id="ritao", name="日淘市集", region="代购", kind="当前商品", group="A",
         tpl="https://m.ritao.hk/search?site_id=2&keyword={q}", hint="回到日淘市集搜同款"),
]

# Figure-specific sources. These answer "when did it come out / what was the
# original list price / where is the maker's page".
FIGURE_PLATFORMS: list[dict[str, str]] = [
    dict(id="hpoi", name="Hpoi 手办维基", site="hpoi.net",
         tpl="https://www.hpoi.net/search?category=100&keyword={q}",
         home="https://www.hpoi.net/hobby/all?order=release&category=100", status="ok",
         hint="中文手办资料库：发售日 / 定价 / 厂商 / 原型师；结果已直接显示在上方"),
    dict(id="mfc", name="MyFigureCollection", site="myfigurecollection.net",
         tpl="https://myfigurecollection.net/search/?q={q}",
         home="https://myfigurecollection.net/", status="shield",
         hint="全球最大手办数据库，Release date / Price / Manufacturer"),
    dict(id="amiami", name="AmiAmi", site="amiami.com",
         tpl="https://www.amiami.com/eng/search/list/?s_keywords={q}",
         home="https://www.amiami.com/eng/", status="shield",
         hint="新品预售与再贩价格"),
    dict(id="hobbysearch", name="Hobby Search (1999)", site="1999.co.jp",
         tpl="https://www.1999.co.jp/eng/search?txtSearch={q}",
         home="https://www.1999.co.jp/eng/", status="shield",
         hint="老牌模型店的发售资料"),
    dict(id="gsc", name="Good Smile Company", site="goodsmile.com",
         tpl="", home="https://www.goodsmile.com/", status="maintenance",
         hint="厂商官网（GSC / Max Factory / FREEing）；官网维护中，用「站内精准找」定位官方商品页"),
    dict(id="kotobukiya", name="寿屋 Kotobukiya", site="kotobukiya.co.jp",
         tpl="", home="https://www.kotobukiya.co.jp/", status="shield",
         hint="厂商官网；站内参数不稳定，建议用「站内精准找」"),
    dict(id="tamashii", name="TAMASHII WEB", site="tamashiiweb.com",
         tpl="https://tamashiiweb.com/search/?q={q}",
         home="https://tamashiiweb.com/", status="ok",
         hint="万代魂官网，高达 / 机器人 / 可动成品"),
    dict(id="amazon_jp_fig", name="日亚(新品/再贩)", site="amazon.co.jp",
         tpl="https://www.amazon.co.jp/s?k={q}",
         home="https://www.amazon.co.jp/", status="ok",
         hint="看当前新品与再贩定价"),
]

STATUS_LABEL = {"ok": "", "shield": "官网有反爬盾，优先用精准找",
                "maintenance": "官网维护中，优先用精准找"}

PLATFORM_BY_ID = {p["id"]: p for p in PLATFORMS}

SOURCE_PATTERNS = [
    ("jp.mercari.com/item/", "煤炉 Mercari", "mercari"),
    ("auctions.yahoo.co.jp/jp/auction/", "雅虎日拍", "yahoo_auction"),
    ("www.suruga-ya.jp/product", "骏河屋", "surugaya"),
    ("www.amazon.co.jp", "日本亚马逊", "amazon_jp"),
    ("search.rakuten.co.jp", "乐天市场", "rakuten"),
    ("item.rakuten.co.jp", "乐天市场", "rakuten"),
    ("hpoi.net/hobby/", "Hpoi 手办资料", "hpoi"),
]


def normalize_keyword(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"https?://\S+", " ", text)
    s = re.sub(r"[#【】\[\]（）()|｜/／,，。.、!！?？~～]+", " ", s)
    s = re.sub(r"(包邮|包税|现货|全新|未开封|美品|中古|限定|日版|代购|直邮|日元|人民币|￥|¥)\s*\d*", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:120]


def detect_source(text: str) -> dict[str, Any] | None:
    m = re.search(r"https?://[^\s\"'<>]+", text or "")
    if not m:
        return None
    url = m.group(0)
    for needle, label, pid in SOURCE_PATTERNS:
        if needle in url:
            return {"platform": label, "platform_id": pid, "url": url}
    if "52ritao.cn" in url or "ritao.hk" in url:
        return {"platform": "日淘市集", "platform_id": "ritao", "url": url}
    return {"platform": "外部链接", "platform_id": None, "url": url}


def _aucfan_q(keyword: str) -> str:
    """AucFan encodes keywords as EUC-JP bytes in a dotted-hex path segment."""
    try:
        raw = keyword.encode("euc-jp", "replace")
    except Exception:
        raw = keyword.encode("utf-8", "replace")
    out = []
    for b in raw:
        if 0x30 <= b <= 0x39 or 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A:
            out.append(chr(b))
        else:
            out.append("." + format(b, "02x"))
    return "".join(out)


PRICED_PLATFORMS = {"rakuten", "surugaya", "yahoo_closed", "mercari", "xianyu"}


def build_links(keyword: str, jp_keyword: str = "", enabled: list[str] | None = None) -> list[dict[str, Any]]:
    q_cn = urllib.parse.quote(normalize_keyword(keyword))
    q_jp = urllib.parse.quote(normalize_keyword(jp_keyword)) if jp_keyword.strip() else q_cn
    out = []
    for p in PLATFORMS:
        if enabled is not None and p["id"] not in enabled:
            continue
        use_jp = bool(jp_keyword.strip()) and p["region"] == "日本"
        kw = normalize_keyword(jp_keyword) if use_jp else normalize_keyword(keyword)
        if not kw:
            continue
        q = _aucfan_q(kw) if p["id"] == "aucfan" else (q_jp if use_jp else q_cn)
        out.append({
            "id": p["id"], "name": p["name"], "region": p["region"], "kind": p["kind"],
            "group": p["group"], "hint": p["hint"], "url": p["tpl"].format(q=q),
            "keyword_used": kw, "priced": p["id"] in PRICED_PLATFORMS,
        })
    return out


def build_figure_links(keyword: str, jp_keyword: str = "") -> list[dict[str, Any]]:
    """For every figure site give BOTH its own search URL and a Bing site:
    search. Many maker sites sit behind Cloudflare or are under maintenance,
    so the site-restricted search is the reliable way to land on the original
    release page with the keyword already filled in.
    """
    cn = normalize_keyword(keyword)
    jp = normalize_keyword(jp_keyword) or cn
    out = []
    for p in FIGURE_PLATFORMS:
        kw = cn if p["id"] == "hpoi" else jp
        q = urllib.parse.quote(kw)
        find_q = urllib.parse.quote(f"site:{p['site']} {kw}")
        out.append({
            "id": p["id"], "name": p["name"], "hint": p["hint"],
            "status": p["status"], "status_label": STATUS_LABEL.get(p["status"], ""),
            "url": p["tpl"].format(q=q) if p["tpl"] else "",
            "home": p["home"],
            "find": f"https://www.bing.com/search?q={find_q}",
        })
    return out


def _opener(proxy: str):
    if proxy.strip():
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy.strip(), "https": proxy.strip()}))
    return urllib.request.build_opener()


# Hosts that genuinely need the proxy from mainland China.
_PROXY_HOSTS = ("rakuten.co.jp", "yahoo.co.jp", "aucfan.com", "mercari.com",
                "suruga-ya.jp", "amazon.co.jp", "1999.co.jp", "goodsmile.com",
                "kotobukiya.co.jp", "tamashiiweb.com", "myfigurecollection.net",
                "amiami.com")
_PROXY_STATE = {"checked": 0.0, "alive": False}


def proxy_alive(proxy: str) -> bool:
    """Quick TCP probe with a 20s cache, so a dead Clash port fails fast."""
    if not proxy.strip():
        return False
    now = time.time()
    if now - _PROXY_STATE["checked"] < 20:
        return _PROXY_STATE["alive"]
    ok = False
    try:
        u = urllib.parse.urlparse(proxy)
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=0.6):
            ok = True
    except Exception:
        ok = False
    _PROXY_STATE.update(checked=now, alive=ok)
    return ok


def _need_proxy(url: str) -> bool:
    return any(h in url for h in _PROXY_HOSTS)


def _headers(referer: str | None = None) -> dict[str, str]:
    # The new Rakuten openapi platform rejects / rate-limits requests carrying
    # an `Origin: http://localhost` header. Only send Referer, never Origin.
    h = {"User-Agent": UA, "Accept": "application/json, text/html, */*",
         "Accept-Language": "ja,zh-CN;q=0.9,en;q=0.8"}
    if referer:
        h["Referer"] = referer
    return h


def _fetch(url: str, timeout: int = 15, proxy: str = "",
           referer: str | None = None, retries: int = 2) -> str:
    # Fail fast with a clear message when Clash is configured but not running.
    # Domestic hosts (Hpoi, Bing) simply fall back to a direct connection.
    use_proxy = proxy.strip()
    if use_proxy and not proxy_alive(use_proxy):
        if _need_proxy(url):
            raise RuntimeError("代理未启动（Clash Verge 端口无监听），请启动代理后重试")
        use_proxy = ""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=_headers(referer))
            with _opener(use_proxy).open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.4 * (attempt + 1))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries and "代理未启动" not in str(exc):
                time.sleep(0.8)
                continue
            raise
    raise last  # type: ignore[misc]


def _get_json(url: str, timeout: int = 15, proxy: str = "") -> dict[str, Any]:
    return json.loads(_fetch(url, timeout, proxy))


# Rakuten's new API allows ~1 request/second across the whole application.
_RAKUTEN_LOCK = threading.Lock()
_RAKUTEN_LAST = [0.0]


def _rakuten_throttle() -> None:
    with _RAKUTEN_LOCK:
        wait = 1.25 - (time.time() - _RAKUTEN_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _RAKUTEN_LAST[0] = time.time()


def _big_image(url: str) -> str:
    """Ask the CDNs for a larger rendition. Both Rakuten and Yahoo accept a
    size hint in the URL, so the gallery view stays sharp without extra calls."""
    if not url:
        return ""
    url = url.replace("_ex=128x128", "_ex=500x500")
    url = re.sub(r"([?&])w=300\b", r"\g<1>w=600", url)
    url = re.sub(r"([?&])h=300\b", r"\g<1>h=600", url)
    return url


def _item(title: str, price: Any, url: str, platform: str, extra: dict | None = None) -> dict[str, Any]:
    try:
        value = float(str(price).replace(",", ""))
    except (TypeError, ValueError):
        value = None
    d = {"platform": platform, "title": (title or "").strip()[:160], "price": value,
         "currency": "JPY", "url": url or "", "source": "official_api"}
    if extra:
        d.update(extra)
    return d


def rakuten_search(keyword: str, app_id: str, access_key: str, affiliate_id: str = "",
                   hits: int = 20, proxy: str = "") -> dict[str, Any]:
    """Tier B -- new Rakuten Web Service platform (UUID app ID + pk_ access key)."""
    if not app_id or not access_key:
        return {"ok": False, "reason": "missing_key", "items": []}
    params = {"format": "json", "applicationId": app_id, "accessKey": access_key,
              "keyword": keyword, "hits": min(max(hits, 1), 30), "sort": "+itemPrice"}
    if affiliate_id:
        params["affiliateId"] = affiliate_id
    url = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401?" + urllib.parse.urlencode(params)
    try:
        _rakuten_throttle()
        data = _get_json(url, proxy=proxy)
        if "statusCode" in data and "Items" not in data:
            return {"ok": False, "reason": f"楽天限流({data.get('statusCode')})：{data.get('message', '')}", "items": []}
        if "errors" in data:
            err = data["errors"]
            code, msg = err.get("errorCode"), err.get("errorMessage", "")
            friendly = {
                "CLIENT_IP_NOT_ALLOWED": "出口 IP 不在楽天应用的 IP 白名单里（到 webservice.rakuten.co.jp/app/list 编辑应用）",
                "REQUEST_CONTEXT_BODY_HTTP_REFERRER_MISSING": "缺少 Referer，请检查代理是否改写请求头",
            }.get(msg, f"{code}: {msg}")
            return {"ok": False, "reason": friendly, "items": []}
        items = []
        for row in data.get("Items", []):
            it = row.get("Item", {})
            items.append(_item(it.get("itemName"), it.get("itemPrice"), it.get("itemUrl"),
                               "乐天市场", {
                                   "shop": it.get("shopName"),
                                   "reviews": it.get("reviewCount") or 0,
                                   "rating": it.get("reviewAverage") or 0,
                                   "free_shipping": it.get("postageFlag") == 0,
                                   "in_stock": it.get("availability") == 1,
                                   "image": _big_image((it.get("mediumImageUrls") or [{}])[0].get("imageUrl") or "")}))
        return {"ok": True, "items": items, "total": data.get("count", len(items))}
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None)
        if code == 503:
            return {"ok": False, "reason": "楽天鉴权服务暂时不可用(503)，稍后会自动重试；若持续，请到 webservice.rakuten.co.jp/app/list 检查应用 IP 白名单", "items": []}
        if code == 403:
            return {"ok": False, "reason": "被拒绝(403)：多半是出口 IP 不在应用白名单里，请到 webservice.rakuten.co.jp/app/list 编辑应用", "items": []}
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "items": []}


def _yahoo_categories(h: str) -> list[dict[str, str]]:
    """Category facets are embedded in the page as auccat=<id> links, so we
    never hard-code Yahoo's category IDs (they change)."""
    out: dict[str, str] = {}
    for cid, name in re.findall(r'auccat=(\d+)[^>]*>\s*([^<]{1,40})', h):
        name = html.unescape(name).strip()
        if cid == "0" or not name or len(name) > 24:
            continue
        if re.fullmatch(r"[0-9,\s]+", name):  # prices leak into this pattern
            continue
        if name in ("〜", "～", "次へ", "前へ", "すべて", "オークション", "-", "—") or len(name) < 2:
            continue
        out[cid] = name
    return [{"id": k, "name": v} for k, v in out.items()]


def _infer_yahoo_category(keyword: str, cats: list[dict[str, str]]) -> dict[str, str] | None:
    low = (keyword or "").lower()
    for hints, targets in dicts.YAHOO_CATEGORY_HINTS:
        if not any(h.lower() in low for h in hints):
            continue
        for target in targets:
            pick = [c for c in cats if target in c["name"]
                    and "おもちゃ" not in c["name"] and c["name"] != "その他"]
            if pick:
                return pick[0]
    return None


def _yahoo_sold_items(h: str) -> list[dict[str, Any]]:
    """落札明細 cards. Each card carries a tracking blob with everything we
    need: ``cid`` (auction id), ``end`` (unix end time) and ``etc:p=..,b=..``
    (realised price + bid count) -- no extra request required."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(r'data-cl-params="([^"]*)"', h):
        blob = html.unescape(m.group(1))
        if "cid:" not in blob:
            continue
        fields = dict(kv.split(":", 1) for kv in blob.split(";") if ":" in kv)
        cid = fields.get("cid", "")
        if not cid or cid in seen:
            continue
        sub = dict(kv.split("=", 1) for kv in fields.get("etc", "").split(",") if "=" in kv)
        seg = h[m.end(): m.end() + 3200]
        tag = re.search(r'<img[^>]*>', seg)
        if not tag:
            continue
        tag = tag.group(0)
        alt = re.search(r'alt="([^"]*)"', tag)
        src = re.search(r'src="([^"]+)"', tag)
        title = html.unescape(alt.group(1)).strip() if alt else ""
        if not title:
            continue
        end_ts = fields.get("end") or sub.get("etm") or ""
        ended_at = int(end_ts) if str(end_ts).isdigit() else None
        price = sub.get("p") or ""
        bids = sub.get("b") or ""
        seen.add(cid)
        items.append({
            "title": title,
            "image": _big_image(html.unescape(src.group(1))) if src else "",
            "url": f"https://auctions.yahoo.co.jp/jp/auction/{cid}",
            "price": int(price) if price.isdigit() else None,
            "bids": int(bids) if bids.isdigit() else None,
            "ended_at": ended_at,
            "free_shipping": "送料無料" in seg[:1400],
            "currency": "JPY", "platform": "雅虎落札", "source": "yahoo_souba",
        })
    return items


def yahoo_souba(keyword: str, proxy: str = "", category: str | None = None,
                attrs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Yahoo! Auctions' OWN 落札相場 page. Free, no login, no Japanese phone.

    category: None/'auto' = infer from keyword; a category id/name = force it;
    'all' = no filter. Returns the facet list so the UI can offer a picker.
    attrs: identity from query_engine; when present, non-matching listings are
    removed before the statistics are computed.
    """
    kw = normalize_keyword(keyword)
    # Bundle listings dominate keyword statistics; exclude them at the source.
    kw_search = (kw + " -まとめ -ジャンク").strip()
    base = "https://auctions.yahoo.co.jp/closedsearch/closedsearch?" + urllib.parse.urlencode(
        {"p": kw_search, "ei": "utf-8"})
    ref = "https://auctions.yahoo.co.jp/"
    url = base
    try:
        h = _fetch(base, timeout=18, proxy=proxy, referer=ref)
        cats = _yahoo_categories(h)
        chosen = None
        if category and category not in ("auto", "all", ""):
            chosen = next((c for c in cats if c["id"] == str(category) or c["name"] == category), None)
        elif category != "all":
            chosen = _infer_yahoo_category(kw, cats)
        if chosen:
            url = base + "&auccat=" + chosen["id"] + "&fixed=0&b=1&n=50&select=6&mode=3"
            h = _fetch(url, timeout=18, proxy=proxy, referer=ref)
            # Keep facet names from the UNFILTERED page: the filtered page
            # rewrites the breadcrumb text (25888 showed up as "アルター").
            if chosen and not any(c["id"] == chosen["id"] for c in cats):
                cats.insert(0, chosen)
        labels = re.findall(r'aria-label="(最安価格|平均価格|最高価格)\s*([0-9,]+)円', h)
        stats = {name: int(val.replace(",", "")) for name, val in labels}
        meta = re.search(r'約([0-9,]+)件の落札価格は平均([0-9,]+)', h)
        count = int(meta.group(1).replace(",", "")) if meta else None
        days = re.search(r'過去(\d+)日分の落札相場', h)
        if "平均価格" not in stats and meta:
            stats["平均価格"] = int(meta.group(2).replace(",", ""))
        detail = _yahoo_sold_items(h)
        raw_count = count
        filtered_out = 0
        if attrs and (attrs.get("character") or attrs.get("series") or attrs.get("must")):
            kept = [it for it in detail if qe.judge(it.get("title", ""), attrs)[0] > 0]
            filtered_out = len(detail) - len(kept)
            if len(kept) >= 3:
                detail = kept
                prices = [i["price"] for i in detail if i.get("price")]
                if prices:
                    stats = {"最安価格": min(prices),
                             "平均価格": round(sum(prices) / len(prices)),
                             "最高価格": max(prices)}
                    count = len(prices)
            elif kept:
                detail = kept
        note = ("已按分类「" + chosen["name"] + "」过滤" if chosen else
                "Yahoo!オークション 官方落札相場，免登录；可选择分类剔除跨品类商品。")
        if filtered_out:
            note += f"；已剔除 {filtered_out} 条非同款落札"
        return {
            "ok": bool(stats), "url": url, "stats": stats, "count": count,
            "raw_count": raw_count, "filtered_out": filtered_out,
            "days": int(days.group(1)) if days else 120,
            "categories": cats, "category": chosen, "items": detail,
            "note": note,
            "reason": "" if stats else "页面结构变化，未解析到统计（可直接点开链接查看）",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "stats": {}, "count": None, "raw_count": None,
                "filtered_out": 0, "days": 120, "categories": [], "category": None,
                "items": [], "reason": f"{type(exc).__name__}: {exc}", "note": ""}


def surugaya_search(keyword: str, proxy: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """骏河屋 in-stock listings (server-rendered HTML, no login).

    Amazon JP returns 503 to non-browser clients, Mandarake redirects foreign
    IPs to a landing page and Taobao requires a login. 駿河屋 is the one
    additional Japanese shop that reliably exposes prices in HTML, so it is
    the one wired into the workbench.
    """
    q = urllib.parse.quote(normalize_keyword(keyword))
    url = f"https://www.suruga-ya.jp/search?search_word={q}"
    try:
        h = _fetch(url, timeout=20, proxy=proxy, referer="https://www.suruga-ya.jp/")
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for block in h.split('<div class="item">')[1:limit + 2]:
        title = re.search(r'<h3 class="product-name">(.*?)</h3>', block, re.S)
        link = re.search(r'href="(https://www\.suruga-ya\.jp/product/detail/\d+)"', block)
        img = re.search(r'<img src="([^"]*photo\.php[^"]*)"', block)
        price = re.search(r'(?:中古|新品)：.*?￥\s*([0-9,]+)', block, re.S)
        condition = re.search(r'<p class="condition[^"]*"[^>]*>\s*([^<]+?)\s*</p>', block)
        release = re.search(r'\[発売日：([0-9/]+)\]', block)
        brand = re.search(r'<p class="brand">\[([^\]]+)\]', block)
        if not (title and link and price):
            continue
        clean = lambda x: html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x or ""))).strip()  # noqa: E731
        out.append({
            "platform": "骏河屋", "title": clean(title.group(1)),
            "price": int(price.group(1).replace(",", "")), "currency": "JPY",
            "url": link.group(1), "image": (img.group(1) if img else ""),
            "shop": "駿河屋", "source": "surugaya",
            "condition": clean(condition.group(1)) if condition else "",
            "release": release.group(1) if release else "",
            "brand": clean(brand.group(1)) if brand else "",
        })
    return out


def hpoi_search(keyword: str, proxy: str = "") -> dict[str, Any]:
    """Hpoi 手办维基 search results (server-rendered HTML), best effort."""
    q = urllib.parse.quote(normalize_keyword(keyword))
    url = f"https://www.hpoi.net/search?category=100&keyword={q}"
    try:
        h = _fetch(url, timeout=18, proxy=proxy, referer="https://www.hpoi.net/")
        blocks = re.findall(r'<li class="media ibox-content">(.*?)</li>', h, re.S)
        items = []
        for b in blocks[:10]:
            link = re.search(r'href="(hobby/\d+)"', b)
            title = re.search(r'<h4 class="media-heading">\s*<a[^>]*>(.*?)</a>', b, re.S)
            img = re.search(r'<img class="list-item-img" src="([^"]+)"', b)
            detail = re.search(r'<p class="list-item-detail">(.*?)</p>', b, re.S)
            tags = re.findall(r'<span class="label label-tag">([^<]+)</span>', b)
            clean = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()  # noqa: E731
            desc = html.unescape(clean(detail.group(1) if detail else ""))
            price = re.search(r'([0-9,]+)\s*(?:日元|円)', desc)
            date = re.search(r'(20\d{2})\s*年\s*(\d{1,2})\s*月', desc)
            if not link and not title:
                continue
            items.append({
                "title": html.unescape(clean(title.group(1) if title else "")),
                "url": "https://www.hpoi.net/" + link.group(1) if link else url,
                "image": img.group(1) if img else "",
                "tags": [html.unescape(t) for t in tags[:8]],
                "detail": desc[:400],
                "release": f"{date.group(1)}年{date.group(2)}月" if date else "",
                "list_price": int(price.group(1).replace(",", "")) if price else None,
            })
        return {"ok": True, "items": items, "url": url,
                "reason": "" if items else "未解析到结果，可直接点开 Hpoi 链接"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "items": [], "url": url, "reason": f"{type(exc).__name__}: {exc}"}


def official_prices(keyword: str, cfg: dict[str, Any]) -> dict[str, Any]:
    proxy = cfg.get("proxy", "")
    return {"rakuten": rakuten_search(
        keyword, cfg.get("rakuten_app_id", "").strip(),
        cfg.get("rakuten_access_key", "").strip(),
        cfg.get("rakuten_affiliate_id", "").strip(), proxy=proxy)}
