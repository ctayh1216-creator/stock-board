#!/usr/bin/env python
"""live.py — 장중 실시간 시세만 뽑는 경량 스크립트.

전체 재계산(discover.py) 없이 현재가/일간등락만 data/live.json 으로 저장한다.
파일이 작아야 자주 커밋해도 저장소가 붓지 않으므로 압축 JSON으로 쓴다.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "live.json"


def r2(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=("us", "kr"), default="us")
    args = ap.parse_args()
    uni = "universe_kr.json" if args.market == "kr" else "universe_sp500.json"
    out = DATA / ("live_kr.json" if args.market == "kr" else "live.json")

    t0 = time.time()
    syms = [r["ticker"] for r in json.loads((DATA / uni).read_text())["rows"]]
    px = yf.download(syms, period="2d", interval="1d", auto_adjust=True,
                     progress=False, group_by="ticker", threads=True)

    rows = {}
    for s in syms:
        try:
            c = (px[s]["Close"] if isinstance(px.columns, pd.MultiIndex) else px["Close"]).dropna()
        except Exception:
            continue
        if len(c) < 1:
            continue
        last = r2(c.iloc[-1])
        day = r2((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2) if len(c) > 1 else None
        if last is not None:
            rows[s] = [last, day]

    floor = 300 if args.market == "us" else 80
    if len(rows) < floor:
        print(f"오류: 시세 {len(rows)}건 (<{floor}) — 실패로 간주", file=sys.stderr)
        return 1

    fx = None
    try:
        if args.market != "us":
            raise StopIteration
        fxh = yf.Ticker("KRW=X").history(period="2d")["Close"].dropna()
        if len(fxh) >= 1:
            fx = {"rate": r2(fxh.iloc[-1]),
                  "day_pct": r2((fxh.iloc[-1] / fxh.iloc[-2] - 1) * 100, 2) if len(fxh) > 1 else None}
    except (Exception, StopIteration):
        pass

    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "count": len(rows), "fx": fx, "rows": rows}
    DATA.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"[live:{args.market}] {len(rows)}종목 {time.time() - t0:.1f}s → {out.stat().st_size / 1024:.0f}KB",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
