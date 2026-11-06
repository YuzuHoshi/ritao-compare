"""Local price history. The reliable way to get "historical prices" for personal use.

Every query is persisted, so the more you use the tool, the better the price
curve for the items you actually care about becomes. No third-party DB needed.
"""
from __future__ import annotations

import sqlite3
import statistics
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "data" / "prices.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init() -> None:
    with _connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                platform TEXT NOT NULL,
                title TEXT,
                price REAL,
                currency TEXT DEFAULT 'JPY',
                url TEXT,
                source TEXT,
                created_at REAL NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_kw_time ON snapshots(keyword, created_at)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                jp_keyword TEXT,
                note TEXT,
                created_at REAL NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL UNIQUE,
                jp_keyword TEXT,
                target_price REAL,
                note TEXT,
                created_at REAL NOT NULL
            )
        """)


def log_query(keyword: str, jp_keyword: str = "", note: str = "") -> None:
    with _connect() as con:
        con.execute("INSERT INTO queries(keyword, jp_keyword, note, created_at) VALUES(?,?,?,?)",
                    (keyword, jp_keyword, note, time.time()))


def save_prices(keyword: str, items: list[dict[str, Any]]) -> int:
    """Store only priced results; returns how many rows were written."""
    now = time.time()
    rows = []
    for it in items:
        price = it.get("price")
        if price is None or price <= 0:
            continue
        rows.append((keyword, it.get("platform") or "", (it.get("title") or "")[:200],
                     float(price), it.get("currency", "JPY"), it.get("url") or "",
                     it.get("source") or "", now))
    if not rows:
        return 0
    with _connect() as con:
        con.executemany("""INSERT INTO snapshots
            (keyword, platform, title, price, currency, url, source, created_at)
            VALUES(?,?,?,?,?,?,?,?)""", rows)
    return len(rows)


def history(keyword: str, limit: int = 600) -> list[dict[str, Any]]:
    with _connect() as con:
        rows = con.execute("""SELECT platform, price, currency, created_at, title
            FROM snapshots WHERE keyword=? ORDER BY created_at ASC LIMIT ?""",
            (keyword, limit)).fetchall()
    return [dict(r) for r in rows]


def recent_queries(limit: int = 12) -> list[str]:
    with _connect() as con:
        rows = con.execute("""SELECT keyword FROM queries GROUP BY keyword
            ORDER BY MAX(created_at) DESC LIMIT ?""", (limit,)).fetchall()
    return [r["keyword"] for r in rows]


def watch_list() -> list[dict[str, Any]]:
    with _connect() as con:
        rows = con.execute("""SELECT keyword, jp_keyword, target_price, note, created_at
            FROM watchlist ORDER BY created_at DESC""").fetchall()
    return [dict(r) for r in rows]


def watch_add(keyword: str, jp_keyword: str = "", target_price: float | None = None,
              note: str = "") -> None:
    with _connect() as con:
        con.execute("""INSERT INTO watchlist(keyword, jp_keyword, target_price, note, created_at)
            VALUES(?,?,?,?,?) ON CONFLICT(keyword) DO UPDATE SET
            jp_keyword=excluded.jp_keyword, target_price=excluded.target_price,
            note=excluded.note""",
                    (keyword, jp_keyword, target_price, note, time.time()))


def watch_remove(keyword: str) -> None:
    with _connect() as con:
        con.execute("DELETE FROM watchlist WHERE keyword=?", (keyword,))


def export_csv(keyword: str) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["时间", "平台", "商品名", "价格", "币种", "来源", "链接"])
    with _connect() as con:
        rows = con.execute("""SELECT created_at, platform, title, price, currency, source, url
            FROM snapshots WHERE keyword=? ORDER BY created_at DESC""", (keyword,)).fetchall()
    for r in rows:
        w.writerow([time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"])),
                    r["platform"], r["title"], r["price"], r["currency"], r["source"], r["url"]])
    return "\ufeff" + buf.getvalue()  # BOM for Excel on Windows


def summary(keyword: str) -> dict[str, Any]:
    rows = history(keyword)
    by_platform: dict[str, list[float]] = {}
    for r in rows:
        by_platform.setdefault(r["platform"], []).append(r["price"])
    out = {}
    for name, values in by_platform.items():
        out[name] = {
            "count": len(values), "min": min(values),
            "median": round(statistics.median(values), 2),
            "max": max(values),
            "latest": values[-1] if values else None,
        }
    return {"total": len(rows), "platforms": out}
