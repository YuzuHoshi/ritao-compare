(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = {
    cfg: {}, data: null, links: [], visualSites: [], watch: [],
    selected: new Set(), activeCandidate: '', image: '', view: 'price',
    soubaCategory: 'auto', onlyRelevant: true, sort: 'match', platform: 'all', layout: 'grid',
    lastQueryAt: 0, tokens: { cn: [], jp: [] }, dropped: new Set(), plan: null, searchKey: '',
    cnQuery: '', planCnDirty: false, cnManualKey: '',
    onlyGenuine: true, videos: null, videoLoading: false, planVideo: false, deepMode: false,
    media: null, mediaLoading: false,
    planConfirm: true, compare: { ship: 1800, fee: 8, tariff: 0 }
  };
  const DEFAULT_SELECTED = ['xianyu', 'mercari', 'yahoo_auction', 'surugaya', 'yahoo_closed', 'rakuten'];
  const VIEWS = {
    price: ['比价工作台', '同款在售、近 120 天落札成交、国内参考价，一次看清。'],
    souba: ['落札相場', '雅虎日拍官方公开页，免登录 · 默认近 120 天 · 可按分类过滤。'],
    compare: ['中日比价', '日本到手价（含运费/服务费/关税）与国内闲鱼价格放在一起看。'],
    sites: ['站点直达', '只拼接搜索链接，不抓取任何页面 · 零封禁风险。'],
    figures: ['手办资料', '发售日、定价、厂商、原型师，以及当年发售的官网入口。'],
    media: ['发售资料', '游戏 / CD / 动画 BD / 轻小说：发售日、厂商、平台、卷数与官网入口。'],
    videos: ['开箱视频', 'B 站 / niconico / YouTube 的实物开箱与评测，买前先看真东西。'],
    vision: ['识图检索', '图片 → 结构化关键词 → 复用文字检索链路。'],
    track: ['追踪清单', '每次检索自动沉淀到本机 SQLite，越用越有自己的价格曲线。']
  };

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n) => (n == null || n === '' || Number.isNaN(Number(n))) ? '—' : Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
  const median = (a) => { const v = a.filter((x) => x != null); if (!v.length) return null; const s = [...v].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const rate = () => state.cfg.jpy_cny || 0.048;
  const toCny = (p, cur) => (p == null ? null : (cur === 'CNY' ? p : p * rate()));
  // Direct CDN first (fast, no server round-trip). If the CDN rejects the
  // hotlink (Hpoi) or the JP network is unreachable, fall back to the local
  // cache proxy, which supplies the correct Referer / proxy route.
  const imgTag = (u) => {
    if (!u) return '';
    if (/^(data:|blob:)/.test(u)) return `<img src="${esc(u)}" alt="" loading="lazy" decoding="async">`;
    // Three-step fallback: direct CDN -> local proxy (adds Referer / proxy
    // route) -> the original small thumbnail (some shops reject _ex=500x500).
    const small = u.replace('_ex=500x500', '_ex=128x128')
      .replace(/([?&])w=600\b/, '$1w=300').replace(/([?&])h=600\b/, '$1h=300');
    const fb2 = small !== u ? ` data-fb2="${esc(small)}"` : '';
    return `<img src="${esc(u)}" data-fb="${esc('/api/img?u=' + encodeURIComponent(u))}"${fb2} alt="" loading="lazy" decoding="async" onerror="if(!this.dataset.f){this.dataset.f=1;this.src=this.dataset.fb;}else if(this.dataset.fb2&&!this.dataset.g){this.dataset.g=1;this.src=this.dataset.fb2;}else{this.parentNode.innerHTML='<span class=noimg>无图</span>';}">`;
  };
  const clip = (s, n = 120) => String(s || '').replace(/\s+/g, ' ').slice(0, n);

  // ---------------------------------------------------------------- tokens
  let tokTimer, planTimer;
  function scheduleTokens() {
    clearTimeout(tokTimer);
    tokTimer = setTimeout(loadTokens, 380);
  }
  async function loadTokens() {
    const kw = $('keyword').value.trim(), jp = $('jpKeyword').value.trim();
    // A new query starts with a clean slate: exclusions from the previous
    // product must not silently apply to a different one.
    const key = kw + '|' + jp;
    if (state.searchKey && key !== state.searchKey) { state.dropped.clear(); state.searchKey = ''; }
    if (!kw && !jp) {
      state.tokens = { cn: [], jp: [] };
      renderTokens();
      return;
    }
    try {
      const r = await api('/api/tokens?' + new URLSearchParams({ keyword: kw, jp }));
      state.tokens = { cn: r.cn || [], jp: r.jp || [] };
      const present = new Set([...state.tokens.cn, ...state.tokens.jp].map((t) => t.text));
      for (const d of [...state.dropped]) if (!present.has(d)) state.dropped.delete(d);
      renderTokens();
      if (!$('planModal').hidden) schedulePlan();
    } catch (e) { /* offline */ }
  }
  function tokenChip(t) {
    const interactive = t.keep !== false;
    const off = state.dropped.has(t.text);
    const cls = [t.type || 'word', interactive ? '' : 'noise', off ? 'off' : ''].filter(Boolean).join(' ');
    const title = interactive ? '点击排除这个分词' : '系统自动忽略的噪声词';
    return `<button class="tok ${cls}" type="button" data-tok="${esc(t.text)}" title="${title}" ${interactive ? '' : 'disabled'}>${esc(t.text)}<b>${esc(t.label || '')}</b></button>`;
  }
  function renderTokens() {
    const cn = state.tokens.cn || [], jp = state.tokens.jp || [];
    // The strip stays quiet: only words the engine will actually use. Noise
    // (condition, scale, shop codes) is visible in the plan modal for audit.
    const cnShow = cn.filter((t) => t.keep !== false);
    const jpShow = jp.filter((t) => t.keep !== false)
      .filter((t) => !cnShow.some((c) => c.text === t.text));
    const allShow = [...cnShow, ...jpShow];
    $('tokStrip').hidden = !allShow.length;
    if (allShow.length) {
      $('tokStripList').innerHTML = cnShow.map(tokenChip).join('') +
        (cnShow.length && jpShow.length ? '<span class="tok-div">日</span>' : '') +
        jpShow.map(tokenChip).join('');
    }
    if ($('planTokCn')) $('planTokCn').innerHTML = cn.length ? cn.map(tokenChip).join('') : '<span class="hint">没有中文分词</span>';
    if ($('planTokJp')) $('planTokJp').innerHTML = jp.length ? jp.map(tokenChip).join('') : '<span class="hint">没有日文分词</span>';
  }
  const hhmm = (ts) => { const d = new Date(ts * 1000); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
  const ymd = (ts) => { if (!ts) return ''; const d = new Date(ts * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }
  let toastTimer;
  function toast(msg) {
    const el = $('toast'); el.textContent = msg; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 3800);
  }
  // Re-render result cards after the listing price changes, debounced so typing
  // stays smooth. This never issues a request.
  let resultTimer;
  function scheduleResultRefresh() {
    clearTimeout(resultTimer);
    resultTimer = setTimeout(() => { if (state.data) renderResults(); }, 220);
  }

  // ---------------------------------------------------------------- init
  async function init() {
    const p = new URLSearchParams(location.search);
    $('keyword').value = p.get('keyword') || '';
    $('jpKeyword').value = p.get('jp') || '';
    state.planConfirm = localStorage.getItem('ritao-plan-confirm') !== '0';
    state.compare.ship = Number(localStorage.getItem('ritao-ship') || 1800);
    state.compare.fee = Number(localStorage.getItem('ritao-fee') || 8);
    state.compare.tariff = Number(localStorage.getItem('ritao-tariff') || 0);
    $('cfgPlanConfirm').checked = state.planConfirm;
    $('cnShip').value = state.compare.ship;
    $('cnFee').value = state.compare.fee;
    $('cnTariff').value = state.compare.tariff;
    $('cnManualPrice').value = localStorage.getItem('ritao-cn-price') || '';
    state.onlyGenuine = localStorage.getItem('ritao-only-genuine') !== '0';
    const deepPref = localStorage.getItem('ritao-deep');
    state.deepMode = deepPref === '1';
    state.planVideo = localStorage.getItem('ritao-plan-video') === '1';
    $('onlyGenuine').checked = state.onlyGenuine;
    $('planVideo').checked = state.planVideo;
    buildMobileTabs();
    bindNav();
    bindEvents();

    try {
      const h = await api('/api/health');
      const bad = h.proxy && h.proxy_alive === false;
      $('statusDot').className = bad ? 'err' : 'ok';
      $('statusText').textContent = bad ? '代理离线' : '本机服务在线';
      $('railNote').innerHTML = bad
        ? 'Clash 未监听，日本站暂不可用'
        : `手机访问 ${h.lan_ip}:${h.port}`;
      $('browserNote').textContent = h.playwright ? `已就绪 · ${(h.chrome || '').split('\\').pop()}` : h.playwright_note;
    } catch (e) {
      $('statusDot').className = 'err'; $('statusText').textContent = '服务未连接';
      toast('本地服务没连上，请先运行 start.bat');
    }
    try {
      state.cfg = await api('/api/config'); applyConfig();
      // First run: follow the Tier C setting instead of silently skipping
      // 闲鱼 / 煤炉. The choice is remembered afterwards.
      if (deepPref === null) state.deepMode = !!state.cfg.enable_browser;
    } catch (e) { /* offline */ }
    try { await loadSafety(); } catch (e) { /* ignore */ }
    try { const r = await api('/api/recent'); renderRecent(r.recent || []); } catch (e) { /* ignore */ }
    try { const v = await api('/api/visual'); state.visualSites = v.sites || []; renderVisualSites(); } catch (e) { /* ignore */ }
    try { const w = await api('/api/watchlist'); state.watch = w.items || []; renderWatch(); } catch (e) { /* ignore */ }

    renderKPI(); renderSide(); renderResults(); renderSouba(); renderTrack(); renderVisionResult(null);
    renderComparePage(); renderVideos(); renderMedia();
    if ($('keyword').value.trim() || $('jpKeyword').value.trim()) loadTokens();
    // NOTE: deliberately no auto-search. A URL keyword is only prefilled.
    if ($('keyword').value.trim()) toast('已载入关键词，点「查价格」开始');
  }

  function applyConfig() {
    const c = state.cfg;
    $('ritaoPrice').value = c.ritao_price_jpy ?? '';
    $('cfgRate').value = (c.jpy_cny * 100).toFixed(2);
    $('cfgProxy').value = c.proxy || '';
    $('cfgRakuten').value = ''; $('cfgRakutenKey').value = '';
    $('cfgLlmBase').value = c.llm_base_url || '';
    $('cfgLlmKey').value = '';
    $('cfgLlmModel').value = c.llm_model || '';
    $('cfgVisionModel').value = c.llm_vision_model || '';
    $('cfgUseLlm').checked = c.llm_enabled !== false;
    applyLlmState();
    $('cfgBrowser').checked = !!c.enable_browser;
    $('cfgHeadless').checked = !!c.browser_headless;
    $('cfgInterval').value = c.browser_min_interval;
    $('cfgMaxHour').value = c.browser_max_per_hour;
    $('cfgTheme').value = c.theme === 'dark' ? 'dark' : 'light';
    setTheme(c.theme);
    $('keyState').textContent = `楽天 ID ${c.has_rakuten_app_id ? '已配置 ✓' : '未配置'} · Access Key ${c.has_rakuten_access_key ? '已配置 ✓' : '未配置'} · AI ${c.llm_ready ? '已配置 ✓' : (c.llm_configured ? '已关闭' : '未配置（用规则词典）')}。输入框留空表示不修改。`;
    renderCfgBrowserPlatforms(); renderCfgPlatforms();
  }

  // Master AI switch. Off means the whole app runs on the local rule engine:
  // no rewrite call, no vision call, no translate call, zero tokens.
  function applyLlmState() {
    const on = $('cfgUseLlm').checked;
    const hasKey = !!(state.cfg && state.cfg.llm_configured);
    $('llmFields').classList.toggle('off', !on);
    $('llmState').textContent = on
      ? (hasKey ? '当前：已开启 — 检索时把中文标题发给上面这个接口，换回日文检索词。'
                : '当前：已开启，但还没填接口 — 实际仍走本地规则，不会产生费用。')
      : '当前：已关闭 — 检索 / 识图 / 翻译全部走本地规则，不发送任何模型请求，也不产生任何费用。';
    $('llmHint').textContent = on
      ? '开启后：每次检索多一次改写请求（约几百 token），识图会把图片发给上面这个接口；觉得费钱随时来这里关掉，关掉后一切照常可用。'
      : '关闭后：检索词完全由内置规则生成（100+ 作品 / 角色 / 品牌映射 + 噪声清洗 + 同款判定），检索仍以搜索框内容为准，只是不再请求任何模型；凭据可以留着，不会发出一个请求。';
    const um = $('useModel');
    if (um) { um.checked = on && hasKey; um.disabled = !on; }
  }

  function setTheme(t) {
    const mode = t === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = mode;
    document.querySelector('meta[name=theme-color]').setAttribute('content', mode === 'dark' ? '#15120E' : '#F3EEE4');
    localStorage.setItem('ritao-theme', mode);
  }

  function buildMobileTabs() {
    const items = [...document.querySelectorAll('#navMain .nav-item')];
    $('mobileTabs').innerHTML = items.map((b) => `<button data-view="${b.dataset.view}" type="button" class="${b.classList.contains('active') ? 'active' : ''}">${b.querySelector('svg').outerHTML}<span>${b.querySelector('span').textContent.replace('工作台', '').replace('清单', '')}</span></button>`).join('');
  }

  function switchView(name) {
    if (!VIEWS[name]) return;
    state.view = name;
    document.querySelectorAll('#navMain .nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
    document.querySelectorAll('#mobileTabs button').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
    document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.dataset.view === name));
    document.querySelector('.stage').scrollTop = 0;
    window.scrollTo({ top: 0, behavior: 'smooth' });
    if (name === 'compare') renderComparePage();
    if (name === 'sites') ensureLinks();
  }

  function bindNav() {
    document.addEventListener('click', (e) => {
      const b = e.target.closest('[data-view]');
      if (b) switchView(b.dataset.view);
    });
  }

  // ---------------------------------------------------------------- search
  function startSearchFlow() {
    const kw = $('keyword').value.trim();
    if (!kw) { toast('先填商品名，或点「贴」从剪贴板读取'); $('keyword').focus(); return; }
    if (state.planConfirm) openPlan();
    else executeSearch();
  }

  async function executeSearch(customQueries, cnQuery) {
    const kw = $('keyword').value.trim();
    if (!kw) { toast('先填商品名，或点「贴」从剪贴板读取'); $('keyword').focus(); return; }
    const jp = $('jpKeyword').value.trim();
    const cn = (cnQuery || '').trim();
    const btn = $('btnSearch');
    btn.disabled = true; btn.querySelector('span').textContent = '检索中';
    $('resultsList').innerHTML = `<div class="blank"><div class="blank-mark"><svg viewBox="0 0 48 48"><circle cx="21" cy="21" r="12"/><path d="M30 30l9 9"/></svg></div><h3>正在检索…</h3><p>生成候选词 · 请求楽天 API · 读取雅虎落札相場 · 匹配重排</p></div>`;
    $('resultCount').textContent = '—';
    $('resultMeta').textContent = '检索中…';
    const params = {
      keyword: kw, jp,
      deep: (state.deepMode || $('deepToggle').checked) ? '1' : '0',
      souba_category: state.soubaCategory || 'auto'
    };
    if (state.dropped.size) params.drop = JSON.stringify([...state.dropped]);
    if (customQueries && customQueries.length) params.queries = JSON.stringify(customQueries);
    if (cn) params.cn = cn;
    try {
      const d = await api('/api/search?' + new URLSearchParams(params));
      state.data = d; state.links = d.links || [];
      state.cnQuery = d.cn_query || cn || '';
      state.activeCandidate = (d.candidates && d.candidates[0] && d.candidates[0].id) || '';
      state.selected = new Set(state.links.filter((l) => DEFAULT_SELECTED.includes(l.id)).map((l) => l.id));
      state.platform = 'all'; state.lastQueryAt = Date.now() / 1000;
      state.media = null;
      state.searchKey = kw + '|' + jp;
      closePlan();
      renderAll();
      if (state.planVideo) loadVideos(false);
      history.replaceState(null, '', '/?' + new URLSearchParams({ keyword: kw, jp }));
      if (d.llm_error) toast('AI 改写未生效，已用规则词典：' + clip(d.llm_error, 70));
    } catch (e) {
      toast('检索失败：' + e.message);
      renderResults();
    }
    btn.disabled = false; btn.querySelector('span').textContent = '查价格';
  }

  function renderAll() {
    renderKPI(); renderSide(); renderResults(); renderSouba(); renderSites();
    renderFigures(); renderTrack(); renderVisualSites(); renderComparePage();
    $('emptyHero').hidden = !!(state.data && (state.data.items || []).length);
    $('navCountSouba').textContent = (state.data && state.data.souba && state.data.souba.items || []).length || '';
    try { api('/api/recent').then((r) => renderRecent(r.recent || [])); } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------- derived
  function itemsAll() { return (state.data && state.data.items) || []; }

  // 闲鱼 listings that are bootleg copies or a single part (faceplate, base)
  // are not a price reference. The server tags them; the UI hides by default.
  const badQuality = (i) => i.quality
    && (i.quality.bootleg || i.quality.parts || i.quality.suspect);

  function itemsShown() {
    // Hard-dropped items (score 0: different character / bundle / wrong
    // platform) are never shown, even with the filter off. "Show all" means
    // all plausible matches, not the junk that caused the original complaint.
    let arr = itemsAll().filter((i) => (i.match || 0) > 0);
    if (state.onlyRelevant) arr = arr.filter(relevant);
    if (state.onlyGenuine) arr = arr.filter((i) => !badQuality(i));
    if (state.platform !== 'all') arr = arr.filter((i) => (i.platform || '其他') === state.platform);
    const val = (i) => (i.currency === 'CNY' ? i.price : (i.price == null ? null : i.price * rate()));
    if (state.sort === 'price_asc') arr = [...arr].sort((a, b) => (val(a) ?? 1e12) - (val(b) ?? 1e12));
    else if (state.sort === 'price_desc') arr = [...arr].sort((a, b) => (val(b) ?? -1) - (val(a) ?? -1));
    else arr = [...arr].sort((a, b) => (b.match || 0) - (a.match || 0));
    return arr;
  }

  // When a variant (detail) was recognised, "related" means the same variant,
  // not just the same character. Fall back to the character floor when no
  // exact variant exists, so the list can never go empty.
  function relevantFloor() {
    const a = (state.data && state.data.attrs) || {};
    if (!a.detail) return 42;
    return itemsAll().some((i) => (i.match || 0) >= 85) ? 85 : 42;
  }
  const relevant = (i) => (i.match || 0) >= relevantFloor();
  // The KPI / comparison must describe THIS product, not every figure of the
  // same character. Take the highest-scoring cohort (within 8 points of the
  // best match) so a ¥550 prize figure cannot drag the median down.
  function focusSet() {
    const rel = itemsAll().filter(relevant).filter((i) => !badQuality(i));
    if (!rel.length) return [];
    const top = Math.max(...rel.map((i) => i.match || 0));
    const cohort = rel.filter((i) => (i.match || 0) >= top - 8);
    return cohort.length >= 2 ? cohort : rel;
  }
  function jpItems() { return focusSet().filter((i) => i.currency !== 'CNY' && i.price > 0); }
  function cnItems() { return focusSet().filter((i) => i.currency === 'CNY' && i.price > 0); }
  function soubaMedian() {
    const items = (state.data && state.data.souba && state.data.souba.items) || [];
    const m = median(items.map((i) => i.price));
    return m != null ? m : (state.data && state.data.souba && state.data.souba.stats && state.data.souba.stats['平均価格']) || null;
  }

  // ---------------------------------------------------------------- KPI
  function renderKPI() {
    const d = state.data;
    const jpMed = median(jpItems().map((i) => i.price));
    const sbMed = soubaMedian();
    const ritao = Number($('ritaoPrice').value || 0);
    const ritaoCny = ritao * rate();
    const refJp = jpMed != null ? jpMed * rate() : null;
    const refSb = sbMed != null ? sbMed * rate() : null;
    const ref = [refJp, refSb].filter((x) => x != null);
    const best = ref.length ? Math.min(...ref) : null;
    // Empty state stays quiet: no wall of dashes before the first search.
    const hasSomething = !!d || ritao > 0;
    $('kpiRow').hidden = !hasSomething;
    $('workSide').hidden = !d;
    $('resultsToolbar').hidden = !d;
    $('workGrid').classList.toggle('solo', !d);
    if (!hasSomething) return;
    let verdict = '—', vNote = '填入日淘标价后给出结论', vClass = '';
    if (ritao > 0 && best != null) {
      const good = ritaoCny <= best;
      verdict = good ? '可以入手' : '再等等';
      vClass = good ? 'verdict-good' : 'verdict-bad';
      vNote = `比参考价 ${good ? '低' : '高'} ¥${fmt(Math.abs(best - ritaoCny))}`;
    } else if (ritao > 0) {
      verdict = '待报价'; vNote = '暂无可比价格';
    }
    const plats = [...new Set(jpItems().map((i) => i.platform))].filter(Boolean).slice(0, 2).join(' / ') || '在售平台';
    $('kpiRow').innerHTML = `
      <div class="kpi">
        <div class="kpi-label">日淘市集标价</div>
        <div class="kpi-value"><span class="kpi-cur">¥</span><input class="kpi-input" id="kpiRitao" type="number" min="0" step="1" value="${ritao || ''}" placeholder="点击输入"></div>
        <div class="kpi-note">${ritao > 0 ? `≈ ¥${fmt(ritaoCny)} · 点击可改` : '日元标价，填入后给出购买结论'}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">日本在售中位</div>
        <div class="kpi-value">${jpMed == null ? '—' : fmt(jpMed)}<small>${jpMed == null ? '' : '円 · ≈¥' + fmt(refJp)}</small></div>
        <div class="kpi-note">${jpMed == null ? '暂无在售报价' : `${jpItems().length} 条同款 · ${esc(plats)}`}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">落札成交中位</div>
        <div class="kpi-value">${sbMed == null ? '—' : fmt(sbMed)}<small>${sbMed == null ? '' : '円 · ≈¥' + fmt(refSb)}</small></div>
        <div class="kpi-note">${sbMed == null ? '暂无落札数据' : '雅虎日拍 · 近 120 天'}</div>
      </div>
      <div class="kpi ${vClass}">
        <div class="kpi-label">购买结论</div>
        <div class="kpi-value">${esc(verdict)}</div>
        <div class="kpi-note">${esc(vNote)}</div>
      </div>`;
  }

  // Recompute only the verdict cell while the user types a listing price, so
  // the input keeps focus and the caret never jumps.
  function updateVerdict() {
    const cell = $('kpiRow').children[3];
    if (!cell) return;
    const ritao = Number($('ritaoPrice').value || 0);
    const jpMed = median(jpItems().map((i) => i.price));
    const sbMed = soubaMedian();
    const refs = [jpMed, sbMed].filter((x) => x != null).map((v) => v * rate());
    const best = refs.length ? Math.min(...refs) : null;
    let verdict = '—', note = '填入日淘标价后给出结论', cls = '';
    if (ritao > 0 && best != null) {
      const good = ritao * rate() <= best;
      verdict = good ? '可以入手' : '再等等';
      cls = good ? 'verdict-good' : 'verdict-bad';
      note = `比参考价 ${good ? '低' : '高'} ¥${fmt(Math.abs(best - ritao * rate()))}`;
    } else if (ritao > 0) { verdict = '待报价'; note = '暂无可比价格'; }
    cell.className = 'kpi ' + cls;
    cell.querySelector('.kpi-value').textContent = verdict;
    cell.querySelector('.kpi-note').textContent = note;
    const first = $('kpiRow').children[0];
    if (first) first.querySelector('.kpi-note').textContent = ritao
      ? `≈ ¥${fmt(ritao * rate())} · 点击可改` : '点击直接输入日元标价';
  }

  // ---------------------------------------------------------------- side
  function renderSide() {
    renderCompare(); renderSoubaMini(); renderQuery();
  }

  function renderCompare() {
    const ritao = Number($('ritaoPrice').value || 0);
    const jpMed = median(jpItems().map((i) => i.price));
    const cnMed = median(cnItems().map((i) => i.price));
    const sbMed = soubaMedian();
    const rows = [
      ['日淘标价', ritao ? ritao * rate() : null, 'accent'],
      ['日本同款', jpMed != null ? jpMed * rate() : null, 'gold'],
      ['落札中位', sbMed != null ? sbMed * rate() : null, 'good'],
      ['国内参考', cnMed, 'gold']
    ].filter(([, v]) => v);
    if (!rows.length) {
      $('compareCard').innerHTML = `<div class="card-head"><h3>价格对比</h3><span class="hint">折合人民币</span></div><div class="compare-empty">填入日淘标价并检索后，这里会给出结论。</div>`;
      return;
    }
    const max = Math.max(...rows.map(([, v]) => v));
    const bars = rows.map(([n, v, c]) => `
      <div class="cmp-row"><div class="cmp-name">${esc(n)}</div>
      <div class="cmp-track"><div class="cmp-fill ${c}" style="width:${Math.max(4, v / max * 100)}%"></div></div>
      <div class="cmp-val">¥${fmt(v)}</div></div>`).join('');
    let verdict = '', vClass = '', delta = '';
    if (ritao) {
      const best = Math.min(...rows.filter(([n]) => n !== '日淘标价').map(([, v]) => v));
      if (Number.isFinite(best)) {
        const good = ritao * rate() <= best;
        vClass = good ? 'good' : 'bad';
        verdict = good ? '日淘更划算' : '别处更便宜';
        delta = `${good ? '低' : '高'} ¥${fmt(Math.abs(best - ritao * rate()))}`;
      }
    }
    $('compareCard').innerHTML = `<div class="card-head"><h3>价格对比</h3><span class="hint">折合人民币</span></div>
      <div class="cmp-list">${bars}</div>
      ${verdict ? `<div class="verdict ${vClass}"><div class="verdict-mark">${vClass === 'good' ? '✓' : '!'}</div><div><b>${verdict}</b><small>与最低参考价相比</small></div><span class="delta">${delta}</span></div>` : ''}
      <p class="cmp-foot">均为商品标价，未含国际运费、服务费与关税。</p>`;
  }

  function renderSoubaMini() {
    const s = (state.data && state.data.souba) || {};
    const t = s.stats || {};
    if (!s.ok || !Object.keys(t).length) {
      $('soubaMini').innerHTML = `<div class="card-head"><h3>落札相場</h3><span class="hint">近 120 天</span></div><div class="compare-empty">检索后显示雅虎官方成交统计。</div>`;
      return;
    }
    const row = (label, val, hl) => `<div class="sm-row"><span>${label}</span><b class="${hl ? 'hl' : ''}">${val}</b></div>`;
    $('soubaMini').innerHTML = `<div class="card-head"><h3>落札相場</h3><span class="hint">${s.days || 120} 天 · ${fmt(s.count)} 件</span></div>
      <div class="sm-list">
        ${row('最安', t['最安価格'] != null ? fmt(t['最安価格']) + ' 円' : '—')}
        ${row('平均', t['平均価格'] != null ? fmt(t['平均価格']) + ' 円' : '—', true)}
        ${row('最高', t['最高価格'] != null ? fmt(t['最高価格']) + ' 円' : '—')}
      </div>
      <div class="attrs"><span class="attr">分类 <b>${esc((s.category && s.category.name) || '全部')}</b></span></div>`;
  }

  function renderQuery() {
    const d = state.data;
    const cands = (d && d.candidates) || [];
    if (!cands.length) {
      $('queryCard').innerHTML = `<div class="card-head"><h3>本次检索</h3><span class="hint">候选词</span></div><div class="compare-empty">这里会展示清洗后的候选词与识别出的品牌、型号。</div>`;
      return;
    }
    const chips = cands.map((c) => {
      const hit = (d.per_candidate || []).find((x) => x.id === c.id);
      return `<button class="chip ${c.id === state.activeCandidate ? 'active' : ''}" data-cand="${esc(c.id)}" type="button" title="${esc(c.reason)}">
        <b>${esc(c.label)}${hit ? `<em>${hit.count} 条</em>` : ''}</b><code>${esc(c.query)}</code></button>`;
    }).join('');
    const a = d.attrs || {};
    const attrs = [['作品', a.series], ['角色', a.character], ['特征', a.detail],
      ['品类', a.category], ['品牌', a.brand], ['平台', a.platform]]
      .filter(([, v]) => v).map(([k, v]) => `<span class="attr">${k} <b>${esc(v)}</b></span>`).join('');
    const conf = a.confidence === 'high' ? '识别 · 高' : (a.confidence === 'medium' ? '识别 · 中' : '识别 · 低');
    $('queryCard').innerHTML = `<div class="card-head"><h3>本次检索</h3><span class="hint">${conf} · ${d.mode === 'llm' ? 'AI 改写' : '规则改写'}</span></div>
      <div class="chips">${chips}</div>
      ${attrs ? `<div class="attrs">${attrs}</div>` : ''}
      ${d.needs_jp ? '<p class="query-note" style="color:var(--accent)">未能识别作品或角色，建议补一个日文商品名再查。</p>' : ''}
      <p class="query-note">切换候选只会重算站点链接；要按新词重查，请再点「查价格」。</p>`;
  }

  // ---------------------------------------------------------------- results
  function renderResults() {
    const d = state.data;
    if (!d) { renderPlatformChips([]); return; }
    const all = itemsAll();
    const shown = itemsShown();
    const plausible = all.filter((i) => (i.match || 0) > 0);
    const qualityHidden = state.onlyGenuine ? plausible.filter(badQuality).length : 0;
    const hidden = (all.length - plausible.length)
      + (state.onlyRelevant ? plausible.filter((i) => !relevant(i)).length : 0)
      + qualityHidden;
    const onlyLbl = document.querySelector('#onlyRelevant + span');
    if (onlyLbl) onlyLbl.textContent = (d.attrs && d.attrs.detail) ? '只看同款' : '只看相关';
    $('resultCount').textContent = shown.length + ' 件';
    const engine = d.llm_used ? 'AI 改写' : (d.llm_enabled === false ? '本地规则' : '');
    $('resultMeta').textContent = `原始 ${all.length} 条 · ${new Set(shown.map((i) => i.platform)).size} 个平台` + (engine ? ` · ${engine}` : '') + (state.lastQueryAt ? ` · ${hhmm(state.lastQueryAt)} 检索` : '');

    const note = $('resultNote');
    const parts = [];
    if (d.needs_jp) parts.push('未能识别作品/角色，结果可能不准；建议补一个日文商品名');
    if (hidden) {
      const a = (d.attrs || {});
      if (state.onlyRelevant) {
        parts.push(a.detail
          ? `已隐藏 ${hidden} 条同角色不同款 / 完全无关商品（取消「只看同款」可看同角色其他款）`
          : `已隐藏 ${hidden} 条非同款或低相关商品（取消「只看相关」可查看）`);
      } else {
        parts.push(`已隐藏 ${hidden} 条完全无关商品`);
      }
    }
    if (qualityHidden) {
      parts.push(`已隐藏 ${qualityHidden} 条闲鱼国产/仿品、异常低价或散件配件（取消「只看正版」可查看）`);
    }
    // Be explicit when a platform was queried but had nothing that is the
    // same product/version: silence there looks like a bug.
    const shownByPlat = {};
    shown.forEach((i) => { shownByPlat[i.platform] = (shownByPlat[i.platform] || 0) + 1; });
    const hiddenPlats = Object.entries(d.by_platform || {})
      .filter(([name, arr]) => (arr || []).length && !shownByPlat[name])
      .map(([name, arr]) => `${name} ${arr.length}`);
    if (hiddenPlats.length) {
      parts.push(`以下平台抓到商品，但没有同款/同版本，已隐藏：${hiddenPlats.join('、')}`);
    }
    const errs = d.errors || [];
    if (errs.length) parts.push('部分数据源未取到：' + errs.map((e) => `${e.source} — ${clip(e.reason, 60)}`).join('；'));
    if (parts.length) { note.hidden = false; note.textContent = parts.join(' · '); } else note.hidden = true;

    renderPlatformChips(shown);

    if (!shown.length) {
      $('resultsList').innerHTML = `<div class="blank"><div class="blank-mark"><svg viewBox="0 0 48 48"><path d="M12 12l24 24M36 12L12 36"/></svg></div><h3>这个筛选下没有商品</h3><p>试试取消「只看相关」或切换平台筛选；也可能是该关键词在楽天暂无现货。</p></div>`;
      return;
    }
    const groups = {};
    shown.forEach((i) => { (groups[i.platform || '其他'] ||= []).push(i); });
    $('resultsList').innerHTML = Object.entries(groups).map(([name, arr]) => {
      const med = median(arr.map((i) => i.price));
      const cur = arr[0] && arr[0].currency === 'CNY' ? '元' : '円';
      return `<section class="pgroup">
        <div class="pgroup-head"><h3>${esc(name)}</h3><span class="pcount">${arr.length}</span>
        <span class="pmed">中位 <b>${fmt(med)}</b> ${cur}</span></div>
        <div class="items-grid ${state.layout === 'grid' ? 'gallery' : ''}">${arr.map(itemCard).join('')}</div>
      </section>`;
    }).join('');
  }

  function itemCard(i) {
    const score = i.match || 0;
    const cls = score >= 65 ? '' : (score >= 42 ? 'mid' : 'low');
    const meta = [];
    if (i.shop) meta.push(`<span>${esc(clip(i.shop, 14))}</span>`);
    if (i.rating) meta.push(`<span>★ ${Number(i.rating).toFixed(1)}${i.reviews ? `（${i.reviews}）` : ''}</span>`);
    if (i.free_shipping) meta.push('<span class="free">包邮</span>');
    if (i.bids != null) meta.push(`<span>入札 ${i.bids}</span>`);
    if (i.ended_at) meta.push(`<span title="${ymd(i.ended_at)} ${hhmm(i.ended_at)}">${ymd(i.ended_at)} 落札</span>`);
    // Relative to the Ritao listing price, when the user has entered one.
    const ritaoCny = Number($('ritaoPrice').value || 0) * rate();
    const cny = toCny(i.price, i.currency);
    if (ritaoCny > 0 && cny) {
      const pct = Math.round((cny - ritaoCny) / ritaoCny * 100);
      if (pct <= -3) meta.push(`<span class="free">低于标价 ${Math.abs(pct)}%</span>`);
      else if (pct >= 3) meta.push(`<span class="above">高于标价 ${pct}%</span>`);
    }
    const img = i.image ? imgTag(i.image) : '<span class="noimg">无图</span>';
    const q = i.quality || {};
    const qCls = (q.bootleg || q.suspect) ? 'bad' : (q.parts ? 'part' : 'good');
    const qTag = q.label ? `<span class="qtag ${qCls}">${esc(q.label)}</span>` : '';
    return `<article class="item">
      <a class="item-thumb" href="${esc(i.url || '#')}" target="_blank" rel="noopener">${img}</a>
      <div class="item-body">
        <div class="item-top"><span class="score ${cls}">${score}</span><span class="plat">${esc(i.platform || '')}</span>${qTag}</div>
        <a class="item-title" href="${esc(i.url || '#')}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title || '商品')}</a>
        <div class="item-price">${fmt(i.price)}<small>${i.currency === 'CNY' ? '元' : '円'} · 约 ¥${fmt(toCny(i.price, i.currency))}</small></div>
        ${meta.length ? `<div class="item-meta">${meta.join('')}</div>` : ''}
      </div>
    </article>`;
  }

  function renderPlatformChips(list) {
    const counts = {};
    itemsAll().filter(relevant)
      .filter((i) => !state.onlyGenuine || !badQuality(i))
      .forEach((i) => { counts[i.platform || '其他'] = (counts[i.platform || '其他'] || 0) + 1; });
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    if (entries.length < 2) { $('platformFilter').innerHTML = ''; return; }
    $('platformFilter').innerHTML = [
      `<button class="fchip ${state.platform === 'all' ? 'on' : ''}" data-plat="all" type="button">全部 ${list.length}</button>`,
      ...entries.map(([n, c]) => `<button class="fchip ${state.platform === n ? 'on' : ''}" data-plat="${esc(n)}" type="button">${esc(n)} ${c}</button>`)
    ].join('');
  }

  // ---------------------------------------------------------------- souba
  function renderSouba() {
    const s = (state.data && state.data.souba) || {};
    const t = s.stats || {};
    if (!state.data) {
      $('soubaStats').innerHTML = '';
      $('soubaDist').innerHTML = '';
      $('soubaCategory').innerHTML = '';
      $('soubaNote').textContent = '先在上方检索一次，这里会显示该商品的落札相場。';
      $('soubaItems').innerHTML = '<p class="compare-empty">还没有检索结果。落札明细会带图片、落札价、落札日期和入札数。</p>';
      $('soldLinks').innerHTML = '<p class="compare-empty">开始检索后显示。</p>';
      $('soubaLink').href = 'https://auctions.yahoo.co.jp/';
      return;
    }
    const defs = [['最安価格', '最安落札', ''], ['平均価格', '平均落札', 'hl'], ['最高価格', '最高落札', ''], ['count', '统计样本', 'good']];
    $('soubaStats').innerHTML = defs.map(([k, label, cls]) => {
      const val = k === 'count' ? s.count : t[k];
      const unit = k === 'count' ? '件' : '円';
      return `<div class="stat ${cls}"><div class="s-label">${label}</div>
        <div class="s-value">${fmt(val)}<small>${unit}</small></div>
        <div class="s-note">${k === 'count' ? `近 ${s.days || 120} 天` : '≈¥' + fmt(val != null ? val * rate() : null)}</div></div>`;
    }).join('');

    $('soubaLink').href = s.url || '#';
    $('soubaNote').textContent = s.ok ? (s.note || '') : ('读取失败：' + clip(s.reason, 90));

    // category picker
    const cats = s.categories || [];
    const cur = state.soubaCategory === 'all' ? '__all__'
      : (state.soubaCategory && state.soubaCategory !== 'auto' ? state.soubaCategory
        : (s.category ? s.category.id : '__auto__'));
    $('soubaCategory').innerHTML = [
      `<option value="__auto__"${cur === '__auto__' ? ' selected' : ''}>自动${s.category ? ' · ' + esc(s.category.name) : ''}</option>`,
      `<option value="__all__"${cur === '__all__' ? ' selected' : ''}>不筛选分类</option>`,
      ...cats.map((c) => `<option value="${esc(c.id)}"${cur === c.id ? ' selected' : ''}>${esc(c.name)}</option>`)
    ].join('');

    renderDist(s.items || []);

    const items = [...(s.items || [])].sort((a, b) => (b.ended_at || 0) - (a.ended_at || 0));
    $('soubaItems').innerHTML = items.length ? items.slice(0, 16).map((i) => `
      <article class="sold">
        <a class="sold-thumb" href="${esc(i.url || s.url || '#')}" target="_blank" rel="noopener">
          ${i.image ? imgTag(i.image) : '<span class="noimg">无图</span>'}
        </a>
        <div class="sold-body">
          <div class="sold-top"><span class="sold-price">${fmt(i.price)}<small>円 ≈¥${fmt(toCny(i.price, 'JPY'))}</small></span>
          <span class="sold-date">${i.ended_at ? ymd(i.ended_at) : ''}</span></div>
          <a class="sold-title" href="${esc(i.url || s.url || '#')}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title)}</a>
          <div class="sold-meta">
            ${i.bids != null ? `<span class="pill">入札 ${i.bids}</span>` : ''}
            ${i.free_shipping ? '<span class="pill free">送料無料</span>' : ''}
            <span class="pill">落札</span>
          </div>
        </div>
      </article>`).join('') : '<p class="compare-empty">该分类下暂未解析到落札明细，可点右上角打开原页。</p>';

    const sold = (state.links || []).filter((l) => (l.kind || '').includes('成交'));
    $('soldLinks').innerHTML = sold.length ? sold.map((l) => `
      <a class="link-item" href="${esc(l.url)}" target="_blank" rel="noopener"><div class="link-main">
        <div class="link-top"><span class="link-name">${esc(l.name)} ↗</span><span class="link-kind">${esc(l.kind || '')}</span></div>
        <div class="link-hint">${esc(l.hint || '')}</div></div></a>`).join('') : '<p class="compare-empty">开始检索后显示。</p>';
  }

  function renderDist(items) {
    const prices = items.map((i) => i.price).filter((p) => p > 0);
    const box = $('soubaDist');
    if (prices.length < 4) { box.innerHTML = ''; return; }
    const lo = Math.min(...prices), hi = Math.max(...prices);
    const bins = Math.max(4, Math.min(9, prices.length));
    const step = (hi - lo) / bins || 1;
    const counts = new Array(bins).fill(0);
    prices.forEach((p) => { const k = Math.min(bins - 1, Math.floor((p - lo) / step)); counts[k]++; });
    const maxC = Math.max(...counts);
    const med = median(prices);
    const medBin = Math.min(bins - 1, Math.floor((med - lo) / step));
    const W = 640, H = 110, bw = W / bins;
    const bars = counts.map((c, idx) => {
      const h = Math.max(2, (c / maxC) * (H - 24));
      return `<rect class="bar ${idx === medBin ? 'mid' : ''}" x="${(idx * bw + 3).toFixed(1)}" y="${(H - 18 - h).toFixed(1)}" width="${(bw - 6).toFixed(1)}" height="${h.toFixed(1)}" rx="2"><title>${fmt(lo + idx * step)}–${fmt(lo + (idx + 1) * step)} 円 · ${c} 件</title></rect>`;
    }).join('');
    box.innerHTML = `<div class="dist-title"><b>落札价分布</b><span>${prices.length} 条明细 · 中位 ${fmt(med)} 円 · 区间 ${fmt(lo)}–${fmt(hi)} 円</span></div>
      <svg viewBox="0 0 ${W} ${H + 8}" role="img" aria-label="落札价分布">
        ${bars}
        <line class="axis" x1="0" y1="${H - 18}" x2="${W}" y2="${H - 18}"/>
        <text x="0" y="${H + 2}">${fmt(lo)}</text>
        <text x="${W}" y="${H + 2}" text-anchor="end">${fmt(hi)}</text>
      </svg>`;
  }

  // ---------------------------------------------------------------- sites
  function linkCard(l) {
    return `<div class="link-item">
      <input type="checkbox" data-open="${esc(l.url)}" data-id="${esc(l.id)}" ${state.selected.has(l.id) ? 'checked' : ''} aria-label="选择 ${esc(l.name)}">
      <div class="link-main">
        <div class="link-top"><a class="link-name" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.name)} ↗</a><span class="link-kind">${esc(l.region || '')} · ${esc(l.kind || '')}</span><span class="link-badge ${l.priced ? 'on' : ''}" title="${l.priced ? '可以直接显示抓取到的价格' : '仅生成搜索链接，不抓取页面'}">${l.priced ? '价格' : '跳转'}</span></div>
        <div class="link-hint">${esc(l.hint || '')}</div>
      </div></div>`;
  }

  async function ensureLinks() {
    if (state.links.length) return;
    const kw = state.cnQuery || $('keyword').value.trim();
    if (!kw) return;
    try {
      const r = await api('/api/links?' + new URLSearchParams({
        keyword: kw, jp: $('jpKeyword').value.trim()
      }));
      state.links = r.links || [];
      renderSites();
    } catch (e) { /* offline */ }
  }

  function renderSites() {
    $('links').innerHTML = state.links.length
      ? state.links.map(linkCard).join('')
      : '<p class="compare-empty">开始检索后生成各平台搜索链接。</p>';
    $('links').querySelectorAll('input[data-open]').forEach((b) => b.addEventListener('change', () => {
      if (b.checked) state.selected.add(b.dataset.id); else state.selected.delete(b.dataset.id);
    }));
  }

  function renderFigures() {
    const f = state.data || {};
    const h = f.hpoi || {}, items = h.items || [];
    $('figureHint').textContent = items.length ? `Hpoi 命中 ${items.length} 条` : '';
    $('figureItems').innerHTML = items.length ? items.slice(0, 9).map((i) => `
      <article class="figure-item">
        <a href="${esc(i.url)}" target="_blank" rel="noopener">${imgTag(i.image)}</a>
        <div class="fi-body">
          <a class="fi-title" href="${esc(i.url)}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title || '手办资料')}</a>
          <div class="fi-meta">${i.release ? esc(i.release) + ' 发售' : '发售日见详情'}${i.list_price ? ` · ${fmt(i.list_price)} 円` : ''}</div>
          <div class="fi-tags">${esc((i.tags || []).slice(0, 5).join(' / '))}</div>
          <div class="fi-detail">${esc(i.detail || '')}</div>
        </div>
      </article>`).join('') : `<p class="compare-empty">${esc(h.reason || '检索后显示 Hpoi 发售资料。')}</p>`;
    const fl = f.figure_links || [];
    $('figureLinks').innerHTML = fl.length ? fl.map((l) => `
      <div class="link-item"><div class="link-main">
        <div class="link-top"><a class="link-name" href="${esc(l.url || l.home)}" target="_blank" rel="noopener">${esc(l.name)} ↗</a>
        <span class="link-kind">${l.status === 'maintenance' ? '维护中' : (l.status === 'shield' ? '有反爬盾' : '可用')}</span></div>
        <div class="fig-actions">
          <a class="fig-find" href="${esc(l.find)}" target="_blank" rel="noopener">站内精准找 ↗</a>
          ${l.url ? `<a class="fig-home" href="${esc(l.home)}" target="_blank" rel="noopener">官网</a>` : ''}
        </div>
        <div class="link-hint">${esc(l.hint || '')}</div>
      </div></div>`).join('') : '<p class="compare-empty">开始检索后显示。</p>';
  }

  // ---------------------------------------------------------------- track
  function renderTrack() {
    renderWatch();
    const h = (state.data && state.data.history) || { history: [], summary: { platforms: {} } };
    const rows = h.history || [], box = $('chart');
    if (!rows.length) {
      box.innerHTML = '<p class="compare-empty">还没有数据。查几次之后，这里会画出你自己积累的价格曲线。</p>';
      $('historySummary').innerHTML = ''; $('historyMeta').textContent = '';
      return;
    }
    const byDay = {};
    rows.forEach((r) => { const d = new Date(r.created_at * 1000); const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; (byDay[k] ||= []).push(r.price); });
    const keys = Object.keys(byDay).sort();
    const pts = keys.map((k, i) => ({ x: keys.length === 1 ? .5 : i / (keys.length - 1), v: median(byDay[k]), k, n: byDay[k].length }));
    const vals = pts.map((p) => p.v), lo = Math.min(...vals) * .95, hi = Math.max(...vals) * 1.05, span = hi - lo || 1;
    const X = (p) => 46 + p.x * 580, Y = (v) => 132 - ((v - lo) / span) * 104;
    const path = pts.map((p, i) => `${i ? 'L' : 'M'}${X(p).toFixed(1)},${Y(p).toFixed(1)}`).join(' ');
    const area = `${path} L${X(pts[pts.length - 1]).toFixed(1)},140 L${X(pts[0]).toFixed(1)},140 Z`;
    box.innerHTML = `<svg viewBox="0 0 680 160" preserveAspectRatio="none" role="img" aria-label="历史价格曲线">
      <line x1="46" y1="140" x2="626" y2="140" style="stroke:var(--line)" stroke-width="1"/>
      <text x="2" y="30" style="fill:var(--faint);font-size:10px">${fmt(hi)}</text>
      <text x="2" y="140" style="fill:var(--faint);font-size:10px">${fmt(lo)}</text>
      <path d="${area}" style="fill:var(--accent-soft);stroke:none"/>
      <path d="${path}" style="fill:none;stroke:var(--accent)" stroke-width="2" stroke-linejoin="round"/>
      ${pts.map((p) => `<circle cx="${X(p).toFixed(1)}" cy="${Y(p).toFixed(1)}" r="3.4" style="fill:var(--accent)"><title>${p.k} · 中位 ${fmt(p.v)} · ${p.n} 条</title></circle>`).join('')}
      <text x="46" y="156" style="fill:var(--faint);font-size:10px">${keys[0]}</text>
      <text x="626" y="156" text-anchor="end" style="fill:var(--faint);font-size:10px">${keys[keys.length - 1]}</text></svg>`;
    $('historyMeta').textContent = `${rows.length} 条记录 / ${keys.length} 天`;
    $('historySummary').innerHTML = Object.entries(h.summary.platforms || {}).map(([k, v]) =>
      `<div class="stat"><div class="s-label">${esc(k)}</div><div class="s-value">${fmt(v.median)}<small>円</small></div><div class="s-note">最低 ${fmt(v.min)} · ${v.count} 条</div></div>`
    ).join('') || '<p class="compare-empty">暂无统计。</p>';
  }

  function renderRecent(list) {
    $('recent').innerHTML = list.length
      ? list.map((k) => `<button class="chip" data-kw="${esc(k)}" type="button">${esc(k)}</button>`).join('')
      : '<p class="compare-empty">还没有检索记录。</p>';
  }

  function renderWatch() {
    $('navCountWatch').textContent = state.watch.length || '';
    $('watchItems').innerHTML = state.watch.length ? state.watch.map((w) => `
      <div class="watch-item"><div class="wi-name">${esc(w.keyword)}</div>
      <div class="wi-meta">${w.target_price ? `目标 ¥${fmt(w.target_price)}` : '未设目标价'}</div>
      <button class="btn btn-quiet" data-watch-open="${esc(w.keyword)}" type="button">重查</button>
      <button class="btn btn-quiet" data-watch-del="${esc(w.keyword)}" type="button">移除</button></div>`).join('')
      : '<p class="compare-empty">还没有收藏。检索后点右上角「加入追踪」即可。</p>';
  }

  function renderVisualSites() {
    $('visualSites').innerHTML = state.visualSites.length ? state.visualSites.map((s) =>
      `<a class="link-item" href="${esc(s.url)}" target="_blank" rel="noopener"><div class="link-main">
        <div class="link-top"><span class="link-name">${esc(s.name)} ↗</span></div>
        <div class="link-hint">${esc(s.hint || '')}</div></div></a>`).join('')
      : '<p class="compare-empty">暂无入口。</p>';
  }

  function renderVisionResult(v) {
    const box = $('visionResult');
    if (!v) { box.innerHTML = '<p class="compare-empty">上传图片后显示品牌、系列、角色、型号与日文检索词。</p>'; return; }
    const d = v.analysis || {};
    if (!v.ok) {
      box.innerHTML = `<div class="notice">${esc(v.reason || '未识别')}</div><p class="compare-empty">图片已保存在本机，可直接用右下「平台原生以图搜图」手动上传。</p>`;
      return;
    }
    const cells = [['品牌', d.brand], ['系列 / 作品', d.series], ['角色', d.character], ['型号 / 比例', d.model], ['品类', d.category], ['成色线索', d.condition]];
    box.innerHTML = `<div class="vr-primary"><div><div class="kpi-label">识别为</div><b>${esc(d.cn_name || d.jp_name || '未命名商品')}</b></div><button id="btnUseVision" type="button">用此结果检索</button></div>
      <div class="vr-grid">${cells.filter(([, x]) => x).map(([l, x]) => `<div class="vr-cell"><span>${l}</span><b title="${esc(x)}">${esc(x)}</b></div>`).join('')}</div>
      <div><div class="kpi-label">日文检索词（点击填入）</div><div class="vr-queries">${(d.jp_queries || []).map((q) => `<button data-vq="${esc(q)}" type="button" title="${esc(q)}">${esc(q)}</button>`).join('') || '<span class="hint">模型未给出</span>'}</div></div>
      <p class="hint">${esc(d.notes || '')}${d.confidence ? ` · 置信度 ${esc(d.confidence)}` : ''} · 模型 ${esc(v.model || '')}</p>`;
  }

  // ---------------------------------------------------------------- plan
  async function openPlan() {
    if (!$('keyword').value.trim()) { toast('先填商品名'); $('keyword').focus(); return; }
    // Keep a hand-edited Chinese query when the plan is reopened for the same
    // keyword (e.g. after toggling a token), but drop it for a new product.
    if (state.cnManualKey !== $('keyword').value.trim()) {
      state.planCnDirty = false;
      state.cnManualKey = '';
      $('planCnQuery').value = '';
    }
    $('planBackdrop').hidden = false;
    $('planModal').hidden = false;
    $('planSkip').checked = !state.planConfirm;
    $('planDeep').checked = state.deepMode;
    await loadPlan();
  }
  function closePlan() {
    $('planBackdrop').hidden = true;
    $('planModal').hidden = true;
  }
  function schedulePlan() {
    clearTimeout(planTimer);
    planTimer = setTimeout(loadPlan, 220);
  }
  async function loadPlan() {
    const kw = $('keyword').value.trim(), jp = $('jpKeyword').value.trim();
    $('planQueries').innerHTML = '<p class="hint">正在生成方案…</p>';
    try {
      const r = await api('/api/plan?' + new URLSearchParams({
        keyword: kw, jp, drop: JSON.stringify([...state.dropped])
      }));
      state.plan = r;
      renderPlan();
    } catch (e) {
      $('planQueries').innerHTML = `<p class="hint">方案生成失败：${esc(e.message)}</p>`;
    }
  }
  function renderPlan() {
    const p = state.plan || {};
    const a = p.attrs || {};
    renderTokens();
    // Editable Chinese query: what 闲鱼 / 淘宝 / 视频 / 手办站 will actually use.
    if (!state.planCnDirty) $('planCnQuery').value = p.cn_query || p.cn || '';
    const attrs = [['作品', a.series], ['角色', a.character], ['特征', a.detail],
      ['品类', a.category], ['品牌', a.brand], ['平台', a.platform]]
      .filter(([, v]) => v)
      .map(([k, v]) => `<span class="attr">${k} <b>${esc(v)}</b></span>`).join('');
    const conf = a.confidence === 'high' ? '识别 · 高' : (a.confidence === 'medium' ? '识别 · 中' : '识别 · 低');
    $('planAttrs').innerHTML = (attrs || '<span class="hint">未能识别作品或角色，请手动填写查询</span>')
      + `<span class="attr">${conf}</span>`;
    const cands = (p.candidates || []).filter((c) => c.usable);
    $('planQueries').innerHTML = '';
    if (!cands.length) { addPlanRow('自定义', p.jp || $('jpKeyword').value.trim() || '', true); return; }
    cands.forEach((c, i) => addPlanRow(c.label || `方案 ${i + 1}`, c.query, i < 2));
    if (p.needs_jp) {
      const hint = document.createElement('p');
      hint.className = 'hint';
      hint.style.color = 'var(--accent)';
      hint.textContent = '未能识别作品/角色，建议补一个日文商品名，或在下面手动写查询。';
      $('planQueries').appendChild(hint);
    }
  }
  function addPlanRow(label, value, checked) {
    const row = document.createElement('div');
    row.className = 'pq-row';
    row.innerHTML = `<input type="checkbox" ${checked ? 'checked' : ''}>
      <span class="pq-label">${esc(label)}</span>
      <input type="text" value="${esc(value || '')}" placeholder="日文检索词">
      <button class="pq-del" type="button" aria-label="删除">✕</button>`;
    $('planQueries').appendChild(row);
    return row;
  }

  // ---------------------------------------------------------------- CN / JP compare
  function renderComparePage() {
    const box = $('cnSummaryCard');
    if (!box) return;
    const d = state.data;
    const link = (state.links || []).find((l) => l.id === 'xianyu');
    $('cnXianyuLink').href = link ? link.url
      : 'https://www.goofish.com/search?q=' + encodeURIComponent((d && (d.cn_query || d.keyword)) || state.cnQuery || $('keyword').value || '');
    if (!d) {
      box.innerHTML = '<div class="compare-empty">先检索一次，这里会显示同款中日价格对比。</div>';
      $('jpCompareList').innerHTML = '';
      $('cnCompareList').innerHTML = '';
      $('landedTable').innerHTML = '';
      return;
    }
    const active = focusSet().filter((i) => i.platform !== '雅虎落札' && i.price > 0);
    const sold = itemsAll().filter((i) => i.platform === '雅虎落札' && i.price > 0);
    const cn = itemsAll().filter((i) => i.currency === 'CNY' && i.price > 0 && !badQuality(i));
    const jpMed = median(active.map((i) => i.price)) ?? median(sold.map((i) => i.price));
    const ship = Number($('cnShip').value || 0);
    const fee = Number($('cnFee').value || 0);
    const tariff = Number($('cnTariff').value || 0);
    const landed = (p) => p * rate() * (1 + fee / 100 + tariff / 100) + ship * rate();
    const jpLanded = jpMed != null ? landed(jpMed) : null;
    const cnMedian = median(cn.map((i) => i.price));
    const cnManual = Number($('cnManualPrice').value || 0) || null;
    const cnRef = cnMedian ?? cnManual;
    const delta = (jpLanded != null && cnRef != null) ? jpLanded - cnRef : null;

    const cell = (label, value, note, cls) => `<div class="stat ${cls || ''}">
      <div class="s-label">${label}</div><div class="s-value">${value}</div><div class="s-note">${note}</div></div>`;
    const verdict = delta == null
      ? '填入国内价格后显示差价'
      : (delta <= 0 ? `日本到手更便宜 ¥${fmt(Math.abs(delta))}` : `国内便宜 ¥${fmt(delta)}`);
    box.innerHTML = `<div class="cn-summary">
      ${cell('日本同款中位', jpMed == null ? '—' : `${fmt(jpMed)}<small>円</small>`, jpMed == null ? '暂无报价' : `≈¥${fmt(jpMed * rate())} · ${active.length ? active.length + ' 条在售' : sold.length + ' 条落札参考'}`)}
      ${cell('日本到手估算', jpLanded == null ? '—' : `¥${fmt(jpLanded)}`, '含运费 / 服务费 / 关税', 'hl')}
      ${cell('国内参考', cnRef == null ? '—' : `¥${fmt(cnRef)}`, cn.length ? `${cn.length} 条闲鱼` : '手动输入', cnRef == null ? '' : 'good')}
      ${cell('差价', delta == null ? '—' : `${delta <= 0 ? '−' : '+'}¥${fmt(Math.abs(delta))}`, verdict, delta == null ? '' : (delta <= 0 ? 'good' : 'hl'))}
    </div>
    <p class="cmp-foot">到手估算 = 商品价 ×（1 + 服务费 + 关税）× 汇率 + 国际运费。实际以转运/代购账单为准。</p>`;

    const jpList = [...active, ...sold].sort((a, b) => landed(a.price) - landed(b.price)).slice(0, 8);
    $('jpCompareList').innerHTML = jpList.length ? jpList.map((i) => {
      const land = landed(i.price);
      const dd = cnRef != null ? land - cnRef : null;
      return `<div class="cmp-item">
        <a href="${esc(i.url || '#')}" target="_blank" rel="noopener">${imgTag(i.image)}</a>
        <div class="ci-body"><a class="ci-title" href="${esc(i.url || '#')}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title)}</a>
          <div class="ci-meta"><span>${esc(i.platform || '')}</span>${i.ended_at ? `<span>${ymd(i.ended_at)} 落札</span>` : ''}</div></div>
        <div class="ci-price"><b>${fmt(i.price)} 円</b><small>到手 ≈¥${fmt(land)}</small>
          ${dd == null ? '' : `<span class="delta ${dd <= 0 ? 'good' : 'bad'}">${dd <= 0 ? '低' : '高'} ¥${fmt(Math.abs(dd))}</span>`}</div>
      </div>`;
    }).join('') : '<p class="compare-empty">没有同款日本报价。</p>';

    if (cn.length) {
      $('cnCompareList').innerHTML = cn.sort((a, b) => a.price - b.price).slice(0, 8).map((i) => `
        <div class="cmp-item"><a href="${esc(i.url || '#')}" target="_blank" rel="noopener">${imgTag(i.image)}</a>
        <div class="ci-body"><a class="ci-title" href="${esc(i.url || '#')}" target="_blank" rel="noopener">${esc(i.title)}</a>
        <div class="ci-meta"><span>${esc(i.platform || '闲鱼')}</span></div></div>
        <div class="ci-price"><b>¥${fmt(i.price)}</b><small>人民币</small></div></div>`).join('');
    } else if (cnManual) {
      $('cnCompareList').innerHTML = `<div class="cmp-item"><div class="ci-body"><div class="ci-title">手动输入的国内价格</div>
        <div class="ci-meta"><span>参考价</span></div></div><div class="ci-price"><b>¥${fmt(cnManual)}</b><small>人民币</small></div></div>`;
    } else {
      $('cnCompareList').innerHTML = `<div class="compare-empty">闲鱼网页端对自动化有限制。可以点右上角「抓取闲鱼」试一次，或直接打开闲鱼搜索后把价格填到上面。</div>`;
    }

    const rows = jpList.slice(0, 6).map((i) => {
      const land = landed(i.price);
      const dd = cnRef != null ? land - cnRef : null;
      return `<tr><td>${esc(clip(i.title, 40))}</td>
        <td class="num">${fmt(i.price)} 円</td>
        <td class="num">¥${fmt(i.price * rate())}</td>
        <td class="num">¥${fmt(land)}</td>
        <td class="num ${dd == null ? '' : (dd <= 0 ? 'delta-good' : 'delta-bad')}">${dd == null ? '—' : (dd <= 0 ? '低' : '高') + ' ¥' + fmt(Math.abs(dd))}</td></tr>`;
    }).join('');
    $('landedTable').innerHTML = rows ? `<table>
      <thead><tr><th>商品</th><th style="text-align:right">日本价</th><th style="text-align:right">商品折合</th><th style="text-align:right">到手估算</th><th style="text-align:right">对比国内</th></tr></thead>
      <tbody>${rows}</tbody></table>` : '<p class="compare-empty">检索后显示逐件到手价。</p>';
  }

  async function fetchCn() {
    if (!state.data) { toast('先检索一次'); return; }
    const kw = (state.cnQuery || state.data.keyword || $('keyword').value || '').trim();
    const btn = $('btnFetchCn');
    btn.disabled = true; btn.textContent = '抓取中…';
    try {
      const r = await api('/api/browser_search', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ platform: 'xianyu', keyword: kw })
      });
      const items = (r.items || []).map((i) => ({ ...i, currency: 'CNY', platform: '闲鱼', match: Math.max(i.match || 0, 55) }));
      if (items.length) {
        const seen = new Set(itemsAll().map((i) => i.url));
        state.data.items = [...itemsAll(), ...items.filter((i) => !seen.has(i.url))];
        renderAll();
        toast(`抓到 ${items.length} 条闲鱼结果`);
      } else {
        toast(r.reason || '闲鱼未返回结果，建议手动打开闲鱼');
      }
    } catch (e) { toast('抓取失败：' + e.message); }
    btn.disabled = false; btn.textContent = '抓取闲鱼';
  }

  // ---------------------------------------------------------------- export
  const csvCell = (v) => {
    const t = String(v == null ? '' : v);
    return /[",\n\r]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
  };
  function exportCsv() {
    const rows = itemsShown().length ? itemsShown() : itemsAll();
    if (!rows.length) {
      const kw = $('keyword').value.trim();
      if (!kw) { toast('先检索，再导出'); return; }
      window.location.href = '/api/export?keyword=' + encodeURIComponent(kw);
      return;
    }
    const head = ['匹配度', '平台', '商品名', '价格', '币种', '折合人民币',
      '质量', '店铺', '评分', '包邮', '落札日期', '入札数', '链接'];
    const lines = [head.map(csvCell).join(',')];
    rows.forEach((i) => {
      lines.push([
        i.match, i.platform || '', i.title || '', i.price, i.currency || 'JPY',
        toCny(i.price, i.currency) == null ? '' : Math.round(toCny(i.price, i.currency)),
        i.quality_label || '', i.shop || '', i.rating || '',
        i.free_shipping ? '是' : '', i.ended_at ? ymd(i.ended_at) : '',
        i.bids == null ? '' : i.bids, i.url || ''
      ].map(csvCell).join(','));
    });
    const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    const name = ($('keyword').value.trim() || 'export').replace(/[\\/:*?"<>|]/g, '_').slice(0, 24);
    a.href = URL.createObjectURL(blob);
    a.download = `日淘比价_${name}_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast(`已导出 ${rows.length} 条`);
  }

  // ---------------------------------------------------------------- media
  async function loadMedia() {
    const kw = $('keyword').value.trim(), jp = $('jpKeyword').value.trim();
    if (!kw && !jp) { toast('先填商品名'); $('keyword').focus(); return; }
    if (state.mediaLoading) return;
    state.mediaLoading = true;
    const btn = $('btnMediaSearch');
    btn.disabled = true; btn.textContent = '查询中…';
    $('mediaItems').innerHTML = '<div class="blank"><h3>正在查发售资料…</h3><p>VNDB · MusicBrainz · AniList · 日文维基</p></div>';
    try {
      const attrs = JSON.stringify((state.data && state.data.attrs) || {});
      const d = await api('/api/media?' + new URLSearchParams({
        keyword: state.cnQuery || kw, jp, attrs
      }));
      state.media = d;
      renderMedia();
    } catch (e) {
      state.media = { ok: false, items: [], links: [], errors: [{ source: '服务', reason: e.message }], type_label: '' };
      renderMedia();
    }
    state.mediaLoading = false;
    btn.disabled = false; btn.textContent = '查发售资料';
  }
  function renderMedia() {
    const d = state.media;
    if (!d) {
      $('mediaItems').innerHTML = '<div class="blank"><h3>还没有资料</h3><p>点右上角「查发售资料」，会用当前商品名去查游戏、CD、BD 与书籍数据库。</p></div>';
      $('mediaLinks').innerHTML = '';
      $('mediaQuery').textContent = '';
      if ($('mediaType')) $('mediaType').textContent = '';
      $('mediaNote').hidden = true;
      return;
    }
    $('mediaType').textContent = d.type_label || '';
    $('mediaQuery').textContent = d.query ? `查询词：${d.query}` : '';
    const items = d.items || [];
    $('mediaItems').innerHTML = items.length ? items.map((i) => `
      <article class="media-card">
        <a class="mc-thumb" href="${esc(i.url || '#')}" target="_blank" rel="noopener">
          ${i.image ? `<img src="${esc(i.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">` : '<span class="noimg">无图</span>'}
        </a>
        <div class="mc-body">
          <div class="mc-top"><span class="mc-src">${esc(i.source || '')}</span>${i.date ? `<span class="mc-date">${esc(i.date)}</span>` : ''}</div>
          <a class="mc-title" href="${esc(i.url || '#')}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title || '')}</a>
          ${i.subtitle && i.subtitle !== i.title ? `<div class="mc-sub">${esc(i.subtitle)}</div>` : ''}
          ${i.meta ? `<div class="mc-meta">${esc(i.meta)}</div>` : ''}
          ${i.desc ? `<p class="mc-desc">${esc(clip(i.desc, 120))}</p>` : ''}
        </div>
      </article>`).join('')
      : '<div class="blank"><h3>没有查到资料</h3><p>可能是标题太新或太冷门，试试更短的日文原名。</p></div>';
    $('mediaLinks').innerHTML = (d.links || []).map((l) => `
      <a class="vlink" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.name)} ↗</a>`).join('');
    const errs = d.errors || [];
    const note = $('mediaNote');
    if (errs.length) {
      note.hidden = false;
      note.textContent = '部分数据源未取到：' + errs.map((e) => `${e.source} — ${clip(e.reason, 50)}`).join('；');
    } else note.hidden = true;
  }

  // ---------------------------------------------------------------- videos
  const fmtViews = (n) => {
    const v = Number(n) || 0;
    if (v >= 10000) return (v / 10000).toFixed(v >= 100000 ? 0 : 1) + ' 万';
    return String(v);
  };
  async function loadVideos(showView) {
    const kw = $('keyword').value.trim(), jp = $('jpKeyword').value.trim();
    if (!kw && !jp) { toast('先填商品名'); $('keyword').focus(); return; }
    if (state.videoLoading) return;
    state.videoLoading = true;
    const btn = $('btnVideoSearch');
    btn.disabled = true; btn.textContent = '搜索中…';
    $('videoList').innerHTML = '<div class="blank"><h3>正在找视频…</h3><p>B 站 · niconico</p></div>';
    try {
      const attrs = JSON.stringify((state.data && state.data.attrs) || {});
      const d = await api('/api/videos?' + new URLSearchParams({ keyword: kw, jp, attrs, cn: state.cnQuery || '' }));
      state.videos = d;
      renderVideos();
    } catch (e) {
      $('videoList').innerHTML = `<div class="blank"><h3>视频检索失败</h3><p>${esc(e.message)}</p></div>`;
    }
    state.videoLoading = false;
    btn.disabled = false; btn.textContent = '搜索视频';
    if (showView) switchView('videos');
  }
  function renderVideos() {
    const d = state.videos;
    if (!d) {
      $('videoList').innerHTML = '<div class="blank"><h3>还没有视频结果</h3><p>点右上角「搜索视频」，会用当前商品名去找开箱与评测。</p></div>';
      $('videoLinks').innerHTML = '';
      $('videoQueries').innerHTML = '';
      $('videoNote').hidden = true;
      return;
    }
    const q = d.queries || {};
    $('videoQueries').innerHTML = [
      ...(q.cn || []).map((x) => `<span class="vq">${esc(x)}</span>`),
      q.jp ? `<span class="vq jp">${esc(q.jp)}</span>` : ''
    ].join('');
    const items = d.items || [];
    $('navCountVideo').textContent = items.length || '';
    $('videoList').innerHTML = items.length ? items.map((v) => `
      <article class="video-card">
        <a class="vc-thumb" href="${esc(v.url)}" target="_blank" rel="noopener">
          ${v.image ? `<img src="${esc(v.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}
          ${v.duration ? `<span class="vc-dur">${esc(v.duration)}</span>` : ''}
        </a>
        <div class="vc-body">
          <a class="vc-title" href="${esc(v.url)}" target="_blank" rel="noopener" title="${esc(v.title)}">${esc(v.title)}</a>
          <div class="vc-meta"><span>${esc(v.platform)}</span>${v.author ? `<span>${esc(clip(v.author, 16))}</span>` : ''}${v.views ? `<span>${fmtViews(v.views)} 播放</span>` : ''}${v.date ? `<span>${esc(v.date)}</span>` : ''}</div>
        </div>
      </article>`).join('')
      : '<div class="blank"><h3>没有找到视频</h3><p>换个更短的中文名，或点下方直接站内搜索。</p></div>';
    $('videoLinks').innerHTML = (d.links || []).map((l) => `
      <a class="vlink" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.name)} ↗</a>`).join('');
    const errs = d.errors || [];
    const note = $('videoNote');
    if (errs.length) {
      note.hidden = false;
      note.textContent = '部分平台未取到：' + errs.map((e) => `${e.source} — ${clip(e.reason, 60)}`).join('；');
    } else note.hidden = true;
  }

  // ---------------------------------------------------------------- config
  function renderCfgBrowserPlatforms() {
    const opts = [['mercari', '煤炉 Mercari'], ['xianyu', '闲鱼（常被拦）'], ['yahoo_closed', '雅虎日拍']];
    const on = state.cfg.browser_platforms || ['mercari'];
    $('cfgBrowserPlatforms').innerHTML = opts.map(([id, l]) => `<label><input type="checkbox" data-bpid="${id}" ${on.includes(id) ? 'checked' : ''}>${l}</label>`).join('');
  }
  function renderCfgPlatforms() {
    const opts = [['xianyu', '闲鱼'], ['mercari', '煤炉'], ['mercari_sold', '煤炉已售'], ['surugaya', '骏河屋'], ['yahoo_auction', '雅虎日拍'], ['yahoo_closed', '落札相場'], ['aucfan', 'AucFan'], ['rakuten', '乐天'], ['amazon_jp', '日亚'], ['kakaku', '价格.com'], ['ritao', '日淘市集']];
    const on = state.cfg.enabled_platforms || [];
    $('cfgPlatforms').innerHTML = opts.map(([id, l]) => `<label><input type="checkbox" data-pid="${id}" ${on.includes(id) ? 'checked' : ''}>${l}</label>`).join('');
  }
  async function loadSafety() {
    const s = await api('/api/safety');
    $('safetyBox').innerHTML = `<b>当前状态：</b>近一小时 ${s.calls_last_hour}/${s.max_per_hour} 次 · 最小间隔 ${s.min_interval_seconds}s + 抖动 · ${s.headless ? '静默模式' : '可见窗口'} · 会话${s.session_running ? '运行中' : '未启动'}<br>${esc((s.guarantees || []).join(' / '))}<br><span style="color:var(--gold)">${esc(s.no_guarantee || '')}</span>`;
  }
  async function saveCfg() {
    const payload = {
      jpy_cny: Number($('cfgRate').value || 4.8) / 100,
      ritao_price_jpy: Number($('ritaoPrice').value || 0) || null,
      proxy: $('cfgProxy').value.trim(),
      theme: $('cfgTheme').value,
      use_llm: $('cfgUseLlm').checked,
      llm_base_url: $('cfgLlmBase').value.trim(),
      llm_model: $('cfgLlmModel').value.trim(),
      llm_vision_model: $('cfgVisionModel').value.trim(),
      enable_browser: $('cfgBrowser').checked,
      browser_headless: $('cfgHeadless').checked,
      browser_min_interval: Number($('cfgInterval').value || 5),
      browser_max_per_hour: Number($('cfgMaxHour').value || 20),
      browser_platforms: [...$('cfgBrowserPlatforms').querySelectorAll('input[data-bpid]')].filter((i) => i.checked).map((i) => i.dataset.bpid),
      enabled_platforms: [...$('cfgPlatforms').querySelectorAll('input[data-pid]')].filter((i) => i.checked).map((i) => i.dataset.pid)
    };
    if ($('cfgRakuten').value.trim()) payload.rakuten_app_id = $('cfgRakuten').value.trim();
    if ($('cfgRakutenKey').value.trim()) payload.rakuten_access_key = $('cfgRakutenKey').value.trim();
    if ($('cfgLlmKey').value.trim()) payload.llm_api_key = $('cfgLlmKey').value.trim();
    try {
      const r = await api('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      state.cfg = r.config;
      $('cfgRakuten').value = ''; $('cfgRakutenKey').value = ''; $('cfgLlmKey').value = '';
      applyConfig(); await loadSafety();
      closeDrawer(); toast('设置已保存');
      renderKPI(); renderSide();
    } catch (e) { toast('保存失败：' + e.message); }
  }
  function openDrawer() { $('drawerBackdrop').hidden = false; $('settingsDrawer').hidden = false; loadSafety().catch(() => {}); }
  function closeDrawer() { $('drawerBackdrop').hidden = true; $('settingsDrawer').hidden = true; }

  // ---------------------------------------------------------------- vision
  function handleImage(dataUrl) {
    if (!dataUrl) return;
    state.image = dataUrl;
    $('visionPreview').hidden = false;
    $('visionPreview').innerHTML = `<img src="${esc(dataUrl)}" alt="待识别图片"><a id="savedImage" href="#" target="_blank" rel="noopener">下载这张图 ↗</a>`;
    $('visionStatus').textContent = '图片已就绪，点「识别并生成关键词」';
  }
  function readFile(file) {
    if (!file || !file.type.startsWith('image/')) { toast('请选择图片文件'); return; }
    if (file.size > 6 * 1024 * 1024) { toast('图片太大（>6MB），请压缩后重试'); return; }
    const fr = new FileReader();
    fr.onload = () => handleImage(fr.result);
    fr.readAsDataURL(file);
  }
  async function analyzeImage() {
    if (!state.image) { toast('先上传或粘贴一张图片'); return; }
    $('visionStatus').textContent = '识别中…';
    try {
      const v = await api('/api/vision', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: state.image, analyze: $('useModel').checked })
      });
      state.vision = v;
      renderVisionResult(v); renderVisualSites();
      if (v.image_url) $('savedImage').href = v.image_url;
      $('visionStatus').textContent = v.ok ? `识别完成（${v.model}）` : (v.reason || '未识别');
    } catch (e) { $('visionStatus').textContent = '识别失败：' + e.message; }
  }

  // ---------------------------------------------------------------- events
  function bindEvents() {
    $('searchForm').addEventListener('submit', (e) => { e.preventDefault(); startSearchFlow(); });
    $('btnSearch').addEventListener('click', (e) => { e.preventDefault(); startSearchFlow(); });
    $('keyword').addEventListener('input', scheduleTokens);
    $('jpKeyword').addEventListener('input', scheduleTokens);

    // token chips (strip + plan modal)
    document.addEventListener('click', (e) => {
      const b = e.target.closest('.tok[data-tok]');
      if (!b || b.disabled) return;
      const t = b.dataset.tok;
      if (state.dropped.has(t)) state.dropped.delete(t); else state.dropped.add(t);
      renderTokens();
      if (!$('planModal').hidden) schedulePlan();
    });
    $('btnPlan').addEventListener('click', openPlan);
    $('btnClosePlan').addEventListener('click', closePlan);
    $('planBackdrop').addEventListener('click', closePlan);
    $('btnAddQuery').addEventListener('click', () => {
      const row = addPlanRow('自定义', '', true);
      row.querySelector('input[type="text"]').focus();
    });
    $('planQueries').addEventListener('click', (e) => {
      const d = e.target.closest('.pq-del');
      if (d) d.closest('.pq-row').remove();
    });
    $('btnRunPlan').addEventListener('click', () => {
      const rows = [...document.querySelectorAll('#planQueries .pq-row')];
      const qs = rows.filter((r) => r.querySelector('input[type="checkbox"]').checked)
        .map((r) => r.querySelector('input[type="text"]').value.trim())
        .filter(Boolean);
      if (!qs.length) { toast('至少勾选一个查询'); return; }
      const cn = $('planCnQuery').value.trim();
      if (cn) state.cnManualKey = $('keyword').value.trim();
      state.deepMode = $('planDeep').checked;
      localStorage.setItem('ritao-deep', state.deepMode ? '1' : '0');
      $('deepToggle').checked = state.deepMode;
      const skip = $('planSkip').checked;
      state.planConfirm = !skip;
      localStorage.setItem('ritao-plan-confirm', skip ? '0' : '1');
      $('cfgPlanConfirm').checked = !skip;
      closePlan();
      executeSearch([...new Set(qs)], cn);
    });
    $('cfgPlanConfirm').addEventListener('change', (e) => {
      state.planConfirm = e.target.checked;
      localStorage.setItem('ritao-plan-confirm', e.target.checked ? '1' : '0');
    });
    $('cfgUseLlm').addEventListener('change', applyLlmState);
    $('planCnQuery').addEventListener('input', () => {
      state.planCnDirty = true;
      state.cnManualKey = $('keyword').value.trim();
    });
    ['cnShip', 'cnFee', 'cnTariff', 'cnManualPrice'].forEach((id) => {
      $(id).addEventListener('input', () => {
        if (id === 'cnManualPrice') {
          localStorage.setItem('ritao-cn-price', $(id).value);
        } else {
          const key = { cnShip: 'ship', cnFee: 'fee', cnTariff: 'tariff' }[id];
          state.compare[key] = Number($(id).value || 0);
          localStorage.setItem('ritao-' + key, $(id).value);
        }
        renderComparePage();
      });
    });
    $('btnFetchCn').addEventListener('click', fetchCn);

    // --- filters are purely local: no re-search ---
    $('onlyRelevant').addEventListener('change', (e) => { state.onlyRelevant = e.target.checked; renderResults(); });
    $('sortSeg').addEventListener('click', (e) => {
      const b = e.target.closest('[data-sort]'); if (!b) return;
      state.sort = b.dataset.sort;
      $('sortSeg').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      renderResults();
    });
    $('layoutSeg').addEventListener('click', (e) => {
      const b = e.target.closest('[data-layout]'); if (!b) return;
      state.layout = b.dataset.layout;
      $('layoutSeg').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      renderResults();
    });
    $('platformFilter').addEventListener('click', (e) => {
      const b = e.target.closest('[data-plat]'); if (!b) return;
      state.platform = b.dataset.plat; renderResults();
    });

    $('ritaoPrice').addEventListener('input', () => {
      const k = $('kpiRitao'); if (k) k.value = $('ritaoPrice').value;
      updateVerdict(); renderCompare(); scheduleResultRefresh();
    });
    document.addEventListener('input', (e) => {
      if (e.target && e.target.id === 'kpiRitao') {
        $('ritaoPrice').value = e.target.value;
        updateVerdict();
        renderCompare();
        scheduleResultRefresh();
      }
    });

    $('soubaCategory').addEventListener('change', async (e) => {
      const v = e.target.value;
      state.soubaCategory = v === '__auto__' ? 'auto' : (v === '__all__' ? 'all' : v);
      const kw = (state.data && state.data.jp) || $('jpKeyword').value.trim() || $('keyword').value.trim();
      if (!kw) return;
      toast('正在按分类重新统计…');
      try {
        const at = (state.data && state.data.attrs) || {};
        const r = await api('/api/souba?' + new URLSearchParams({
          keyword: kw, category: state.soubaCategory,
          character: at.character || '', series: at.series || '', detail: at.detail || ''
        }));
        if (!state.data) state.data = {};
        state.data.souba = r;
        renderSouba(); renderSoubaMini();
        toast(r.category ? `已按「${r.category.name}」过滤` : '已切换为全部分类');
      } catch (err) { toast('切换失败：' + err.message); }
    });

    $('btnTheme').addEventListener('click', () => {
      const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      setTheme(t); $('cfgTheme').value = t;
    });
    $('btnSettings').addEventListener('click', openDrawer);
    $('btnSettingsTop').addEventListener('click', openDrawer);
    $('btnCloseSettings').addEventListener('click', closeDrawer);
    $('drawerBackdrop').addEventListener('click', closeDrawer);
    $('btnSaveConfig').addEventListener('click', saveCfg);

    $('btnOpenAll').addEventListener('click', () => {
      const urls = [...$('links').querySelectorAll('input[data-open]')].filter((b) => b.checked).map((b) => b.dataset.open);
      if (!urls.length) { toast('先勾选要打开的站点'); return; }
      if (urls.length > 8) toast('一次最多打开 8 个，其余请单独点卡片');
      urls.slice(0, 8).forEach((u) => window.open(u, '_blank', 'noopener'));
    });
    $('btnToggleAll').addEventListener('click', () => {
      const boxes = [...$('links').querySelectorAll('input[data-open]')], allOn = boxes.every((b) => b.checked);
      boxes.forEach((b) => { b.checked = !allOn; allOn ? state.selected.delete(b.dataset.id) : state.selected.add(b.dataset.id); });
    });

    $('queryCard').addEventListener('click', async (e) => {
      const b = e.target.closest('[data-cand]'); if (!b || !state.data) return;
      state.activeCandidate = b.dataset.cand;
      const c = (state.data.candidates || []).find((x) => x.id === state.activeCandidate);
      if (c) $('jpKeyword').value = c.query;
      renderQuery();
      try {
        const r = await api('/api/links?' + new URLSearchParams({ keyword: $('keyword').value.trim(), jp: c ? c.query : '' }));
        state.links = r.links || [];
        state.selected = new Set(state.links.filter((l) => DEFAULT_SELECTED.includes(l.id)).map((l) => l.id));
        renderSites(); renderSouba();
        toast('站点链接已按该候选词重算；要重查请再点「查价格」');
      } catch (err) { toast('链接重算失败：' + err.message); }
    });

    $('recent').addEventListener('click', (e) => {
      const b = e.target.closest('[data-kw]'); if (!b) return;
      $('keyword').value = b.dataset.kw; switchView('price'); executeSearch();
    });
    $('emptyHero').addEventListener('click', (e) => {
      const b = e.target.closest('[data-example]'); if (!b) return;
      $('keyword').value = b.dataset.example;
      $('keyword').focus();
    });
    $('btnPaste').addEventListener('click', async () => {
      try { $('keyword').value = await navigator.clipboard.readText(); loadTokens(); toast('已读取，点「查价格」开始'); }
      catch (e) { toast('浏览器不允许自动读剪贴板，请手动粘贴'); $('keyword').focus(); }
    });
    $('btnTranslate').addEventListener('click', async () => {
      const text = $('keyword').value.trim() || $('jpKeyword').value.trim();
      if (!text) { toast('先填一个商品名'); return; }
      try {
        const r = await api('/api/translate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, target: 'ja' }) });
        if (r.text) { $('jpKeyword').value = r.text; loadTokens(); toast(r.mode === 'llm' ? '已翻译为日文' : (r.note || '已按词典改写')); }
        else toast(r.note || '翻译未成功');
      } catch (e) { toast('翻译失败：' + e.message); }
    });
    $('btnFav').addEventListener('click', async () => {
      const kw = $('keyword').value.trim(); if (!kw) { toast('先填商品名'); return; }
      try {
        const r = await api('/api/watchlist', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'add', keyword: kw, jp_keyword: $('jpKeyword').value.trim(), target_price: Number($('ritaoPrice').value || 0) || null }) });
        state.watch = r.items; renderWatch(); toast('已加入追踪清单');
      } catch (e) { toast('收藏失败：' + e.message); }
    });
    $('btnCopy').addEventListener('click', async () => {
      if (!state.data) { toast('先检索一次再复制摘要'); return; }
      const d = state.data, sb = d.souba || {}, t = sb.stats || {};
      const jpMed = median(jpItems().map((i) => i.price));
      const sbMed = median((sb.items || []).map((i) => i.price)) ?? t['平均価格'];
      const cheapest = [...itemsAll()].filter((i) => i.price).sort((a, b) => toCny(a.price, a.currency) - toCny(b.price, b.currency))[0];
      const ritao = Number($('ritaoPrice').value || 0);
      const cell = $('kpiRow').children[3];
      const verdict = cell ? `结论：${cell.querySelector('.kpi-value').textContent.trim()}（${cell.querySelector('.kpi-note').textContent.trim()}）` : '';
      const text = [
        `【日淘比价】${d.keyword || $('keyword').value}`,
        d.jp ? `日文检索：${d.jp}` : '',
        jpMed != null ? `在售中位：${fmt(jpMed)} 円 ≈ ¥${fmt(jpMed * rate())}（${jpItems().length} 条）` : '在售：暂无报价',
        sbMed != null ? `落札中位：${fmt(sbMed)} 円 ≈ ¥${fmt(sbMed * rate())}（${fmt(sb.count)} 件 / 近 ${sb.days || 120} 天）` : '落札：暂无数据',
        ritao ? `日淘标价：${fmt(ritao)} 円 ≈ ¥${fmt(ritao * rate())}` : '',
        verdict,
        cheapest ? `最低在售：${fmt(cheapest.price)} ${cheapest.currency === 'CNY' ? '元' : '円'} · ${clip(cheapest.title, 40)}\n${cheapest.url}` : ''
      ].filter(Boolean).join('\n');
      try {
        await navigator.clipboard.writeText(text);
        toast('已复制比价摘要');
      } catch (err) {
        const ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); toast('已复制比价摘要'); } catch (e2) { toast('复制失败，请手动选择文本'); }
        ta.remove();
      }
    });
    $('btnExport').addEventListener('click', exportCsv);
    $('btnVideoSearch').addEventListener('click', () => loadVideos(false));
    $('btnMediaSearch').addEventListener('click', loadMedia);
    $('onlyGenuine').addEventListener('change', (e) => {
      state.onlyGenuine = e.target.checked;
      localStorage.setItem('ritao-only-genuine', e.target.checked ? '1' : '0');
      renderResults();
    });
    $('deepToggle').addEventListener('change', (e) => {
      state.deepMode = e.target.checked;
      localStorage.setItem('ritao-deep', state.deepMode ? '1' : '0');
    });
    $('planDeep').addEventListener('change', (e) => {
      state.deepMode = e.target.checked;
      localStorage.setItem('ritao-deep', state.deepMode ? '1' : '0');
      $('deepToggle').checked = state.deepMode;
    });
    $('planVideo').addEventListener('change', (e) => {
      state.planVideo = e.target.checked;
      localStorage.setItem('ritao-plan-video', e.target.checked ? '1' : '0');
    });
    $('watchItems').addEventListener('click', async (e) => {
      const open = e.target.closest('[data-watch-open]'), del = e.target.closest('[data-watch-del]');
      if (open) { $('keyword').value = open.dataset.watchOpen; switchView('price'); executeSearch(); }
      if (del) {
        try {
          const r = await api('/api/watchlist', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'remove', keyword: del.dataset.watchDel }) });
          state.watch = r.items; renderWatch();
        } catch (err) { /* ignore */ }
      }
    });
    $('btnClearKeys').addEventListener('click', async () => {
      try {
        const r = await api('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clear_rakuten: true }) });
        state.cfg = r.config; applyConfig(); toast('本机楽天凭证已清除');
      } catch (e) { toast('清除失败：' + e.message); }
    });
    document.querySelectorAll('[data-login]').forEach((b) => b.addEventListener('click', async () => {
      toast('正在打开登录标签页…');
      try { const r = await api('/api/login?platform=' + b.dataset.login); toast(r.reason || '已打开'); }
      catch (e) { toast('打开失败：' + e.message); }
    }));

    // keyboard
    document.addEventListener('keydown', (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if (e.key === '/' && !typing) { e.preventDefault(); $('keyword').focus(); }
      if (e.key === 'Escape') { closeDrawer(); closePlan(); }
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); startSearchFlow(); }
    });

    // vision
    $('dropZone').addEventListener('click', () => $('fileInput').click());
    $('fileInput').addEventListener('change', (e) => readFile(e.target.files[0]));
    ['dragenter', 'dragover'].forEach((ev) => $('dropZone').addEventListener(ev, (e) => { e.preventDefault(); $('dropZone').classList.add('over'); }));
    ['dragleave', 'drop'].forEach((ev) => $('dropZone').addEventListener(ev, (e) => { e.preventDefault(); $('dropZone').classList.remove('over'); }));
    $('dropZone').addEventListener('drop', (e) => readFile(e.dataTransfer.files[0]));
    document.addEventListener('paste', (e) => {
      const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
      if (item) { readFile(item.getAsFile()); switchView('vision'); }
    });
    $('btnAnalyze').addEventListener('click', analyzeImage);
    $('visionResult').addEventListener('click', (e) => {
      const q = e.target.closest('[data-vq]');
      if (q) { $('jpKeyword').value = q.dataset.vq; toast('已填入日文关键词'); }
      if (e.target.closest('#btnUseVision') && state.vision) {
        const v = state.vision;
        $('keyword').value = v.cn_name || $('keyword').value;
        $('jpKeyword').value = v.jp_name || (v.analysis?.jp_queries || [])[0] || '';
        switchView('price'); executeSearch();
      }
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
