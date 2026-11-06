"""Retrieval quality layer v2.

The old engine picked the "rarest" token from a long Japanese title, which
turned タバコ臭有 (tobacco smell) into the main query and returned nothing.
This version parses the title into a product identity first:

    series / character / brand / category / detail / platform / model

Only those identity terms can become a search query. Everything else
(condition, smell, shop stock codes, scale, PVC, bundle words) is banned.

Relevance is then a hard gate on the identity, not a fuzzy character score:
a listing that does not contain the character (or series, when no character
is known) is not the same product, no matter how similar the title looks.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import dicts

_URL = re.compile(r"https?://\S+")
_BRACKETS = re.compile(r"[#【】\[\]（）()|｜/／,，。.、!！?？~～:：;；+＋]+")
_MODEL = re.compile(r"(?<!\w)(?:[A-Za-z]{1,8}[- ]?\d{1,4}[A-Za-z]{0,3}|\d+/\d+|No\.?\s?\d+|[A-Z]{1,3}\d{2,5})(?!\w)")
_LATIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/]{1,20}")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]+")
_SEP = re.compile(r"[\s\u3000◆◇■□●○▲△▼▽/／|｜,，;；]+")
# Categories where a "character name" makes no sense; the title itself is the
# identity and the platform/model carries the rest.
NON_CHARACTER_CATS = {"ゲーム", "ゲームソフト", "CD", "Blu-ray", "DVD", "アルバム",
                      "シングル", "コミック", "同人誌", "画集", "設定資料集", "トレカ",
                      "ゲーム機", "コントローラー", "カメラ", "レンズ", "イヤホン",
                      "キーボード", "腕時計", "ライトノベル", "小説", "文庫",
                      "単行本", "コミック", "同人誌", "画集", "設定資料集",
                      "Blu-ray", "DVD"}


@dataclass
class Attrs:
    brand: str = ""
    series: str = ""
    character: str = ""
    category: str = ""
    model: str = ""
    detail: str = ""
    condition: str = ""
    edition: str = ""
    platform: str = ""
    series_parts: list[str] = field(default_factory=list)
    must: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    bundle: bool = False
    confidence: str = "low"

    def as_dict(self) -> dict[str, Any]:
        return {
            "brand": self.brand, "series": self.series, "character": self.character,
            "category": self.category, "model": self.model, "detail": self.detail,
            "condition": self.condition, "edition": self.edition, "platform": self.platform,
            "series_parts": self.series_parts[:4], "must": self.must[:4],
            "tokens": self.tokens[:8], "bundle": self.bundle, "confidence": self.confidence,
        }


# ---------------------------------------------------------------- normalise
def normalize(text: str, tier: str = "light") -> str:
    """tier='light' keeps condition/edition words; 'core' strips them too."""
    if not text:
        return ""
    s = _URL.sub(" ", text)
    s = unicodedata.normalize("NFKC", s)
    s = dicts.fold_trad(s)
    for w in dicts.CN_HARD + dicts.JP_HARD:
        s = s.replace(w, " ")
    if tier == "core":
        for w in dicts.CN_SOFT + dicts.JP_SOFT:
            s = s.replace(w, " ")
    s = re.sub(r"\b(?:[¥￥]\s*\d[\d,]*)|\d[\d,]*\s*(?:円|元|JPY|CNY)\b", " ", s, flags=re.I)
    s = _BRACKETS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:160]


def _jp_norm(text: str) -> str:
    """Keep punctuation such as 、 because it is part of series titles."""
    s = unicodedata.normalize("NFKC", text or "")
    s = _URL.sub(" ", s)
    s = re.sub(r"[¥￥]\s*\d[\d,]*|\d[\d,]*\s*(?:円|元|JPY)", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def _key(text: str) -> str:
    """Comparison key: width/kana-insensitive, punctuation-free, JP kanji
    folded to simplified so a Xianyu title can match a JP listing."""
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = s.translate(dicts.CHAR_FOLD)
    s = re.sub(r"[\s\u3000・、。，,\.\-_/／|｜:：;；!！?？'\"“”‘’()（）\[\]【】{}<>《》+＋~～#＃◆◇■□●○▲△▼▽]", "", s)
    return s


def _is_noise_seg(seg: str) -> bool:
    s = seg.strip()
    if not s or re.fullmatch(r"\d+(?:\.\d+)?", s):
        return True
    if re.fullmatch(dicts.SHOP_CODE, s):
        return True
    low = s.lower()
    if any(w.lower() in low for w in dicts.JP_TITLE_NOISE):
        return True
    if any(re.search(p, s, re.I) for p in dicts.JP_SPEC_PATTERNS):
        return True
    return False


def _segments(text: str) -> list[str]:
    s = _jp_norm(text)
    s = re.sub(r"[【\[（(]([^】\]）)]*)[】\]）)]", r" \1 ", s)
    s = re.sub(r"[◆◇■□●○▲△▼▽/／|｜,，;；]+", " ", s)
    return [x.strip() for x in _SEP.split(s) if x.strip()]


def _contains(text: str, term: str) -> bool:
    kt, kw = _key(text), _key(term)
    return bool(kw) and kw in kt


# ---------------------------------------------------------------- dictionaries
def _match_series(cn: str, jp: str = "") -> str:
    cnl = (cn or "").lower()
    for src, dst in sorted(dicts.SERIES_MAP, key=lambda p: -len(p[0])):
        if src.lower() in cnl:
            return dst
    jp_key = _key(jp)
    for name in dicts.JP_SERIES_NAMES:
        if _key(name) in jp_key:
            return name
    return ""


def _match_character(cn: str, jp: str = "") -> str:
    cnl = (cn or "").lower()
    for src, dst in sorted(dicts.CHARACTER_MAP, key=lambda p: -len(p[0])):
        if src.lower() in cnl:
            return dst
    jp_key = _key(jp)
    for name in dicts.JP_CHARACTER_NAMES:
        if _key(name) in jp_key:
            return name
    return ""


BRAND_JP = [
    "グッドスマイル", "マックスファクトリー", "コトブキヤ", "バンダイスピリッツ", "バンダイ",
    "メガハウス", "アルター", "フリュー", "タイトー", "セガ", "アニプレックス", "KADOKAWA",
    "FREEing", "Phat!", "quesQ", "ホビージャパン", "あみあみ", "ミドル", "マイルストン",
    "ウィング", "オーキッドシード", "ネイティブ", "スカイチューブ", "ダイソン", "ソニー",
    "キヤノン", "ニコン", "Apple", "LEGO", "ポケモン", "魂ネイションズ",
]


def _match_brand(cn: str, jp: str = "") -> str:
    cnl = (cn or "").lower()
    for src, dst in sorted(dicts.BRAND_MAP, key=lambda p: -len(p[0])):
        if dst in BRAND_JP and src.lower() in cnl:
            return dst
    for b in sorted(BRAND_JP, key=len, reverse=True):
        if _key(b) in _key(jp):
            return b
    return ""


_CAT_PAIRS = [(a, b) for a, b in dicts.CATEGORY_MAP_V2
              if b not in ("限定", "再販", "未開封", "中古", "未使用")]


def _match_category(cn: str, jp: str = "") -> str:
    # JP side first: it has the marketplace's own wording and is more specific.
    jp_key = _key(jp)
    for term in sorted(dicts.JP_CATEGORY_TERMS, key=len, reverse=True):
        if _key(term) in jp_key:
            return term
    # "CD" is too short for substring matching (it appears inside model codes),
    # so require a standalone token. ブルーレイ is the JP spelling of Blu-ray.
    if re.search(r"(?<![A-Za-z])CD(?![A-Za-z])", jp or "", re.I):
        return "CD"
    if "ブルーレイ" in (jp or ""):
        return "Blu-ray"
    cnl = (cn or "").lower()
    for src, dst in sorted(_CAT_PAIRS, key=lambda p: -len(p[0])):
        if src.lower() in cnl:
            return dst
    return ""


def _match_platform(text: str) -> str:
    k = _key(text)
    for term in sorted(dicts.PLATFORM_TERMS, key=len, reverse=True):
        if _key(term) in k:
            return "PS Vita" if term == "PSV" else term
    return ""


def _match_detail(cn: str, jp: str = "") -> str:
    cnl = (cn or "").lower()
    hits: list[tuple[int, int, str]] = []
    for src, dst in dicts.DETAIL_MAP:
        pos = cnl.find(src.lower())
        if pos >= 0:
            hits.append((pos, len(src), dst))
    # Longest match first at the same position, and never let 圣诞装 and 圣诞
    # both contribute (that produced サンタサンタビキニ).
    hits.sort(key=lambda x: (x[0], -x[1]))
    words: list[str] = []
    end = -1
    for pos, ln, dst in hits:
        if pos < end:
            continue
        words.append(dst)
        end = pos + ln
    # Preserve the order the user wrote: 圣诞+比基尼 -> サンタビキニ.
    if words:
        return "".join(words) if all(re.fullmatch(r"[\u30a0-\u30ff]+", w) for w in words) else " ".join(words)
    return ""


def _edition(text: str) -> str:
    t = text or ""
    for w in dicts.EDITION_WORDS:
        if w in t:
            return w
    return ""


def _condition(text: str) -> str:
    for w in ("未開封", "未使用", "中古", "美品", "限定", "再販", "プライズ", "特典"):
        if w in (text or ""):
            return w
    return ""


# ---------------------------------------------------------------- guessing
def _guess_character(segs: list[str], a: Attrs) -> str:
    cat_idx = None
    for i, s in enumerate(segs):
        if any(_key(t) in _key(s) for t in dicts.JP_CATEGORY_TERMS):
            cat_idx = i
            break
    best: tuple[int, int, str] | None = None
    for i, s in enumerate(segs):
        k = _key(s)
        if not (2 <= len(k) <= 12):
            continue
        if not re.search(r"[\u4e00-\u9fff\u30a0-\u30ff]", s):
            continue
        if _is_noise_seg(s):
            continue
        if any(_key(w) == k or _key(w) in k for w in dicts.GENERIC_TITLE_WORDS):
            continue
        if a.series and _key(a.series) in k:
            continue
        if a.brand and _key(a.brand) in k:
            continue
        if any(_key(t) in k for t in dicts.JP_CATEGORY_TERMS):
            continue
        if k in {_key(x) for x in dicts.JP_DETAIL_WORDS}:
            continue
        score = 0
        if cat_idx is not None and i < cat_idx:
            score += 10 - min(6, cat_idx - i)
        if re.fullmatch(r"[\u30a0-\u30ff]{2,10}", s):
            score += 6
        if re.fullmatch(r"[\u4e00-\u9fff]{2,5}", s):
            score += 4
        if "の" in s or "と" in s or "を" in s:
            score -= 3
        if score < 2:
            continue
        if best is None or score > best[0]:
            best = (score, i, s)
    return best[2] if best else ""


def _guess_series(segs: list[str], a: Attrs) -> str:
    if a.series:
        return a.series
    if not a.character:
        cands = [(i, s) for i, s in enumerate(segs)
                 if not _is_noise_seg(s)
                 and not any(_key(t) in _key(s) for t in dicts.JP_CATEGORY_TERMS)
                 and not (a.platform and _key(a.platform) in _key(s))
                 and len(_key(s)) >= 3]
        if not cands:
            return ""
        # A franchise title almost always comes first; the longest segment is
        # often a format word such as キャラクターソングス.
        idx, best = min(cands, key=lambda x: x[0])
        parts = [best]
        nxt = idx + 1
        if nxt < len(segs):
            s2 = segs[nxt]
            if (not _is_noise_seg(s2)
                    and not any(_key(t) in _key(s2) for t in dicts.JP_CATEGORY_TERMS)
                    and len(_key(s2)) >= 3):
                parts.append(s2)
        return " ".join(parts)
    idx = next((i for i, s in enumerate(segs) if _key(s) == _key(a.character)), -1)
    if idx <= 0:
        return ""
    parts: list[str] = []
    for s in reversed(segs[:idx]):
        if _is_noise_seg(s) or len(_key(s)) < 3:
            continue
        if any(_key(t) in _key(s) for t in dicts.JP_CATEGORY_TERMS):
            continue
        if a.brand and _key(a.brand) in _key(s):
            continue
        parts.append(s)
        if len(parts) >= 2:
            break
    return " ".join(reversed(parts))


def _guess_detail(segs: list[str], a: Attrs) -> str:
    for s in segs:
        for term in sorted(dicts.JP_CATEGORY_TERMS, key=len, reverse=True):
            if _key(term) and _key(term) in _key(s):
                pre = s.split(term)[0]
                pre = re.split(r"(?:de|で|の|×|x|X|ver\.?|Ver\.?|【|】)", pre)[0]
                pre = pre.strip(" ・-_/|、")
                if 3 <= len(pre) <= 14 and not _is_noise_seg(pre):
                    return pre
                break
    return ""


def _fill_from_llm(a: Attrs, hint: dict[str, Any] | None, source_text: str = "") -> None:
    """Accept LLM attributes only when they actually occur in the source and
    are not noise. Models happily return 箱入り娘 / HO-227 as brand/model."""
    if not hint:
        return
    src = _key(source_text)
    for key in ("brand", "series", "character", "category", "model", "detail", "platform"):
        val = str(hint.get(key) or "").strip()
        if not val or getattr(a, key):
            continue
        vk = _key(val)
        if not vk:
            continue
        if key == "model" and (re.fullmatch(dicts.SHOP_CODE, val)
                               or any(re.search(p, val, re.I) for p in dicts.JP_SPEC_PATTERNS)):
            continue
        if any(vk == _key(w) or vk in _key(w) for w in dicts.JP_TITLE_NOISE):
            continue
        if src and vk not in src:
            continue
        setattr(a, key, val)


# ---------------------------------------------------------------- parse
def parse(cn: str, jp: str = "", llm_attrs: dict[str, Any] | None = None) -> Attrs:
    edition = _edition((cn or "") + " " + (jp or ""))
    cn_s = normalize(cn, "light")
    jp_s = _jp_norm(jp)
    a = Attrs()

    a.series = _match_series(cn_s, jp_s)
    a.character = _match_character(cn_s, jp_s)
    a.brand = _match_brand(cn_s, jp_s)
    a.category = _match_category(cn_s, jp_s)
    a.platform = _match_platform(cn_s + " " + jp_s)
    a.detail = _match_detail(cn_s, jp_s)
    a.condition = _condition(cn_s + " " + jp_s)
    a.edition = edition
    a.bundle = any(re.search(p, cn_s + " " + jp_s, re.I) for p in dicts.BUNDLE_PATTERNS)

    segs = _segments(jp_s) if jp_s else []
    if segs:
        if not a.character and a.category not in NON_CHARACTER_CATS:
            guessed = _guess_character(segs, a)
            if guessed:
                a.character = guessed
                a.confidence = "medium"
        if not a.series:
            a.series = _guess_series(segs, a)
        if not a.detail:
            a.detail = _guess_detail(segs, a)
        if not a.category:
            a.category = _match_category("", jp_s)

    # Catalogue numbers belong to the product code, not the series title.
    a.series = re.sub(r"^[#＃]\s*\d{1,5}\s*", "", a.series).strip()

    # Series parts let us match either the full title or a distinctive chunk.
    # A Latin multi-word title (TECH GIAN) must stay whole: splitting it would
    # let "TECH" match unrelated listings.
    if a.series:
        if re.search(r"[A-Za-z]", a.series) and " " in a.series:
            a.series_parts = [a.series]
        else:
            a.series_parts = [p for p in re.split(r"[\s　]+", a.series) if len(_key(p)) >= 3][:4]

    # Electronics / games keep a model code; figure scale numbers are noise.
    if a.category in ("ゲーム", "ゲームソフト", "CD", "Blu-ray", "DVD", "ゲーム機", "コントローラー") or \
            a.category in ("カメラ", "レンズ", "イヤホン", "キーボード", "腕時計"):
        m = _MODEL.search(_jp_norm(jp_s) or cn_s)
        if m and not re.fullmatch(r"\d+/\d+", m.group()):
            a.model = m.group().strip()

    _fill_from_llm(a, llm_attrs, cn_s + " " + jp_s)

    must: list[str] = []
    if a.character:
        must.append(a.character)
        a.confidence = "high" if a.confidence != "medium" else "medium"
    elif a.series:
        must.append(a.series_parts[0] if a.series_parts else a.series)
        a.confidence = "medium" if a.confidence == "low" else a.confidence
    elif a.model:
        must.append(a.model)
    else:
        # Last resort: longest proper nouns from the JP title, then Latin
        # tokens / Chinese runs (covers artist names for CDs and model codes).
        cands = [s for s in segs if not _is_noise_seg(s)
                 and not any(_key(t) in _key(s) for t in dicts.JP_CATEGORY_TERMS)
                 and len(_key(s)) >= 3]
        cands.sort(key=lambda s: len(_key(s)), reverse=True)
        must.extend(cands[:2])
        if not must:
            latin = [t for t in _LATIN.findall(cn_s) if len(t) >= 3]
            if latin:
                must.extend(latin[:1])
            else:
                runs = sorted(re.findall(r"[\u4e00-\u9fff]{2,}", cn_s), key=len, reverse=True)
                must.extend(runs[:1])
    a.must = [m for m in dict.fromkeys(must) if m]
    a.tokens = [_key(x) for x in (segs or _tokens(cn_s)) if x and not _is_noise_seg(x)][:10]
    return a


# ---------------------------------------------------------------- term tools
def rule_translate(text: str, target: str = "ja") -> str:
    """Dictionary-only rewrite for the "AI off" mode.

    Turns a Chinese marketplace title into a Japanese query using the Japanese
    names already present in the dictionaries (series / character / detail /
    brand / model / category / edition). No network, no model, no tokens.
    """
    if not text:
        return ""
    if target != "ja":
        return normalize(text)
    a = parse(text)
    parts: list[str] = []
    for val in (a.series, a.character, a.detail, a.brand, a.model, a.category, a.edition):
        if not val:
            continue
        if all(_key(val) not in _key(p) and _key(p) not in _key(val) for p in parts):
            parts.append(val)
    if parts:
        return " ".join(parts)
    # Nothing recognised: keep the cleaned original so the box is never blank.
    return normalize(text, "core") or normalize(text)


def key_terms(text: str) -> list[tuple[str, float]]:
    """Discriminative terms only. Used for fallback queries and validation."""
    s = _jp_norm(text)
    out: dict[str, float] = {}
    for seg in _segments(s):
        if _is_noise_seg(seg):
            continue
        low = seg.lower()
        for g in dicts.GENERIC_TERMS:
            low = low.replace(g.lower(), " ")
        for sub in re.split(r"[とのへでをがはもや、。・]+", low):
            sub = re.sub(r"\s+", "", sub).strip("・-_~.:：;；!！?？'\"°")
            if len(sub) < 2 or sub.isdigit():
                continue
            if not re.search(r"[\u3040-\u30ff\u3400-\u9fffA-Za-z]{2,}", sub):
                continue
            w = 1.0 if len(sub) >= 6 else (0.8 if len(sub) >= 4 else 0.4)
            out[sub] = max(out.get(sub, 0), w)
    return sorted(out.items(), key=lambda x: -x[1])


def is_usable_query(q: str, attrs: Attrs | dict[str, Any] | None = None) -> bool:
    q = (q or "").strip()
    if not q or len(q) > 70:
        return False
    a = attrs.as_dict() if isinstance(attrs, Attrs) else (attrs or {})
    must = list(a.get("must") or []) + [p for p in (a.get("series_parts") or [])
                                         if len(_key(p)) >= 3]
    if must:
        return any(_contains(q, m) for m in must)
    terms = [t for t, _ in key_terms(q)]
    if len(terms) < 2:
        return False
    cats = {_key(c) for c in dicts.JP_CATEGORY_TERMS}
    return any(_key(t) not in cats for t in terms)


def _tidy_query(q: str, keep_edition: bool = False) -> str:
    s = _jp_norm(q)
    s = re.sub(r"[【\[（(]([^】\]）)]*)[】\]）)]", " ", s)
    noise = dicts.JP_TITLE_NOISE
    if keep_edition:
        noise = [w for w in noise if w not in dicts.EDITION_WORDS]
    for w in noise + dicts.CN_HARD + dicts.JP_HARD:
        s = re.sub(re.escape(w), " ", s, flags=re.I)
    s = re.sub(r"[◆◇■□●○▲△▼▽/／|｜]+", " ", s)
    kept: list[str] = []
    for tok in s.split():
        tok = tok.strip(" ・-_~:：;；,，、!！?？")
        if not tok:
            continue
        if re.fullmatch(dicts.SHOP_CODE, tok):
            continue
        if re.fullmatch(r"\d{1,2}", tok):  # leftovers from 1/6, 1 8
            continue
        if any(re.search(p, tok, re.I) for p in dicts.JP_SPEC_PATTERNS):
            continue
        kept.append(tok)
    s = re.sub(r"\s+", " ", " ".join(kept)).strip(" ・-_~:：;；,，、!！?？")
    return s[:70].rsplit(" ", 1)[0] if len(s) > 70 else s


# ---------------------------------------------------------------- candidates
def build_candidates(cn: str, jp: str = "", llm_queries: list[str] | None = None,
                     llm_attrs: dict[str, Any] | None = None) -> dict[str, Any]:
    a = parse(cn, jp, llm_attrs)
    base = normalize(cn, "light")
    cands: list[dict[str, Any]] = []

    def add(q: str, cid: str, label: str, reason: str, priority: int,
            usable: bool | None = None, keep_edition: bool = False) -> None:
        q = _tidy_query(q, keep_edition)
        if not q:
            return
        if q in {c["query"] for c in cands}:
            return
        ok = is_usable_query(q, a) if usable is None else usable
        cands.append({"id": cid, "label": label, "query": q, "reason": reason,
                      "priority": priority, "usable": ok})

    # Character + detail is the most precise possible query for a variant.
    if a.character and a.detail:
        add(f"{a.character} {a.detail}", "jpquery", "角色 + 特征",
            "角色名 + 款式特征；最精准，优先进 API", 2)
    if a.character and a.category:
        add(f"{a.character} {a.category}", "jpcore", "角色 + 品类",
            "角色名 + 品类；补召回同角色的其他在售", 3)
    if a.character and a.series and _key(a.series) != _key(a.character):
        add(f"{a.series} {a.character}", "jpseries", "作品 + 角色",
            "作品名 + 角色名；补充长标题商品的召回", 4)
    if a.character and a.detail and a.category:
        add(f"{a.character} {a.detail} {a.category}", "jpfull", "角色 + 特征 + 品类",
            "完整短查询，适合煤炉 / 雅虎", 5)

    if not a.character:
        head = a.series_parts[0] if a.series_parts else a.series
        # Edition is part of the identity for 限定版 / 初回版: keep it even when
        # no dictionary knows the franchise (ToHeart2 XRATED 初回限定版).
        ed_head = a.series or (a.must[0] if a.must else "")
        if a.edition and ed_head:
            add(f"{ed_head} {a.edition}", "jpedition", "作品 + 版本",
                "限定版 / 初回版等发行形态；先精确定位这一版", 2, keep_edition=True)
        title_src = jp or cn
        if not (jp or "").strip() and re.search(r"[\u3040-\u30ff]", a.series or ""):
            # The dictionary knows the Japanese title, so the Chinese original
            # is useless on Rakuten / Mercari; keep the JP series candidate
            # instead of outranking it with an unsearchable CN string.
            title_src = ""
        title_q = _tidy_query(title_src, keep_edition=True) if title_src else ""
        if title_q and _key(title_q) != _key(a.series) and len(title_q) >= 4:
            # "<title> <CN category>" adds nothing over the JP candidate
            # ("文学少女 轻小说" vs "文学少女 ライトノベル") and the Chinese
            # category word kills recall on Rakuten, so drop the duplicate.
            core = title_q
            for w in (cn_category_word(a.category), a.category):
                if w:
                    core = re.sub(re.escape(w), " ", core, flags=re.I)
            core = re.sub(r"\s+", " ", core).strip()
            heads = {_key(a.series), _key(head), _key(a.must[0] if a.must else "")}
            if _key(core) not in heads:
                add(title_q, "jptitle", "原标题", "保留作品 + 专辑/版本名，适合 CD / BD / 书籍",
                    2, keep_edition=True)
        if a.platform and head:
            add(f"{a.platform} {head}", "jpplat", "平台 + 作品",
                "游戏 / 影碟按平台 + 作品检索，避免跨平台混入", 2)
        if a.series and a.category:
            add(f"{head} {a.category}", "jpseries", "作品 + 品类",
                "无角色名时用品类 + 作品定位", 2)
            if a.category in dicts.SOFT_CATEGORIES:
                # Books / CDs / BDs often omit the format word; keep a broad
                # series-only candidate so recall does not collapse.
                add(head, "jpname", "作品名", "只留作品名，补召回同系列商品", 3)
        elif a.series:
            add(a.series, "jpseries", "作品名", "仅作品名，召回同系列商品", 3)
        elif a.model:
            add(f"{a.brand} {a.model}".strip(), "jpmodel", "品牌 + 型号",
                "按型号定位", 2)
        elif a.detail and a.category:
            add(f"{a.detail} {a.category}", "jpdetail", "特征 + 品类",
                "无作品信息时按款式特征检索", 4)
        elif a.must and a.category:
            add(f"{a.must[0]} {a.category}", "jpname", "名称 + 品类",
                "只有名称时按名称 + 品类检索", 3)

    # AI queries are a safety net, not the default: the rule queries above are
    # shorter and more predictable, while the model sometimes appends specs
    # such as "1 6" that make Rakuten return HTTP 400. If no rule candidate
    # survives, the AI ones are promoted to the front.
    rule_usable = any(c["usable"] for c in cands)
    for i, q in enumerate((llm_queries or [])[:3]):
        clean = _tidy_query(q)
        if clean and (not a.must or any(_contains(clean, m) for m in a.must)):
            add(clean, f"llm{i + 1}", f"AI 候选 {i + 1}", "模型按日文电商用语生成，已剔除噪声",
                6 if rule_usable else 1)

    # Full original CN title, for the deep-link cards only.
    add(base, "clean", "清洗原标题", "只去掉促销/交易词；用于站内深链，不送 API", 99, usable=False)

    if not cands:
        add(cn.strip(), "clean", "原标题", "原始输入", 99, usable=False)

    # The souba page wants one short, exact query; detail beats category.
    souba_q = ""
    if a.character and a.detail:
        souba_q = f"{a.character} {a.detail}"
    elif a.character and a.category:
        souba_q = f"{a.character} {a.category}"
    elif a.series and a.edition:
        souba_q = f"{a.series} {a.edition}".strip()
    elif a.series:
        souba_q = f"{a.series} {a.category}".strip()
    elif a.must:
        souba_q = f"{a.must[0]} {a.category}".strip() if a.category else a.must[0]

    return {"attrs": a.as_dict(), "candidates": cands[:8], "souba_query": souba_q,
            "needs_jp": not bool(a.must)}


# ---------------------------------------------------------------- judge
def _hits_bundle(title: str, attrs: dict[str, Any]) -> bool:
    if attrs.get("bundle"):
        return False
    return any(re.search(p, title, re.I) for p in dicts.BUNDLE_PATTERNS)


def url_key(url: str) -> str:
    """Identity of a listing URL.

    闲鱼 / 淘宝 use /item?id=123; stripping the query string collapses every
    listing into the same URL and silently reduces 21 results to 1.
    """
    u = (url or "").strip()
    if not u:
        return ""
    try:
        parts = urllib.parse.urlsplit(u)
    except ValueError:
        return u
    ident = ""
    for name in ("id", "itemId", "item_id", "auctionID"):
        vals = urllib.parse.parse_qs(parts.query).get(name)
        if vals and vals[0]:
            ident = f"{name}={vals[0]}"
            break
    base = f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme else parts.path
    if ident:
        return base + "?" + ident
    return u if parts.scheme else base


def judge(title: str, attrs: dict[str, Any] | Attrs | None) -> tuple[int, str]:
    """Return (0-99, reason). 0 means "not the same product".

    The gate is deliberately hard: a different character or a different series
    can never be a match, however similar the rest of the title is.
    """
    a = attrs.as_dict() if isinstance(attrs, Attrs) else (attrs or {})
    t = _key(title)
    if not t:
        return 0, "空标题"
    if _hits_bundle(title, a):
        return 0, "合集/打包"
    cat = a.get("category") or ""
    if cat:
        for bad in dicts.CATEGORY_CONFLICTS.get(cat, []):
            if _key(bad) and _key(bad) in t:
                return 0, "跨品类"
    platform = a.get("platform") or ""
    if platform:
        own = [_key(x) for x in dicts.PLATFORM_ALIASES.get(platform, [])]
        has_own = any(x and x in t for x in own)
        bad = [_key(x) for x in dicts.PLATFORM_CONFLICTS.get(platform, [])]
        if not has_own and any(x and x in t for x in bad):
            return 0, "跨平台"

    char, series, detail = a.get("character") or "", a.get("series") or "", a.get("detail") or ""
    parts = a.get("series_parts") or ([series] if series else [])
    score = 0

    def _any_alias(value: str) -> bool:
        """Match a CN *or* JP spelling. 闲鱼 titles are Chinese while the
        identity extracted from a JP title is Japanese; without aliases every
        legitimate Chinese listing scores 0 and only junk survives."""
        for alias in dicts.aliases_for(value):
            k = _key(alias)
            if len(k) >= 2 and k in t:
                return True
        return False

    if char:
        if not _any_alias(char):
            return 0, "不同角色"
        score = 62
    elif series:
        if not any(len(_key(p)) >= 3 and _any_alias(p) for p in parts):
            return 0, "不同作品"
        score = 56
    else:
        musts = [m for m in (a.get("must") or []) if len(_key(m)) >= 2]
        if not musts:
            return 0, "无法确认同款"
        hits = [m for m in musts if _any_alias(m)]
        if not hits:
            return 0, "无关键词命中"
        score = 46 + min(12, 6 * len(hits))

    if series and any(len(_key(p)) >= 3 and _any_alias(p) for p in parts):
        score += 10
    if detail and _any_alias(detail):
        score += 14
    if cat:
        if _any_alias(cat):
            score += 5
        elif cat not in dicts.SOFT_CATEGORIES:
            # A ねんどろいど query must not be satisfied by a 1/7 scale figure
            # just because the character matches. Keep it visible, but below
            # the actual product type.
            score = max(20, score - 18)
    brand = a.get("brand") or ""
    if brand and _key(brand) in t:
        score += 3
    if char and detail and _any_alias(char) and _any_alias(detail):
        score += 4
    edition = a.get("edition") or ""
    if edition:
        # A listing of the same franchise but the regular edition / a bonus
        # item is not the limited edition being priced.
        if any(w in (title or "") for w in dicts.EDITION_WORDS):
            score += 5
        else:
            score = max(10, score - 30)
    return min(99, score), "ok"


def similarity(title: str, query: str, attrs: dict[str, Any] | None = None) -> float:
    if attrs and (attrs.get("must") or attrs.get("character") or attrs.get("series")):
        s, _ = judge(title, attrs)
        return round(s / 100, 4)
    t, q = _key(title), _key(query)
    if not t or not q:
        return 0.0
    terms = key_terms(query)
    if not terms:
        return min(0.2, difflib.SequenceMatcher(None, t, q).ratio() * 0.4)
    hit = sum(w for term, w in terms if _key(term) in t) / max(0.1, sum(w for _, w in terms))
    return round(min(1.0, 0.7 * hit + 0.15 * difflib.SequenceMatcher(None, t, q).ratio()), 4)


def cn_category_word(jp_category: str) -> str:
    """Japanese category term -> the Chinese word people actually type."""
    if not jp_category:
        return ""
    word = dicts.CATEGORY_CN_WORD.get(jp_category, "")
    if word:
        return word
    for cn, jp in dicts.CATEGORY_MAP_V2:
        if _key(jp) == _key(jp_category):
            return cn
    return ""


def cn_query(cn_keyword: str, attrs: dict[str, Any] | Attrs | None) -> str:
    """A short Chinese query for 闲鱼 / 淘宝.

    Chinese marketplaces do badly with long titles. Sending only the character
    (or series) plus the category keeps recall high and lets the alias-aware
    judge filter the rest.
    """
    a = attrs.as_dict() if isinstance(attrs, Attrs) else (attrs or {})
    text = _key(cn_keyword)
    if not text:
        return ""

    def pick(value: str) -> str:
        for alias in dicts.aliases_for(value):
            k = _key(alias)
            if len(k) >= 2 and k in text:
                return alias
        return ""

    char = pick(a.get("character") or "")
    series = pick(a.get("series") or "")
    cat = pick(a.get("category") or "")
    if not cat:
        cn_cat = cn_category_word(a.get("category") or "")
        if cn_cat and _key(cn_cat) in text:
            cat = cn_cat
    if cat:
        cat = dicts.CATEGORY_CN_WORD.get(cat, cat)
    edition = a.get("edition") or ""
    parts = [x for x in (char or series, cat) if x]
    if edition and not cat and (char or series):
        parts.append(edition)
    if not (char or series):
        # Nothing in the dictionaries matched. Falling back to the bare
        # category ("轻小说") returns every light novel on 闲鱼 — that is the
        # exact complaint we got — so keep the words the user actually typed,
        # minus generic category / condition words.
        core = cn_keyword or ""
        generic = sorted(
            set(list(dicts.CATEGORY_CN_WORD.values())
                + [cn for cn, _ in dicts.CATEGORY_MAP_V2]
                + list(dicts.CN_SOFT) + list(dicts.CN_HARD)),
            key=len, reverse=True)
        for w in generic:
            if len(w) >= 2 and _key(w) != _key(core):
                core = re.sub(re.escape(w), " ", core, flags=re.I)
        words = [w.strip(" ・-_~:：;；,，、!！?？") for w in re.split(r"[\s　]+", core)]
        words = [w for w in words if len(_key(w)) >= 2]
        title_part = " ".join(words)[:40].strip()
        if title_part:
            parts = [title_part] + ([cat] if cat and _key(cat) not in _key(title_part) else [])
        else:
            brand = pick(a.get("brand") or "")
            if brand:
                parts = [brand] + ([cat] if cat else [])
    if not parts:
        runs = [r for r in re.findall(r"[\u4e00-\u9fff]{2,}", cn_keyword or "")
                if len(r) >= 2][:2]
        parts = runs
    return " ".join(dict.fromkeys(parts)).strip()


def assess(title: str, price: float | None = None, currency: str = "JPY",
           median: float | None = None,
           reference_cny: float | None = None) -> dict[str, Any]:
    """Judge listing quality for CN marketplaces (闲鱼 / 淘宝).

    A search for a ¥600 figure on 闲鱼 is dominated by ¥36 祖国版 copies and
    by people selling a single faceplate. Neither is a price reference, so we
    tag them and let the UI hide them by default.
    """
    t = _key(title)
    flags: list[str] = []
    if any(re.search(pat, t, re.I) for pat in dicts.BOOTLEG_PATTERNS):
        flags.append("bootleg")
    parts = any(re.search(pat, t, re.I) for pat in dicts.PARTS_PATTERNS)
    if parts and any(h in t for h in dicts.COMPLETE_HINTS):
        parts = False  # "配件齐全" describes a complete figure
    if parts:
        flags.append("parts")
    if any(w in t for w in dicts.GENUINE_WORDS):
        flags.append("genuine")
    score = 0
    if "genuine" in flags:
        score += 3
    if "bootleg" in flags:
        score -= 6
    if "parts" in flags:
        score -= 5
    if currency == "CNY" and price and median and price > 0 and price < median * 0.38:
        flags.append("cheap")
        score -= 1
    # A ¥29 "正版 GSC" next to a ¥5,250 JP median is a bootleg that simply
    # avoids the word 国产. Compare against the JP reference, not the CN
    # median (which is itself dominated by cheap copies).
    if currency == "CNY" and price and reference_cny and price > 0:
        strong_import = ("日版" in t or "代理版" in t or "官方正品" in t)
        if price < reference_cny * 0.35 and not strong_import:
            flags.append("suspect")
            score -= 5
    if "bootleg" in flags:
        label = "国产/仿品"
    elif "parts" in flags:
        label = "散件/配件"
    elif "suspect" in flags:
        label = "异常低价"
    elif "genuine" in flags:
        label = "正版"
    else:
        label = ""
    return {"flags": flags, "score": score, "label": label,
            "bootleg": "bootleg" in flags, "parts": "parts" in flags,
            "suspect": "suspect" in flags, "genuine": "genuine" in flags}


def rerank(items: list[dict[str, Any]], queries: dict[str, str],
           attrs: dict[str, Any] | None = None, limit: int = 40,
           min_match: int = 0, primary_query: str | None = None) -> list[dict[str, Any]]:
    """Hard identity filter, dedupe by URL, then a soft price-outlier penalty.

    Items judged irrelevant are kept with match=0 so the UI can still reveal
    them when 「只看相关」is switched off; they sort last.
    """
    prices = [float(i["price"]) for i in items
              if i.get("price") and i.get("currency") == "JPY" and i["price"] > 0]
    med = sorted(prices)[len(prices) // 2] if prices else 0.0

    best: dict[str, dict[str, Any]] = {}
    for it in items:
        url = url_key(it.get("url")) or (it.get("title") or "")[:80]
        score, why = judge(it.get("title", ""), attrs or {})
        price = it.get("price")
        if score and med and price and price > 0 and (price > med * 6 or price < med / 6):
            score = max(1, score - 8)
        row = dict(it)
        row["match"] = score
        row["judge"] = why
        row["candidate"] = it.get("candidate", "clean")
        if url not in best or score > best[url]["match"]:
            best[url] = row
    out = sorted(best.values(),
                 key=lambda r: (-(r["match"] + 3 * float(r.get("quality_score") or 0)),
                                r.get("price") or 0))
    if min_match > 0:
        out = [r for r in out if r["match"] >= min_match]
    return out[:limit]


def merge(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for group in groups:
        for it in group:
            key = url_key(it.get("url")) or it.get("title", "")
            if key and key in seen:
                continue
            seen.add(key)
            out.append(it)
    return out


def _tokens(s: str) -> list[str]:
    out = [t.lower() for t in _LATIN.findall(s)]
    for run in _CJK.findall(s):
        if len(run) >= 2:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
        else:
            out.append(run)
    return [t for t in out if t and not t.isdigit()]


# ---------------------------------------------------------------- tokenizer
_SEG_TERMS_CACHE: list[tuple[str, str, str]] | None = None
_LATIN_PHRASES: set[str] | None = None
_SEG_SPLIT = re.compile(r"[\s\u3000◆◇■□●○▲△▼▽/／|｜,，;；()（）\[\]【】{}]+")
_LATIN_ONLY = re.compile(r"^[A-Za-z][A-Za-z0-9&+.\-]*$")


def _seg_dict() -> list[tuple[str, str, str]]:
    global _SEG_TERMS_CACHE, _LATIN_PHRASES
    if _SEG_TERMS_CACHE is not None:
        return _SEG_TERMS_CACHE
    table: dict[str, tuple[str, str]] = {}

    def add(term: str, typ: str, label: str) -> None:
        t = (term or "").strip()
        if 2 <= len(t) <= 32:
            table.setdefault(t, (typ, label))

    for src, dst in dicts.SERIES_MAP:
        add(src, "series", "作品")
        add(dst, "series", "作品")
    for src, dst in dicts.CHARACTER_MAP:
        add(src, "character", "角色")
        add(dst, "character", "角色")
    for src, dst in dicts.BRAND_MAP:
        add(src, "brand", "品牌")
        add(dst, "brand", "品牌")
    for b in BRAND_JP:
        add(b, "brand", "品牌")
    for src, dst in dicts.CATEGORY_MAP_V2:
        if dst not in ("限定", "再販", "未開封", "中古", "未使用"):
            add(src, "category", "品类")
            add(dst, "category", "品类")
    for term in dicts.JP_CATEGORY_TERMS:
        add(term, "category", "品类")
    for src, dst in dicts.DETAIL_MAP:
        add(src, "detail", "特征")
        add(dst, "detail", "特征")
    for w in dicts.JP_TITLE_NOISE + dicts.CN_HARD + dicts.JP_HARD:
        add(w, "noise", "噪声")
    for w in dicts.GENERIC_TERMS:
        add(w, "noise", "噪声")

    _LATIN_PHRASES = {_key(t) for t in table if re.search(r"[A-Za-z]", t) and " " in t}
    _SEG_TERMS_CACHE = sorted(((t, v[0], v[1]) for t, v in table.items()),
                              key=lambda x: -len(x[0]))
    return _SEG_TERMS_CACHE


def _chunks(text: str) -> list[str]:
    raw = [c for c in _SEG_SPLIT.split(_jp_norm(text)) if c.strip()]
    merged: list[str] = []
    i = 0
    phrases = _LATIN_PHRASES or set()
    while i < len(raw):
        a = raw[i]
        if i + 1 < len(raw) and _LATIN_ONLY.match(a) and _LATIN_ONLY.match(raw[i + 1]):
            comb = a + " " + raw[i + 1]
            if _key(comb) in phrases:
                merged.append(comb)
                i += 2
                continue
        merged.append(a)
        i += 1
    return merged


def segment(text: str, lang: str = "jp") -> list[dict[str, Any]]:
    """Dictionary longest-match segmentation for the editable token chips."""
    if not (text or "").strip():
        return []
    _seg_dict()  # populate _LATIN_PHRASES before _chunks()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(tok: str, typ: str, label: str, keep: bool) -> None:
        tok = tok.strip(" ・-_~:：;；,，、!！?？")
        if not tok:
            return
        k = _key(tok) or tok.lower()
        if k in seen:
            return
        seen.add(k)
        out.append({"text": tok, "type": typ, "label": label, "keep": keep})

    for chunk in _chunks(text):
        if re.fullmatch(r"\d+(?:\.\d+)?", chunk) or re.fullmatch(dicts.SHOP_CODE, chunk):
            push(chunk, "noise", "噪声", False)
            continue
        i, buf = 0, ""
        low = chunk.lower()
        while i < len(chunk):
            hit = None
            for term, typ, label in _seg_dict():
                if low.startswith(term.lower(), i):
                    hit = (term, typ, label)
                    break
            if hit:
                if buf:
                    if _is_noise_seg(buf):
                        push(buf, "noise", "噪声", False)
                    else:
                        push(buf, "word", "词", True)
                    buf = ""
                # Keep the user's original casing for display (TECH GIAN,
                # not the lowercase dictionary key).
                matched = chunk[i:i + len(hit[0])]
                push(matched, hit[1], hit[2], hit[1] != "noise")
                i += len(hit[0])
            else:
                buf += chunk[i]
                i += 1
        if buf:
            if _is_noise_seg(buf):
                push(buf, "noise", "噪声", False)
            else:
                push(buf, "word", "词", True)
    return out


def apply_drops(cn: str, jp: str, dropped: list[str] | None) -> tuple[str, str]:
    """Remove user-excluded tokens from both inputs before parsing."""
    def clean(text: str) -> str:
        out = text or ""
        for d in dropped or []:
            d = (d or "").strip()
            if len(d) >= 1:
                out = re.sub(re.escape(d), " ", out, flags=re.I)
        return re.sub(r"\s+", " ", out).strip()
    return clean(cn), clean(jp)
