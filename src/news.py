#!/usr/bin/env python
"""news.py — 전략 상위 + 급등락 종목의 한국어 우선 뉴스 수집.

1순위: 구글 뉴스 한국어 RSS (한글 사명 매핑 data/ko_names.json 활용)
2순위: 야후 파이낸스 영어 헤드라인 (한국어 기사가 없는 종목 폴백)
추가:  "미국 증시" 시장 뉴스 상위 4건.

--if-stale H : 기존 출력의 generated_at 이 H시간 이내면 스킵.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "news.json"
PER_TICKER = 3
MAX_TICKERS = 80
MAX_AGE_DAYS = 10
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36"}

try:
    KO = json.loads((DATA / "ko_names.json").read_text())
except Exception:
    KO = {}


def targets(sfx: str = "") -> list[tuple[str, str]]:
    d = json.loads((DATA / f"discover{sfx}.json").read_text())
    names = {r["ticker"]: r.get("name") or r["ticker"] for r in d.get("table", [])}
    seen, out = set(), []

    def add(t):
        if t and t not in seen and t in names:
            seen.add(t)
            out.append((t, names[t]))

    for st in d.get("strategies", []):
        for r in st.get("rows", []):
            add(r["ticker"])
    m = d.get("market") or {}
    for r in (m.get("top_gainers") or []) + (m.get("top_losers") or []):
        add(r["ticker"])
    return out[:MAX_TICKERS]


HANGUL = re.compile(r"[가-힣]")


def is_korean(title: str) -> bool:
    """제목에 한글이 하나도 없으면 한국어 기사가 아니다.

    구글 RSS 를 hl=ko 로 불러도 영문 매체 기사가 섞여 들어온다(실측 19건).
    lang 필드만 믿으면 «국내 언론이 다룬 기사» 라는 화면 설명과 어긋난다.
    """
    return bool(HANGUL.search(title or ""))


def google_rss(query: str, limit: int) -> list[dict]:
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=ko&gl=KR&ceid=KR:ko")
    req = urllib.request.Request(url, headers=UA)
    xml = urllib.request.urlopen(req, timeout=15).read()
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    rows = []
    for it in ET.fromstring(xml).findall(".//item"):
        title, link = it.findtext("title"), it.findtext("link")
        if not title or not link:
            continue
        at = None
        try:
            at = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            pass
        if at and at < cutoff:
            continue
        src = it.find("source")
        # 구글 RSS 제목 끝의 " - 언론사" 꼬리 제거
        pub = src.text if src is not None else None
        if pub and title.endswith(" - " + pub):
            title = title[: -len(" - " + pub)]
        title = title.strip()
        if not is_korean(title):
            continue
        rows.append({"title": title, "publisher": pub, "url": link,
                     "at": at.isoformat() if at else None, "lang": "ko"})
        if len(rows) >= limit:
            break
    return rows


def ko_headlines(ticker: str, name: str, kr_market: bool = False) -> list[dict]:
    if kr_market:
        query = f"{name} 주가"          # 국내 종목은 종목명이 곧 검색어
    else:
        q = KO.get(ticker)
        query = f"{q} 주가" if q else f"{name} stock 주가"
    try:
        return google_rss(query, PER_TICKER)
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=("us", "kr"), default="us")
    ap.add_argument("--if-stale", type=float, metavar="H", default=None)
    args = ap.parse_args()

    global OUT
    kr = args.market == "kr"
    sfx = "_kr" if kr else ""
    OUT = DATA / f"news{sfx}.json"

    if args.if_stale is not None and OUT.exists():
        try:
            gen = datetime.fromisoformat(
                json.loads(OUT.read_text())["generated_at"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
            if 0 <= age < args.if_stale:
                print(f"뉴스가 {age:.1f}h 전 수집됨 (< {args.if_stale}h), 스킵", file=sys.stderr)
                return 0
        except (ValueError, KeyError, json.JSONDecodeError):
            pass

    ts = targets(sfx)
    print(f"[news] {len(ts)}종목 (한국어 우선) 수집", file=sys.stderr)
    by, ko_n = {}, 0
    for i, (t, name) in enumerate(ts):
        rows = ko_headlines(t, name, kr)
        if rows:
            ko_n += 1
            by[t] = rows
        if i % 20 == 19:
            print(f"[news] {i + 1}/{len(ts)} (한국어 {ko_n}종목)", file=sys.stderr)
        time.sleep(0.25)

    market = []
    try:
        market = google_rss("코스피 증시" if kr else "미국 증시", 4)
    except Exception as e:
        print(f"[news] 시장 뉴스 실패: {e}", file=sys.stderr)

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "tickers": len(by), "market": market, "by_ticker": by}
    DATA.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"[news] wrote {OUT} — 한국어 {ko_n}종목 / 시장 {len(market)}건",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
