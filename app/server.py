"""Ritao price companion -- local HTTP service (Python standard library only).

Run:  python app/server.py
Then: http://127.0.0.1:8787  (PC)  /  http://<LAN-IP>:8787  (phone, same Wi-Fi)

Secrets policy: API credentials live ONLY in app/config.json on this machine.
GET /api/config never returns them (only has_* booleans). Nothing is logged,
nothing is sent anywhere except the official Rakuten API endpoint.
"""
from __future__ import annotations

import hashlib
import json
import re
import mimetypes
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
WEB = ROOT.parent / "web"
CONFIG_PATH = ROOT / "config.json"

sys.path.insert(0, str(ROOT))
import browser  # noqa: E402
import dicts  # noqa: E402
import llm  # noqa: E402
import media  # noqa: E402
import query_engine as qe  # noqa: E402
import sources  # noqa: E402
import store  # noqa: E402
import videos  # noqa: E402

SECRET_KEYS = {"rakuten_app_id", "rakuten_access_key", "rakuten_affiliate_id", "llm_api_key"}

# ---------------------------------------------------------------- image cache
# Some shops (Hpoi in particular) reject hotlinks unless the Referer is their
# own site, which a page served from 127.0.0.1 can never supply. Fetching the
# thumbnail once on the server and caching it locally fixes that without
# touching the browser's privacy settings.
IMG_CACHE = ROOT / "data" / "imgcache"
IMG_ALLOW = ("hpoi.net", "rakuten.co.jp", "yimg.jp", "yahoo.co.jp", "mercari.com",
             "suruga-ya.jp", "amazon.co.jp", "aucfan.com", "amazon.com",
             "ytimg.com", "ggpht.com", "nicovideo.jp", "hdslb.com")
IMG_REFERER = {
    "hpoi.net": "https://www.hpoi.net/",
    "rakuten.co.jp": "https://www.rakuten.co.jp/",
    "yimg.jp": "https://auctions.yahoo.co.jp/",
    "yahoo.co.jp": "https://auctions.yahoo.co.jp/",
    "mercari.com": "https://jp.mercari.com/",
    "suruga-ya.jp": "https://www.suruga-ya.jp/",
    "aucfan.com": "https://aucfan.com/",
    "ytimg.com": "https://www.youtube.com/",
    "ggpht.com": "https://www.youtube.com/",
    "nicovideo.jp": "https://www.nicovideo.jp/",
    "hdslb.com": "https://www.bilibili.com/",
    "amazon.co.jp": "https://www.amazon.co.jp/",
    "amazon.com": "https://www.amazon.com/",
}


def _img_fetch(url: str, proxy: str = "") -> tuple[bytes, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("bad url")
    host = parsed.hostname.lower()
    if not any(host == h or host.endswith("." + h) for h in IMG_ALLOW):
        raise ValueError("host not allowed")
    referer = next((v for k, v in IMG_REFERER.items() if host == k or host.endswith("." + k)), "")
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    ext = Path(parsed.path).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    IMG_CACHE.mkdir(parents=True, exist_ok=True)
    cached = IMG_CACHE / (key + ext)
    if cached.exists() and time.time() - cached.stat().st_mtime < 30 * 86400:
        ctype = mimetypes.guess_type(cached.name)[0] or "image/jpeg"
        return cached.read_bytes(), ctype

    headers = {"User-Agent": sources.UA, "Accept": "image/*,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    # Japanese CDNs are usually unreachable without the proxy, while Hpoi and
    # Rakuten thumbnails work directly. Pick the likely route first so the
    # fallback doesn't cost a 20s timeout per image.
    jp_only = any(host == h or host.endswith("." + h)
                  for h in ("yimg.jp", "yahoo.co.jp", "mercari.com", "suruga-ya.jp",
                            "aucfan.com", "amazon.co.jp"))
    direct = (urllib.request.build_opener(), 8)
    proxied = None
    if proxy.strip():
        proxied = (urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy.strip(), "https": proxy.strip()})), 20)
    order = [proxied, direct] if (proxied and jp_only) else [direct] + ([proxied] if proxied else [])
    last: Exception | None = None
    for opener, timeout in order:
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read(6 * 1024 * 1024)
                ctype = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            if not ctype.startswith("image/"):
                raise ValueError("not an image")
            cached.write_bytes(body)
            return body, ctype
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last  # type: ignore[misc]

DEFAULT_CONFIG: dict[str, Any] = {
    "jpy_cny": 0.048,
    "ritao_price_jpy": None,
    "proxy": "",
    "rakuten_app_id": "",
    "rakuten_access_key": "",
    "rakuten_affiliate_id": "",
    "enable_browser": False,
    "browser_headless": False,
    "browser_min_interval": 5,
    "browser_max_per_hour": 20,
    # Goofish blocks automated browsers outright; keep it opt-in so the default
    # Tier C never touches it. Mercari works and is enough for JP used prices.
    "browser_platforms": ["mercari"],
    # Optional OpenAI-compatible LLM. Empty = rule-based rewriting only.
    # Off for a fresh install: the tool is fully usable with zero model calls
    # and zero cost. Users opt in from Settings. Credentials may stay saved but
    # no request is ever sent while this is False.
    "use_llm": False,
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "gpt-4o-mini",
    "llm_vision_model": "",
    "theme": "light",
    "enabled_platforms": [p["id"] for p in sources.PLATFORMS],
    "host": "0.0.0.0",
    "port": 8787,
}

_cfg_lock = threading.Lock()


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_CONFIG)
            merged.update(data)
            return merged
        except (OSError, ValueError):
            pass
    return dict(DEFAULT_CONFIG)


def public_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Never let a LAN client read the credentials stored on this machine."""
    out = {k: v for k, v in cfg.items() if k not in SECRET_KEYS}
    out["has_rakuten_app_id"] = bool(cfg.get("rakuten_app_id"))
    out["has_rakuten_access_key"] = bool(cfg.get("rakuten_access_key"))
    out["has_rakuten_affiliate_id"] = bool(cfg.get("rakuten_affiliate_id"))
    out["has_llm_key"] = bool(cfg.get("llm_api_key"))
    out["llm_configured"] = llm.configured(cfg)
    out["llm_enabled"] = cfg.get("use_llm", True) is not False
    out["llm_ready"] = llm.enabled(cfg)
    out["vision_ready"] = llm.vision_available(cfg)
    return out


def save_config(data: dict[str, Any]) -> dict[str, Any]:
    with _cfg_lock:
        cur = load_config()
        for key, val in data.items():
            if key not in DEFAULT_CONFIG:
                continue
            if key in SECRET_KEYS and (val is None or not str(val).strip()):
                continue  # empty means "keep the existing secret"
            cur[key] = val
        if data.get("clear_rakuten"):
            for k in SECRET_KEYS:
                cur[k] = ""
        CONFIG_PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        return cur


def _json_list(raw: str, limit: int = 12) -> list[str]:
    try:
        val = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(val, list):
        return []
    return [str(x).strip() for x in val if str(x).strip()][:limit]


def _is_lan(ip: str) -> bool:
    if not ip or ip.startswith("127.") or ip == "0.0.0.0":
        return False
    # Clash TUN uses 198.18.0.0/15 for fake-ip; it is not reachable from a phone.
    if ip.startswith("198.18.") or ip.startswith("198.19."):
        return False
    return (ip.startswith("192.168.") or ip.startswith("10.")
            or any(ip.startswith(f"172.{n}.") for n in range(16, 32)))


def lan_ip() -> str:
    """Prefer a real private LAN address over a VPN/TUN fake-ip."""
    candidates: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except OSError:
        pass
    for ip in candidates:
        if _is_lan(ip):
            return ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        if _is_lan(ip):
            return ip
        return candidates[0] if candidates else ip
    except OSError:
        return "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    server_version = "RitaoCompare/2.0"

    def log_message(self, fmt: str, *args) -> None:
        # Never log bodies (they may contain credentials).
        if "GET /api/health" not in (args[0] if args else ""):
            print(f"  {self.address_string()} {fmt % args}", flush=True)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str) -> None:
        if not path.exists():
            self._json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _download(self, text: str, filename: str, ctype: str = "text/csv; charset=utf-8") -> None:
        body = text.encode("utf-8")
        # HTTP headers are latin-1. A Japanese keyword in the filename used to
        # raise UnicodeEncodeError mid-response, which is why the browser
        # showed ERR_EMPTY_RESPONSE. RFC 5987 keeps the readable name.
        ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "export.csv"
        utf8_name = urllib.parse.quote(filename, safe="")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        get = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
        cfg = load_config()

        if u.path in ("/", "/index.html"):
            self._file(WEB / "index.html", "text/html; charset=utf-8")
        elif u.path == "/style.css":
            self._file(WEB / "style.css", "text/css; charset=utf-8")
        elif u.path == "/app.js":
            self._file(WEB / "app.js", "application/javascript; charset=utf-8")
        elif u.path == "/favicon.ico":
            self.send_response(204); self.end_headers()

        elif u.path == "/api/health":
            pw_ok, pw_note = browser.available()
            proxy = cfg.get("proxy", "")
            self._json({"ok": True, "lan_ip": lan_ip(), "port": cfg["port"],
                        "playwright": pw_ok, "playwright_note": pw_note,
                        "chrome": browser.chrome_exe() or "",
                        "proxy": proxy, "proxy_alive": sources.proxy_alive(proxy) if proxy else None,
                        "version": "3.0"})
        elif u.path == "/api/config":
            self._json(public_config(cfg))
        elif u.path == "/api/safety":
            self._json(browser.safety_info(cfg))
        elif u.path == "/api/links":
            kw = get("keyword")
            jp = get("jp")
            if not kw.strip():
                self._json({"links": [], "source": None})
                return
            self._json({"keyword": sources.normalize_keyword(kw),
                        "jp_keyword": sources.normalize_keyword(jp),
                        "links": sources.build_links(kw, jp, cfg["enabled_platforms"]),
                        "source": sources.detect_source(get("source_url") or kw)})
        elif u.path == "/api/media":
            attrs = None
            try:
                raw = get("attrs")
                attrs = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                attrs = None
            self._json(media.search(get("keyword"), get("jp"), attrs, cfg.get("proxy", "")))

        elif u.path == "/api/videos":
            kw = get("keyword")
            jp = get("jp")
            attrs = None
            try:
                raw = get("attrs")
                attrs = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                attrs = None
            self._json(videos.search(kw, jp, cfg.get("proxy", ""), attrs, get("cn") or ""))

        elif u.path == "/api/visual":
            self._json({"sites": dicts.VISUAL_SITES,
                        "llm_enabled": cfg.get("use_llm", True) is not False,
                        "vision_ready": llm.vision_available(cfg)})

        elif u.path == "/api/rewrite":
            kw, jp = get("keyword"), get("jp")
            if not kw.strip():
                self._json({"candidates": [], "attrs": {}, "mode": "rules"})
                return
            lq, mode, err, lattr = [], "rules", "", None
            if llm.enabled(cfg):
                r = llm.rewrite(kw, jp, cfg)
                lq, mode, err = r.get("queries", []), r.get("mode", "rules"), r.get("error", "")
                lattr = r.get("attrs") or None
            built = qe.build_candidates(kw, jp, lq, lattr)
            self._json({**built, "mode": mode, "error": err,
                        "llm_enabled": cfg.get("use_llm", True) is not False,
                        "llm_ready": llm.enabled(cfg)})

        elif u.path == "/api/tokens":
            self._json({"cn": qe.segment(get("keyword"), "cn"),
                        "jp": qe.segment(get("jp"), "jp")})

        elif u.path == "/api/plan":
            kw, jp = get("keyword"), get("jp")
            drop = _json_list(get("drop"))
            cn2, jp2 = qe.apply_drops(kw, jp, drop)
            built = qe.build_candidates(cn2, jp2, [])
            self._json({**built, "cn": cn2, "jp": jp2,
                        "cn_query": qe.cn_query(cn2, built["attrs"]),
                        "tokens": {"cn": qe.segment(kw, "cn"),
                                   "jp": qe.segment(jp, "jp")},
                        "dropped": drop})

        elif u.path == "/api/search":
            kw, jp = get("keyword"), get("jp")
            if not kw.strip():
                self._json({"items": [], "candidates": [], "souba": {}, "links": []})
                return
            deep = get("deep", "0") == "1"
            drop = _json_list(get("drop"))
            custom = _json_list(get("queries"), 3)
            kw, jp = qe.apply_drops(kw, jp, drop)
            lq, mode, llm_err, lattr = [], "rules", "", None
            # A user-confirmed plan already contains the queries; no need to
            # spend a model call (and no risk of the model changing them).
            if not custom and llm.enabled(cfg):
                r = llm.rewrite(kw, jp, cfg)
                lq, mode, llm_err = r.get("queries", []), r.get("mode", "rules"), r.get("error", "")
                lattr = r.get("attrs") or None
            built = qe.build_candidates(kw, jp, lq, lattr)
            cands = built["candidates"]
            attrs = built["attrs"]
            queries = {c["id"]: c["query"] for c in cands}
            if custom:
                jp_cands = [{"id": f"custom{i + 1}", "label": f"方案 {i + 1}",
                             "query": q, "usable": True, "priority": 1}
                            for i, q in enumerate([x for x in custom if len(x) <= 70])]
            else:
                # Identity-first selection: at most one AI candidate, then the
                # rule-generated character/detail queries.
                usable = [c for c in cands if c.get("usable")]
                pool = sorted(usable, key=lambda c: c.get("priority", 9))
                jp_cands = pool[:2]
            if not jp_cands:
                jp_cands = [c for c in cands if c["id"] == "clean"][:1] or cands[:1]
            main_jp = jp_cands[0]["query"] if jp_cands else kw
            souba_q = main_jp if custom else (built.get("souba_query") or main_jp)
            only_relevant = get("only_relevant", "1") == "1"
            cn_clean = qe.normalize(kw)
            # The plan dialog lets the user rewrite the Chinese query by hand;
            # 闲鱼 / 淘宝 / 视频 / 手办站 all follow it when provided.
            cn_in = qe.normalize(get("cn") or "")
            cn_q = cn_in or qe.cn_query(kw, attrs) or cn_clean
            proxy = cfg.get("proxy", "")

            def http_work():
                items, per, errs = [], [], []
                for c in jp_cands:
                    r = sources.rakuten_search(
                        c["query"], cfg.get("rakuten_app_id", "").strip(),
                        cfg.get("rakuten_access_key", "").strip(),
                        cfg.get("rakuten_affiliate_id", "").strip(),
                        hits=20, proxy=proxy)
                    for it in r.get("items", []):
                        it["candidate"] = c["id"]
                    items.extend(r.get("items", []))
                    per.append({"id": c["id"], "label": c["label"], "query": c["query"],
                                "count": len(r.get("items", [])), "ok": r["ok"]})
                    if not r["ok"]:
                        errs.append({"source": "乐天/" + c["label"], "reason": r.get("reason", "")})
                # 駿河屋 is the one extra Japanese shop whose prices are in
                # plain HTML; Amazon / Mandarake / Taobao block scraping.
                try:
                    sur = sources.surugaya_search(main_jp, proxy)
                    if not sur:
                        broad = queries.get("jpseries") or queries.get("jpedition") or ""
                        if broad and broad != main_jp:
                            sur = sources.surugaya_search(broad, proxy)
                except Exception as exc:  # noqa: BLE001
                    sur = []
                    errs.append({"source": "骏河屋", "reason": f"{type(exc).__name__}: {exc}"})
                for it in sur:
                    it["candidate"] = "jpquery"
                items.extend(sur)
                per.append({"id": "surugaya", "label": "骏河屋", "query": main_jp,
                            "count": len(sur), "ok": True})

                souba = sources.yahoo_souba(souba_q, proxy,
                                            category=get("souba_category") or None,
                                            attrs=attrs)
                # Rare items often have no Rakuten listing but do have sold
                # comps. Surface those in the workbench instead of an empty page.
                for it in souba.get("items", []):
                    row = dict(it)
                    row["platform"] = "雅虎落札"
                    row["candidate"] = "souba"
                    row["source"] = "yahoo_souba"
                    items.append(row)
                figs = sources.hpoi_search(cn_q, proxy)
                return items, per, errs, souba, figs

            def browser_work():
                if not deep or not cfg.get("enable_browser"):
                    return {}
                out = {}
                for pid in cfg.get("browser_platforms", ["mercari"]):
                    if pid not in ("xianyu", "mercari", "yahoo_closed"):
                        continue
                    if pid == "xianyu":
                        # One query is dominated by ¥36 祖国版 copies. Ask for
                        # the 日版 variant as well and merge: that is where the
                        # legitimate listings live.
                        merged, reasons, urls = [], [], set()
                        xq = cn_q
                        # 商品线本身就是检索词：figma / CD 这类拉丁词去掉
                        # "手办" 泛词后召回更准（"莱莎琳 figma"）。
                        cat = str((attrs or {}).get("category") or "")
                        if cat and re.fullmatch(r"[A-Za-z0-9 .+-]{2,20}", cat):
                            xq = re.sub(r"\s*(手办|模型|フィギュア)$", "", xq).strip()
                            if cat.lower() not in xq.lower():
                                xq = f"{xq} {cat}".strip()
                        for q in dict.fromkeys([xq, f"{xq} 日版"]):
                            r = browser.search(
                                "xianyu", q,
                                headless=bool(cfg.get("browser_headless", False)), proxy=proxy,
                                min_interval=float(cfg.get("browser_min_interval", 5)),
                                max_per_hour=int(cfg.get("browser_max_per_hour", 20)))
                            for it in r.get("items", []):
                                key = qe.url_key(it.get("url"))
                                if key and key not in urls:
                                    urls.add(key)
                                    merged.append(it)
                            if r.get("degraded"):
                                reasons.append(r.get("reason", ""))
                        out[pid] = {"ok": bool(merged), "items": merged,
                                    "degraded": not merged,
                                    "reason": reasons[0] if reasons and not merged else "",
                                    "queries": [xq, f"{xq} 日版"]}
                    else:
                        out[pid] = browser.search(
                            pid, main_jp,
                            headless=bool(cfg.get("browser_headless", False)), proxy=proxy,
                            min_interval=float(cfg.get("browser_min_interval", 5)),
                            max_per_hour=int(cfg.get("browser_max_per_hour", 20)))
                return out

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as pool:
                f_http, f_browser = pool.submit(http_work), pool.submit(browser_work)
                all_items, per, errs, souba, figs = f_http.result()
                br = f_browser.result()
            browser_labels = {"xianyu": "闲鱼", "mercari": "煤炉 Mercari",
                              "yahoo_closed": "雅虎日拍"}
            for pid, res in br.items():
                for it in res.get("items", []):
                    it["candidate"] = "jpquery"
                    it["platform"] = browser_labels.get(pid, it.get("platform") or pid)
                    all_items.append(it)
                if res.get("degraded"):
                    errs.append({"source": pid, "reason": res.get("reason", "")})

            # Quality layer for Chinese marketplaces: 闲鱼 search is flooded by
            # 祖国版 copies and by single-part listings. Tag both so the UI can
            # hide them by default without losing the ability to inspect them.
            cn_prices = sorted(float(i["price"]) for i in all_items
                               if i.get("currency") == "CNY" and i.get("price"))
            cn_med = cn_prices[len(cn_prices) // 2] if cn_prices else 0.0
            jp_prices = sorted(float(i["price"]) for i in all_items
                               if i.get("currency") == "JPY" and i.get("price")
                               and float(i["price"]) > 100)
            jp_med = jp_prices[len(jp_prices) // 2] if jp_prices else 0.0
            ref_cny = jp_med * float(cfg.get("jpy_cny", 0.048)) if jp_med else None
            for it in all_items:
                cur = it.get("currency") or "JPY"
                q = qe.assess(it.get("title", ""), it.get("price"), cur,
                              (cn_med or None) if cur == "CNY" else (jp_med or None),
                              reference_cny=ref_cny if cur == "CNY" else None)
                it["quality"] = q
                it["quality_score"] = q["score"]
                it["quality_label"] = q["label"]

            # Always return the full ranking and let the UI filter client-side:
            # toggling 「只看相关」must never fire a new search.
            ranked_all = qe.rerank(all_items, queries, attrs, limit=60,
                                   min_match=0, primary_query=main_jp)
            hidden_count = sum(1 for it in ranked_all if it.get("match", 0) < 42)
            ranked = ranked_all
            save_rows = [it for it in ranked_all if it.get("match", 0) >= 42]
            if souba.get("stats", {}).get("平均価格"):
                save_rows.append({"platform": "雅虎落札相場·平均", "price": souba["stats"]["平均価格"],
                                  "title": main_jp, "currency": "JPY",
                                  "url": souba.get("url", ""), "source": "official_souba"})
            saved = store.save_prices(cn_clean, save_rows)
            store.log_query(cn_clean, main_jp, "smart-search")
            by_platform: dict[str, list[dict[str, Any]]] = {}
            for it in ranked:
                by_platform.setdefault(it.get("platform") or "其他", []).append(it)
            self._json({
                "keyword": cn_clean, "cn_query": cn_q, "jp": main_jp, "attrs": attrs,
                "souba_query": souba_q, "needs_jp": built.get("needs_jp", False),
                "candidates": cands, "queries": queries, "mode": mode, "llm_error": llm_err,
                "llm_enabled": cfg.get("use_llm", True) is not False,
                "llm_used": mode == "llm",
                "per_candidate": per, "items": ranked, "by_platform": by_platform,
                "errors": errs, "souba": souba, "browser": br, "hpoi": figs,
                "hidden_count": hidden_count, "only_relevant": only_relevant,
                "cn_median": cn_med,
                "quality_filtered": sum(1 for i in ranked_all
                                        if any((i.get("quality") or {}).get(k) for k in
                                               ("bootleg", "parts", "suspect"))),
                "links": sources.build_links(cn_q, main_jp, cfg["enabled_platforms"]),
                "figure_links": sources.build_figure_links(cn_q, main_jp),
                "visual_sites": dicts.VISUAL_SITES,
                "history": {"history": store.history(cn_clean), "summary": store.summary(cn_clean)},
                "saved": saved, "jpy_cny": cfg["jpy_cny"],
            })

        elif u.path == "/api/img":
            try:
                body, ctype = _img_fetch(get("u"), cfg.get("proxy", ""))
            except Exception as exc:  # noqa: BLE001
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 502)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif u.path == "/api/image":
            name = get("name")
            if not name or "/" in name or ".." in name:
                self._json({"error": "bad name"}, 400)
                return
            p = llm.IMAGE_DIR / name
            if not p.exists():
                self._json({"error": "not found"}, 404)
                return
            ext = p.suffix.lower()
            self._file(p, {"jpg": "image/jpeg", ".jpg": "image/jpeg", ".png": "image/png",
                           ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg"))

        elif u.path == "/api/watchlist":
            self._json({"items": store.watch_list()})

        elif u.path == "/api/export":
            kw = qe.normalize(get("keyword"))
            if not kw:
                self._json({"error": "empty keyword"}, 400)
                return
            self._download(store.export_csv(kw), f"ritao_{kw[:12]}.csv")

        elif u.path == "/api/souba":
            kw = sources.normalize_keyword(get("jp") or get("keyword"))
            if not kw:
                self._json({"ok": False, "reason": "empty keyword", "stats": {}})
                return
            character, series, detail = get("character"), get("series"), get("detail")
            attrs = None
            if character or series:
                attrs = {"character": character, "series": series, "detail": detail,
                         "series_parts": [p for p in series.split() if len(p) >= 3]}
            res = sources.yahoo_souba(kw, cfg.get("proxy", ""),
                                      category=get("category") or None, attrs=attrs)
            if res.get("stats", {}).get("平均価格"):
                store.save_prices(sources.normalize_keyword(get("keyword")),
                                  [{"platform": "雅虎落札相場·平均", "price": res["stats"]["平均価格"],
                                    "title": kw, "currency": "JPY", "url": res.get("url", ""),
                                    "source": "official_souba"}])
            self._json(res)
        elif u.path == "/api/figures":
            kw = sources.normalize_keyword(get("keyword"))
            jp = sources.normalize_keyword(get("jp")) or kw
            if not kw:
                self._json({"links": [], "hpoi": {"ok": False, "items": []}})
                return
            self._json({"keyword": kw, "links": sources.build_figure_links(kw, jp),
                        "hpoi": sources.hpoi_search(kw, cfg.get("proxy", ""))})
        elif u.path == "/api/prices":
            kw = sources.normalize_keyword(get("keyword"))
            jp = sources.normalize_keyword(get("jp")) or kw
            if not kw:
                self._json({"official": {}, "browser": {}, "souba": {}, "saved": 0})
                return
            official = sources.official_prices(jp, cfg)
            souba = sources.yahoo_souba(jp, cfg.get("proxy", ""))
            browser_result: dict[str, Any] = {}
            if cfg.get("enable_browser"):
                # Yahoo 落札相場 comes from its public page above. Only the
                # platforms the user explicitly enabled are driven by Chrome.
                for pid in cfg.get("browser_platforms", ["mercari"]):
                    if pid not in ("xianyu", "mercari", "yahoo_closed"):
                        continue
                    browser_result[pid] = browser.search(
                        pid, kw if pid == "xianyu" else jp,
                        headless=bool(cfg.get("browser_headless", False)),
                        proxy=cfg.get("proxy", ""),
                        min_interval=float(cfg.get("browser_min_interval", 5)),
                        max_per_hour=int(cfg.get("browser_max_per_hour", 20)))
            all_items = []
            for res in official.values():
                all_items.extend(res.get("items", []))
            for res in browser_result.values():
                all_items.extend(res.get("items", []))
            if souba.get("stats", {}).get("平均価格"):
                all_items.append({"platform": "雅虎落札相場·平均", "price": souba["stats"]["平均価格"],
                                  "title": jp, "currency": "JPY", "url": souba.get("url", ""),
                                  "source": "official_souba"})
            saved = store.save_prices(kw, all_items)
            store.log_query(kw, jp, "prices")
            self._json({"official": official, "browser": browser_result, "souba": souba,
                        "saved": saved, "jpy_cny": cfg["jpy_cny"]})
        elif u.path == "/api/history":
            kw = sources.normalize_keyword(get("keyword"))
            self._json({"history": store.history(kw), "summary": store.summary(kw)})
        elif u.path == "/api/recent":
            self._json({"recent": store.recent_queries()})
        elif u.path == "/api/login":
            self._json(browser.open_login(get("platform"), cfg.get("proxy", ""),
                                          bool(cfg.get("browser_headless", False))))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        cfg = load_config()

        if u.path == "/api/config":
            self._json({"ok": True, "config": public_config(save_config(body))})
        elif u.path == "/api/capture":
            kw = sources.normalize_keyword(body.get("keyword") or "")
            jp = sources.normalize_keyword(body.get("jp_keyword") or "")
            if not kw:
                self._json({"ok": False, "reason": "empty keyword"}, 400)
                return
            store.log_query(kw, jp, body.get("note") or "capture")
            params = urllib.parse.urlencode({"keyword": kw, "jp": jp,
                                             "source_url": body.get("source_url") or ""})
            self._json({"ok": True, "keyword": kw, "panel": f"/?{params}"})
        elif u.path == "/api/vision":
            data_url = (body.get("image") or "").strip()
            if not data_url:
                self._json({"ok": False, "reason": "没有收到图片", "visual_sites": dicts.VISUAL_SITES}, 400)
                return
            try:
                rel, mime, raw = llm.save_image(data_url)
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "reason": str(exc)[:160],
                            "visual_sites": dicts.VISUAL_SITES}, 400)
                return
            name = rel.rsplit("/", 1)[-1]
            ai_on = cfg.get("use_llm", True) is not False
            do_analyze = body.get("analyze", True) is not False and ai_on
            if not ai_on:
                res = {"ok": False, "reason": "设置里已关闭 AI：图片只存在本机，可用下方各平台原生以图搜图"}
            elif do_analyze:
                res = llm.vision(raw, mime, cfg)
            else:
                res = {"ok": False, "reason": "已关闭模型识别，可直接用各平台原生以图搜图"}
            d = res.get("data", {}) if res.get("ok") else {}
            jpq = [str(x).strip() for x in (d.get("jp_queries") or []) if str(x).strip()]
            cn_name = str(d.get("cn_name") or "").strip()
            jp_name = str(d.get("jp_name") or "").strip() or (jpq[0] if jpq else "")
            built = (qe.build_candidates(cn_name or "商品", jp_name, jpq)
                     if (cn_name or jpq) else {"attrs": {}, "candidates": []})
            self._json({
                "ok": bool(res.get("ok")), "reason": res.get("reason", ""),
                "model": res.get("model", ""), "analysis": d,
                "image_url": "/api/image?name=" + urllib.parse.quote(name),
                "image_name": name, "cn_name": cn_name, "jp_name": jp_name,
                "candidates": built["candidates"], "attrs": built["attrs"],
                "visual_sites": dicts.VISUAL_SITES,
                "llm_enabled": ai_on,
                "vision_ready": llm.vision_available(cfg),
            })

        elif u.path == "/api/translate":
            text = (body.get("text") or "").strip()
            target = (body.get("target") or "ja").strip()
            if not text:
                self._json({"ok": False, "text": "", "mode": "none"}, 400)
                return
            if not llm.enabled(cfg):
                mapped = qe.rule_translate(text, target)
                note = ("设置里已关闭 AI：按品牌/品类词典改写，不发送任何模型请求"
                        if cfg.get("use_llm", True) is False else
                        "规则映射（品牌/品类词典）；填入模型接口后可获得完整机器翻译")
                self._json({"ok": True, "text": mapped, "mode": "rules",
                            "llm_enabled": cfg.get("use_llm", True) is not False, "note": note})
                return
            prompt = (f"把下面的商品名翻译成{'日文电商惯用搜索词' if target == 'ja' else '简洁中文商品名'}，"
                      f"只输出翻译结果，不要解释、不要加引号：\n{text}")
            try:
                out = llm._post({
                    "model": cfg.get("llm_model", "").strip() or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                }, cfg, timeout=40).strip().strip('"')
                self._json({"ok": True, "text": out, "mode": "llm"})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "text": "", "mode": "llm",
                            "note": f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]}"})

        elif u.path == "/api/watchlist":
            action = body.get("action") or "add"
            kw = qe.normalize(body.get("keyword") or "")
            if not kw:
                self._json({"ok": False, "reason": "empty keyword"}, 400)
                return
            if action == "remove":
                store.watch_remove(kw)
            else:
                try:
                    tp = float(body.get("target_price") or 0) or None
                except (TypeError, ValueError):
                    tp = None
                store.watch_add(kw, qe.normalize(body.get("jp_keyword") or ""),
                                tp, str(body.get("note") or "")[:200])
            self._json({"ok": True, "items": store.watch_list()})

        elif u.path == "/api/browser_search":
            if not cfg.get("enable_browser"):
                self._json({"ok": False, "reason": "Tier C 未启用，请到设置里打开", "items": [], "degraded": True})
                return
            plat = body.get("platform") or ""
            kw = body.get("keyword") or ""
            if plat == "xianyu":
                merged, reasons, urls = [], [], set()
                for q in dict.fromkeys([kw, f"{kw} 日版"]):
                    r = browser.search(
                        "xianyu", q,
                        headless=bool(cfg.get("browser_headless", False)),
                        proxy=cfg.get("proxy", ""),
                        min_interval=float(cfg.get("browser_min_interval", 5)),
                        max_per_hour=int(cfg.get("browser_max_per_hour", 20)))
                    for it in r.get("items", []):
                        key = qe.url_key(it.get("url"))
                        if key and key not in urls:
                            urls.add(key)
                            merged.append(it)
                    if r.get("degraded"):
                        reasons.append(r.get("reason", ""))
                prices = sorted(float(i["price"]) for i in merged if i.get("price"))
                med = prices[len(prices) // 2] if prices else 0.0
                for it in merged:
                    q = qe.assess(it.get("title", ""), it.get("price"), "CNY", med or None)
                    it["quality"] = q
                    it["quality_label"] = q["label"]
                    it["quality_score"] = q["score"]
                res = {"ok": bool(merged), "items": merged, "degraded": not merged,
                       "reason": reasons[0] if reasons and not merged else "",
                       "queries": [kw, f"{kw} 日版"], "cn_median": med}
            else:
                res = browser.search(plat, kw,
                                     headless=bool(cfg.get("browser_headless", False)),
                                     proxy=cfg.get("proxy", ""),
                                     min_interval=float(cfg.get("browser_min_interval", 5)),
                                     max_per_hour=int(cfg.get("browser_max_per_hour", 20)))
            if res.get("items"):
                store.save_prices(sources.normalize_keyword(kw), res["items"])
            self._json(res)
        else:
            self._json({"error": "not found"}, 404)


def main() -> None:
    store.init()
    cfg = load_config()
    port = int(cfg["port"])
    srv = ThreadingHTTPServer((cfg["host"], port), Handler)
    ip = lan_ip()
    print("=" * 62, flush=True)
    print("  日淘比价助手 · 本地服务已启动", flush=True)
    print(f"  电脑打开 : http://127.0.0.1:{port}", flush=True)
    print(f"  手机打开 : http://{ip}:{port}   (需与电脑同一 Wi-Fi)", flush=True)
    print("  密钥仅存于本机 app/config.json，接口不回传、不记录、不外发", flush=True)
    print("=" * 62, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
        srv.shutdown()


if __name__ == "__main__":
    main()
