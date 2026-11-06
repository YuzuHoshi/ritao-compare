"""Tier C -- read prices from YOUR OWN logged-in browser, as safely as possible.

Architecture: ONE long-lived Chrome process with ONE profile, driven by a
single worker thread. All HTTP requests submit tasks to that worker through a
queue. Login and search therefore share cookies without ever launching a second
Chrome on the same user-data-dir (which makes Chrome exit with code 21).

Hard rules (these are what keep a personal account out of trouble):
  * opt-in: config flag `enable_browser` must be true
  * ONLY public GET navigations + reading already-rendered DOM text.
    No clicks, no form submits, no messaging, no signed API calls, no writes.
  * one page per query, no pagination, no concurrency
  * minimum interval with jitter + hourly cap
  * any captcha / rate-limit / login hint -> abort and degrade to Tier A

Honest caveat: marketplace ToS generally forbid automation. For personal,
low-frequency, read-only use the realistic worst case is a slider challenge or
a temporary rate limit, which we detect and back off from. No zero-risk promise.
"""
from __future__ import annotations

import os
import queue
import random
import threading
import time
import urllib.parse
from collections import deque
from pathlib import Path
from typing import Any

PROFILE = Path.home() / ".ritao-compare" / "browser-profile"
_LOCK = threading.Lock()
_CALLS: deque[float] = deque()

BLOCK_WORDS = ["请先登录", "登录后", "安全验证", "异常访问", "访问过于频繁",
               "滑动验证", "请滑动", "非法访问", "请使用正常浏览器",
               "captcha", "verify you are human"]

PROFILES = {
    "xianyu": {"home": "https://www.goofish.com/",
               "search": "https://www.goofish.com/search?q={q}",
               "selector": "a[href*='/item?id='], a[href*='item?id=']", "currency": "CNY"},
    "mercari": {"home": "https://jp.mercari.com/",
                "search": "https://jp.mercari.com/search?keyword={q}",
                "selector": "a[href*='/item/m']", "currency": "JPY"},
    "yahoo_closed": {"home": "https://auctions.yahoo.co.jp/",
                     "search": "https://auctions.yahoo.co.jp/closedsearch/closedsearch?p={q}&ei=utf-8",
                     "selector": "a[href*='/jp/auction/']", "currency": "JPY"},
}

EXTRACT_JS = r"""
({sel, platform}) => {
  const bad = ['请先登录','安全验证','异常访问','访问过于频繁','滑动验证','请滑动','非法访问','请使用正常浏览器','captcha','verify'];
  const body = (document.body && document.body.innerText || '').slice(0, 4000);
  if (bad.some(w => body.toLowerCase().includes(w.toLowerCase()))) return {blocked: true, items: []};
  const out = [];
  document.querySelectorAll(sel).forEach(a => {
    // Start at the anchor itself: on 闲鱼 the <a> wraps the whole card and
    // already contains its own price. Walking up first landed on the shared
    // grid container, so every card showed the first card's price.
    let box = a, txt = '';
    for (let i = 0; i < 3 && box; i++) {
      txt = box.innerText || '';
      if (/[¥￥]|[0-9]\s*円/.test(txt)) break;
      box = box.parentElement;
    }
    if (!box) box = a;
    let img = a.querySelector('img') || box.querySelector('img');
    let image = '';
    if (img) {
      image = img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('srcset') || '';
      if (image) image = image.split(' ')[0];
      if (image.startsWith('//')) image = 'https:' + image;
      if (image.startsWith('data:')) image = '';
    }
    const m = txt.match(/[¥￥]\s*([0-9][0-9,]*)/) || txt.match(/([0-9][0-9,]{1,8})\s*円/);
    const price = m ? Number(m[1].replace(/,/g, '')) : null;
    // Mercari wraps only the image in <a> and puts the price on the first
    // line of the card; 闲鱼 wraps the whole card and puts the title first.
    const isMercari = platform === 'mercari';
    let raw = isMercari
      ? ((box && box.innerText) || a.innerText || '').trim()
      : (a.innerText || (img && img.alt) || a.getAttribute('aria-label') || '').trim();
    if (!raw && box) raw = (box.innerText || '').trim();
    const status = ['売り切れ', 'SOLD', '販売中', 'いいね', '新着', '残り1点'];
    const lines = raw.split(/\n+/).map(x => x.trim())
      .filter(x => x && status.indexOf(x) < 0
        && !/^[¥￥]/.test(x)
        && !/^[0-9][0-9,]*$/.test(x)
        && !/^[0-9][0-9,]*\s*円$/.test(x));
    let title = '';
    if (isMercari) {
      title = lines.slice().sort((x, y) => y.length - x.length)[0] || '';
    } else {
      title = lines[0] || '';
      if (title.length < 4 && lines.length) {
        title = lines.slice().sort((x, y) => y.length - x.length)[0];
      }
    }
    title = title.replace(/\s+/g, ' ').slice(0, 150);
    if (title || price) out.push({title, url: a.href, price, image});
  });
  const seen = new Set(), uniq = [];
  for (const it of out) {
    // Keep ?id=...: 闲鱼 / 淘宝 item URLs differ only in that parameter.
    let k = it.url || it.title || '';
    try {
      const u = new URL(it.url, location.href);
      const id = u.searchParams.get('id') || u.searchParams.get('itemId');
      k = u.origin + u.pathname + (id ? '?id=' + id : '');
    } catch (e) { k = (it.url || it.title || '').split('?')[0]; }
    if (k && !seen.has(k)) { seen.add(k); uniq.push(it); }
  }
  return {blocked: false, items: uniq.slice(0, 60)};
}
"""

# ---------- single long-lived browser session ----------
_Q: queue.Queue = queue.Queue()
_WORKER: dict[str, Any] = {"thread": None, "cfg": None}


def chrome_exe() -> str | None:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    return next((p for p in candidates if p and os.path.exists(p)), None)


def available() -> tuple[bool, str]:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "未安装 Playwright（pip install playwright）"
    if not chrome_exe():
        return False, "未找到系统 Chrome / Edge"
    return True, ""


def _friendly(exc: Exception) -> str:
    """Never dump Playwright's multi-KB launch log into the UI."""
    s = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    low = (s + type(exc).__name__).lower()
    if "targetclosed" in low or "exitcode=21" in low or "launch_persistent_context" in low:
        return "浏览器会话异常退出（可能窗口被关闭）；已降级为「一键搜索」，下次查询会自动重连。"
    if "timeout" in low:
        return "页面加载超时，已跳过。"
    return f"{type(exc).__name__}: {s[:160]}"


CDP_PORT = 9333
_OWNED = {"value": True}


def _connect(pw):
    """Attach to the browser we already launched, if it is still alive."""
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    if browser.contexts:
        _OWNED["value"] = False
        return browser.contexts[0]
    return None


def _build_ctx(pw, cfg: dict[str, Any]):
    PROFILE.mkdir(parents=True, exist_ok=True)
    # Reuse a browser that is already running with our profile (e.g. the
    # server was restarted). This keeps the 闲鱼/煤炉 login alive AND avoids
    # the profile lock conflict that produced TargetClosedError: every launch
    # below exposes a fixed CDP port, so a surviving browser is always
    # reachable instead of blocking the next launch.
    try:
        ctx = _connect(pw)
        if ctx is not None:
            return ctx
    except Exception:
        pass

    def _launch():
        kwargs: dict[str, Any] = {
            "executable_path": chrome_exe(),
            "headless": bool(cfg.get("headless", False)),
            "viewport": {"width": 1366, "height": 900},
            "locale": "zh-CN",
            "args": ["--disable-blink-features=AutomationControlled",
                     f"--remote-debugging-port={CDP_PORT}"],
        }
        if cfg.get("proxy", "").strip():
            kwargs["proxy"] = {"server": cfg["proxy"].strip()}
        return pw.chromium.launch_persistent_context(str(PROFILE), **kwargs)

    try:
        ctx = _launch()
        _OWNED["value"] = True
    except Exception:
        # A browser may have started in the meantime; attach instead of
        # fighting over the profile directory.
        time.sleep(0.6)
        ctx = _connect(pw)
        if ctx is None:
            raise
        return ctx
    try:
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    except Exception:
        pass
    return ctx


def _do_search(ctx, payload: dict[str, Any]) -> dict[str, Any]:
    platform, keyword = payload["platform"], payload["keyword"]
    if platform not in PROFILES:
        return {"ok": False, "reason": "unknown platform", "items": [], "degraded": True}
    conf = PROFILES[platform]
    url = conf["search"].format(q=urllib.parse.quote(keyword))
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        # Wait for the listing links to actually render. This happens INSIDE
        # the already-loaded page -- it does not issue another navigation, so
        # it does not raise the request rate against the marketplace.
        try:
            page.wait_for_selector(conf["selector"], timeout=10000, state="attached")
        except Exception:
            pass
        # 闲鱼's grid mounts late; scrolling a skeleton leaves only one card in
        # the virtualised list. Give it time to render first.
        page.wait_for_timeout(3600 if platform == "xianyu" else random.randint(900, 1700))
        # Mercari / 闲鱼 lazy-load the result grid. Scroll inside the SAME
        # page to load more cards -- no extra navigation, so no extra request
        # pressure. 闲鱼 needs more because its first screen is mostly ads.
        for _ in range(4 if platform == "xianyu" else 3):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(600)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(1200 if platform == "xianyu" else 350)
        data = page.evaluate(EXTRACT_JS, {"sel": conf["selector"], "platform": platform})
        # Virtualised grids can unmount cards during scrolling. If very few
        # survived, wait and read once more (same page, no new request).
        if len(data.get("items") or []) < 5 and not data.get("blocked"):
            page.wait_for_timeout(1400)
            again = page.evaluate(EXTRACT_JS, {"sel": conf["selector"], "platform": platform})
            if len(again.get("items") or []) > len(data.get("items") or []):
                data = again
    finally:
        try:
            page.close()
        except Exception:
            pass
    if data.get("blocked"):
        return {"ok": False, "reason": "检测到登录 / 风控提示，已停止并降级为「一键搜索」",
                "items": [], "degraded": True}
    items = [{"platform": platform, "title": it.get("title") or "",
              "price": it.get("price"), "currency": conf["currency"],
              "url": it.get("url") or "", "image": it.get("image") or "",
              "source": "own_browser"}
             for it in data.get("items", [])]
    if not items:
        return {"ok": True, "items": [], "url": url, "degraded": True,
                "reason": "该站未返回可见商品（可能需要先扫码登录，或平台限制了自动访问）；已降级为「一键搜索」。"}
    return {"ok": True, "items": items, "url": url, "degraded": False, "reason": ""}


def _do_login(ctx, payload: dict[str, Any]) -> dict[str, Any]:
    platform = payload["platform"]
    if platform not in PROFILES:
        return {"ok": False, "reason": "unknown platform"}
    # Open a tab and return immediately. The tab stays in the shared context,
    # so the user's QR login lands in exactly the profile used by search.
    page = ctx.new_page()
    try:
        page.goto(PROFILES[platform]["home"], wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        return {"ok": False, "reason": _friendly(exc)}
    return {"ok": True, "reason": "已打开登录标签页，请扫码；扫完直接关掉该标签页即可，Cookie 与查询共用。"}


def _worker_main(cfg: dict[str, Any]) -> None:
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    ctx = None
    current = dict(cfg)
    while True:
        task = _Q.get()
        kind = task["kind"]
        if kind == "stop":
            break
        if kind == "reconfig":
            current = dict(task["cfg"])
            try:
                if ctx and _OWNED["value"]:
                    ctx.close()
            except Exception:
                pass
            ctx = None
            task["box"]["res"] = {"ok": True}
            task["box"]["done"].set()
            continue
        try:
            if ctx is None:
                ctx = _build_ctx(pw, current)
        except Exception as exc:
            task["box"]["res"] = {"ok": False, "reason": _friendly(exc), "items": [], "degraded": True}
            task["box"]["done"].set()
            continue
        try:
            res = _do_search(ctx, task["payload"]) if kind == "search" else _do_login(ctx, task["payload"])
        except Exception as exc:
            # Context died (user closed the browser). Rebuild once and retry.
            try:
                ctx.close()
            except Exception:
                pass
            ctx = None
            try:
                ctx = _build_ctx(pw, current)
                res = _do_search(ctx, task["payload"]) if kind == "search" else _do_login(ctx, task["payload"])
            except Exception as exc2:
                res = {"ok": False, "reason": _friendly(exc2), "items": [], "degraded": True}
        task["box"]["res"] = res
        task["box"]["done"].set()
    try:
        if ctx:
            ctx.close()
    except Exception:
        pass
    pw.stop()


def _ensure_worker(cfg: dict[str, Any]) -> None:
    with _LOCK:
        want = {"headless": bool(cfg.get("headless", False)), "proxy": cfg.get("proxy", "")}
        t = _WORKER["thread"]
        if t is not None and t.is_alive():
            if _WORKER["cfg"] != want:
                box = {"done": threading.Event(), "res": None}
                _Q.put({"kind": "reconfig", "cfg": want, "box": box})
                box["done"].wait(timeout=20)
                _WORKER["cfg"] = want
            return
        _WORKER["cfg"] = want
        _WORKER["thread"] = threading.Thread(
            target=_worker_main, args=(want,), daemon=True, name="ritao-browser")
        _WORKER["thread"].start()


def _submit(kind: str, payload: dict[str, Any], cfg: dict[str, Any], timeout: int = 70) -> dict[str, Any]:
    _ensure_worker(cfg)
    box = {"done": threading.Event(), "res": None}
    _Q.put({"kind": kind, "payload": payload, "box": box})
    if not box["done"].wait(timeout):
        return {"ok": False, "reason": "浏览器会话忙，已跳过（下次查询自动重连）", "items": [], "degraded": True}
    return box["res"] or {"ok": False, "reason": "无响应", "items": [], "degraded": True}


def _allow_call(min_interval: float, max_per_hour: int) -> tuple[bool, float]:
    now = time.time()
    while _CALLS and now - _CALLS[0] > 3600:
        _CALLS.popleft()
    if len(_CALLS) >= max_per_hour:
        return False, max(1.0, 3600 - (now - _CALLS[0]))
    if _CALLS:
        wait = min_interval + random.uniform(0, 2.0) - (now - _CALLS[-1])
        if wait > 0:
            return False, wait
    return True, 0.0


def _pace(min_interval: float, max_per_hour: int) -> str | None:
    """Returns an error reason if the query must be skipped."""
    for _ in range(3):
        allowed, wait = _allow_call(min_interval, max_per_hour)
        if allowed:
            _CALLS.append(time.time())
            return None
        if wait > 60:
            return f"已达到每小时 {max_per_hour} 次上限，{int(wait / 60)} 分钟后再试"
        time.sleep(wait)
    return "浏览器节流中，已跳过本次"


def open_login(platform: str, proxy: str = "", headless: bool = False) -> dict[str, Any]:
    ok, msg = available()
    if not ok:
        return {"ok": False, "reason": msg}
    return _submit("login", {"platform": platform},
                   {"proxy": proxy, "headless": headless}, timeout=60)


def search(platform: str, keyword: str, headless: bool = False, proxy: str = "",
           min_interval: float = 5.0, max_per_hour: int = 20) -> dict[str, Any]:
    ok, msg = available()
    if not ok:
        return {"ok": False, "reason": msg, "items": [], "degraded": True}
    reason = _pace(min_interval, max_per_hour)
    if reason:
        return {"ok": False, "reason": reason, "items": [], "degraded": True}
    return _submit("search", {"platform": platform, "keyword": keyword},
                   {"proxy": proxy, "headless": headless})


def safety_info(cfg: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    while _CALLS and now - _CALLS[0] > 3600:
        _CALLS.popleft()
    alive = bool(_WORKER["thread"] and _WORKER["thread"].is_alive())
    return {
        "calls_last_hour": len(_CALLS),
        "max_per_hour": cfg.get("browser_max_per_hour", 20),
        "min_interval_seconds": cfg.get("browser_min_interval", 5),
        "headless": cfg.get("browser_headless", False),
        "chrome": chrome_exe() or "",
        "session_running": alive,
        "guarantees": ["单一常驻浏览器会话，登录与查询共用 Cookie",
                       "只读页面已渲染文字", "无点击 / 无提交 / 无消息发送",
                       "单页无分页、无并发、带节流与抖动", "遇风控立即停止并降级"],
        "no_guarantee": "平台条款通常禁止自动化；无法承诺绝对零风险，但个人低频只读的最坏情况一般是滑块或临时限流。",
    }
