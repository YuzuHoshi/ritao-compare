(() => {
  'use strict';
  const SERVER = 'http://127.0.0.1:8787';
  const SKIP = /购物车|首页|站点|我的|搜索|立即购买|加入|收藏|客服|登录|注册|运费|结算|确认|返回|分享|翻译|加载中|日淘市集|帮助|更多|筛选|排序/;
  const SOURCE_RE = /(https?:\/\/[^\s"'<>]*(?:jp\.mercari\.com|auctions\.yahoo\.co\.jp|suruga-ya\.jp|amazon\.co\.jp|rakuten\.co\.jp|52ritao\.cn|ritao\.hk)[^\s"'<>]*)/i;

  function pickTitle() {
    const sels = ['h1', '[class*="goods-name"]', '[class*="goodsName"]', '[class*="goods-title"]',
                  '[class*="product-title"]', '[class*="productTitle"]', '[class*="item-title"]',
                  '[class*="title"]', '[id*="title"]'];
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el) {
        const t = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
        if (t.length >= 6 && t.length <= 160 && !SKIP.test(t)) return t;
      }
    }
    // Fallback: longest plausible text node on the page.
    let best = '';
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      const t = (n.nodeValue || '').trim().replace(/\s+/g, ' ');
      if (t.length >= 8 && t.length <= 120 && !SKIP.test(t) && t.length > best.length) best = t;
    }
    return best;
  }

  function findSourceUrl() {
    const anchors = [...document.querySelectorAll('a[href]')].map((a) => a.href);
    for (const u of anchors) if (SOURCE_RE.test(u)) return u;
    const m = document.body.innerText.match(SOURCE_RE);
    return m ? m[1] : '';
  }

  function inject() {
    if (document.getElementById('rt-price-btn')) return;
    const btn = document.createElement('button');
    btn.id = 'rt-price-btn';
    btn.type = 'button';
    btn.textContent = '查价格';
    const hint = document.createElement('div');
    hint.id = 'rt-price-hint';
    btn.addEventListener('click', async () => {
      const kw = pickTitle();
      const src = findSourceUrl();
      const params = new URLSearchParams({ keyword: kw, source_url: src });
      // Log the capture; failure is harmless (the panel works from URL params alone).
      try {
        await fetch(SERVER + '/api/capture', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ keyword: kw, source_url: src, note: 'extension' }),
        });
      } catch (e) { /* service offline or mixed-content blocked */ }
      window.open(SERVER + '/?' + params.toString(), '_blank', 'noopener');
    });
    const refresh = () => {
      const kw = pickTitle();
      hint.textContent = kw ? `将按「${kw.slice(0, 40)}」查询；点开前可在面板里改关键词` : '没读到商品名，可在打开的面板里手动粘贴';
    };
    btn.addEventListener('mouseenter', refresh);
    document.body.appendChild(btn);
    document.body.appendChild(hint);
    // SPA route changes: keep the button, refresh the hint.
    let timer = null;
    new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => { if (!document.getElementById('rt-price-btn')) inject(); }, 600);
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.body) inject();
  else document.addEventListener('DOMContentLoaded', inject);
})();
