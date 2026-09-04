#!/usr/bin/env python
"""S&P 500 구성종목 유니버스 수집 스크립트.

위키백과 "List of S&P 500 companies" 문서의 구성종목 표를 읽어
data/universe_sp500.json 으로 저장한다.

- 티커는 Yahoo Finance 형식으로 변환한다 (BRK.B -> BRK-B, BF.B -> BF-B).
- 출력 JSON: {"fetched_at", "source", "count", "rows": [{"ticker","name","sector"}...]}
  (rows 는 ticker 오름차순 정렬, NaN/Infinity 없음, ensure_ascii=False)

이 스크립트는 스케줄러가 자동 실행하지 않는다.
구성종목이 바뀌었을 때(지수 리밸런싱 등) 수동으로 가끔 재실행하는 용도다:

    python src/fetch_universe.py
"""

import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

SOURCE_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "universe_sp500.json"

# 위키백과가 기본 UA 를 차단하는 경우가 있어 브라우저 UA 를 사용한다.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
}

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,6}$")
SPOT_CHECK = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "LLY", "JPM"]


def log(msg: str) -> None:
    """진행 상황을 stderr 로 출력한다 (stdout 은 결과 전용)."""
    print(msg, file=sys.stderr, flush=True)


def to_yahoo_ticker(raw: str) -> str:
    """위키백과 티커를 Yahoo Finance 형식으로 변환한다 (점 -> 하이픈)."""
    return raw.strip().upper().replace(".", "-")


def fetch_table() -> pd.DataFrame:
    """위키백과에서 구성종목 표(첫 번째 wikitable)를 DataFrame 으로 가져온다."""
    log(f"fetching {SOURCE_URL} ...")
    resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    # 구성종목 표는 Symbol/Security/GICS Sector 컬럼을 가진 첫 표다.
    for t in tables:
        cols = [str(c) for c in t.columns]
        if "Symbol" in cols and "Security" in cols and any("GICS Sector" in c for c in cols):
            return t
    raise RuntimeError("구성종목 표를 찾지 못했습니다 (페이지 구조 변경 가능성)")


def build_rows(df: pd.DataFrame) -> list[dict]:
    """DataFrame 에서 ticker/name/sector 행 목록을 만든다."""
    sector_col = next(c for c in df.columns if "GICS Sector" in str(c))
    rows = []
    for _, r in df.iterrows():
        ticker = to_yahoo_ticker(str(r["Symbol"]))
        name = str(r["Security"]).strip()
        sector = str(r[sector_col]).strip()
        if not ticker or not name or not sector:
            raise RuntimeError(f"빈 필드가 있는 행: {r.to_dict()}")
        rows.append({"ticker": ticker, "name": name, "sector": sector})
    rows.sort(key=lambda x: x["ticker"])
    return rows


def validate(rows: list[dict]) -> None:
    """행 수, 섹터 수, 중복/형식, 대표 티커 존재 여부를 검증한다."""
    n = len(rows)
    if not (495 <= n <= 510):
        raise RuntimeError(f"행 수 이상: {n} (기대 495-510)")

    sectors = sorted({r["sector"] for r in rows})
    if len(sectors) != 11:
        raise RuntimeError(f"GICS 섹터 수 이상: {len(sectors)} (기대 11) -> {sectors}")

    tickers = [r["ticker"] for r in rows]
    dupes = sorted({t for t in tickers if tickers.count(t) > 1})
    if dupes:
        raise RuntimeError(f"중복 티커: {dupes}")

    bad = [t for t in tickers if not TICKER_RE.match(t)]
    if bad:
        raise RuntimeError(f"형식 불일치 티커: {bad}")

    tset = set(tickers)
    missing = [t for t in SPOT_CHECK if t not in tset]
    if missing:
        raise RuntimeError(f"대표 티커 누락: {missing}")

    log(f"validate OK: rows={n}, sectors={len(sectors)}, spot-check {len(SPOT_CHECK)}/8")


def main() -> None:
    df = fetch_table()
    rows = build_rows(df)
    validate(rows)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE_URL,
        "count": len(rows),
        "rows": rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    OUT_PATH.write_text(text + "\n", encoding="utf-8")
    log(f"wrote {OUT_PATH} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
