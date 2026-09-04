"""S&P 500 유니버스 기술적 지표 일괄 계산기.

data/universe_sp500.json 의 티커 전체를 yfinance 벌크 다운로드(1회 HTTP 배치)로
1년 일봉을 받아, 종목별 수익률·RSI·이동평균·52주 위치·거래량 스파이크·변동성을
계산해 data/technicals.json 으로 출력한다.

투자 권유가 아니며, 공개 시세 기반 파생 지표이다.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = ROOT / "data" / "universe_sp500.json"   # main() 에서 시장에 맞게 교체
OUT_PATH = ROOT / "data" / "technicals.json"
# 시장별 경로. --market kr 이면 한국 유니버스와 *_kr.json 산출물을 쓴다.
MARKETS = {
    "us": {"universe": "universe_sp500.json", "suffix": ""},
    "kr": {"universe": "universe_kr.json", "suffix": "_kr"},
}


def market_paths(market: str):
    m = MARKETS[market]
    return (ROOT / "data" / m["universe"]), m["suffix"]

MIN_DAYS = 120          # 이보다 짧은 이력은 스킵
TRADING_DAYS = 252      # 연환산 기준 거래일
VOL_AVG_DAYS = 63       # 거래량 스파이크 비교 구간(3개월)


def f(x, nd=None):
    """NaN/None/inf 를 None 으로 정규화."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, nd) if nd is not None else v


def pct(new, old, nd=2):
    """old 대비 new 의 변화율(%). 분모가 없거나 0이면 None."""
    if new is None or old is None or old == 0:
        return None
    return f((new / old - 1) * 100, nd)


def ret_over(close: pd.Series, days: int, nd=2):
    """days 거래일 전 종가 대비 수익률(%). 이력이 짧으면 None."""
    if len(close) <= days:
        return None
    return pct(float(close.iloc[-1]), float(close.iloc[-1 - days]), nd)


def wilder_rsi(close: pd.Series, period: int = 14):
    """Wilder RSI (ewm alpha=1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_gain = float(gain.iloc[-1])
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        return 100.0 if last_gain > 0 else None
    rs = last_gain / last_loss
    return 100 - 100 / (1 + rs)


def load_universe():
    with open(UNIVERSE_PATH, encoding="utf-8") as fp:
        data = json.load(fp)
    return [row["ticker"] for row in data["rows"]]


def compute_row(px: pd.DataFrame):
    """단일 종목 OHLCV DataFrame -> 지표 dict. 이력 부족 시 None."""
    close = px["Close"].dropna()
    if len(close) < MIN_DAYS:
        return None

    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])

    # 52주 범위와 그 안에서의 현재가 위치(0=저점, 100=고점)
    hi52 = float(close.max())
    lo52 = float(close.min())
    pos52w = ((last - lo52) / (hi52 - lo52) * 100) if hi52 > lo52 else None

    # 이동평균
    sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    # 거래량 스파이크: 오늘 거래량 / 3개월 평균 거래량
    vol = px["Volume"].reindex(close.index).dropna()
    vol_spike = None
    if len(vol) >= VOL_AVG_DAYS:
        avg3m = float(vol.iloc[-VOL_AVG_DAYS:].mean())
        if avg3m > 0:
            vol_spike = float(vol.iloc[-1]) / avg3m

    # 연환산 변동성: 1년 일간수익률 표준편차 * sqrt(252) * 100
    daily_ret = close.pct_change().dropna()
    vol_ann = float(daily_ret.std() * math.sqrt(TRADING_DAYS) * 100) if len(daily_ret) > 1 else None

    return {
        "last": f(last, 2),
        "prev_close": f(prev, 2),
        "day_pct": pct(last, prev),
        "ret_1w": ret_over(close, 5),
        "ret_1m": ret_over(close, 21),
        "ret_3m": ret_over(close, 63),
        "ret_6m": ret_over(close, 126),
        "ret_12m": ret_over(close, min(TRADING_DAYS, len(close) - 1)),
        "rsi14": f(wilder_rsi(close), 1),
        "sma20": f(sma20, 2),
        "sma50": f(sma50, 2),
        "sma200": f(sma200, 2),
        "vs_sma50": pct(last, sma50),
        "vs_sma200": pct(last, sma200),
        "hi52": f(hi52, 2),
        "lo52": f(lo52, 2),
        "pos52w": f(pos52w, 1),
        "vol_spike": f(vol_spike, 2),
        "vol_ann": f(vol_ann, 1),
    }


def main():
    global UNIVERSE_PATH, OUT_PATH
    ap = argparse.ArgumentParser(description="기술적 지표 수집")
    ap.add_argument("--market", choices=("us", "kr"), default="us")
    args = ap.parse_args()
    UNIVERSE_PATH, sfx = market_paths(args.market)
    OUT_PATH = ROOT / "data" / f"technicals{sfx}.json"

    t0 = time.time()
    tickers = load_universe()
    print(f"[technicals] universe: {len(tickers)} tickers", file=sys.stderr)

    # 야후 심볼 표기(BRK.B -> BRK-B). 출력 키는 유니버스 원본 티커 유지.
    # 미국 티커의 점만 하이픈으로 바꾼다 (BRK.B → BRK-B).
    # 한국 티커의 점은 거래소 접미사이므로 건드리면 안 된다 (005930.KS).
    def to_yahoo(t: str) -> str:
        return t if t.endswith((".KS", ".KQ")) else t.replace(".", "-")

    yahoo_map = {t: to_yahoo(t) for t in tickers}
    symbols = list(yahoo_map.values())

    print(f"[technicals] bulk download 1y daily ({len(symbols)} symbols) ...", file=sys.stderr)
    hist = yf.download(symbols, period="1y", interval="1d", auto_adjust=True,
                       progress=False, group_by="ticker", threads=True)
    print(f"[technicals] download done in {time.time() - t0:.1f}s", file=sys.stderr)

    rows, skipped = {}, []
    for t in tickers:
        sym = yahoo_map[t]
        try:
            px = hist[sym] if isinstance(hist.columns, pd.MultiIndex) else hist
            px = px.dropna(how="all")
        except Exception:
            px = pd.DataFrame()
        if px.empty or "Close" not in px.columns:
            skipped.append(t)
            continue
        row = compute_row(px)
        if row is None:
            skipped.append(t)
            continue
        close3m = px["Close"].dropna().tail(63)
        if len(close3m) >= 10:
            step = max(1, len(close3m) // 21)
            samp = close3m.iloc[::step].tolist()
            if close3m.iloc[-1] != samp[-1]:
                samp.append(float(close3m.iloc[-1]))
            base = samp[0]
            row["spark"] = [f(v / base * 100, 1) for v in samp] if base else None
        else:
            row["spark"] = None
        rows[t] = row

    floor = 300 if args.market == "us" else 80
    if len(rows) < floor:
        print(f"오류: 처리된 종목 {len(rows)}개 (<{floor}) — 수집 실패로 간주", file=sys.stderr)
        sys.exit(1)

    fx = None
    try:
        fxh = yf.Ticker("KRW=X").history(period="5d")["Close"].dropna()
        if len(fxh) >= 2:
            fx = {"rate": f(float(fxh.iloc[-1]), 2), "prev": f(float(fxh.iloc[-2]), 2),
                  "day_pct": f((float(fxh.iloc[-1]) / float(fxh.iloc[-2]) - 1) * 100, 2)}
    except Exception as e:
        print(f"[technicals] fx 수집 실패: {e}", file=sys.stderr)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fx": fx,
        "count": len(rows),
        "skipped": skipped,
        "rows": rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    elapsed = time.time() - t0
    print(f"[technicals] wrote {OUT_PATH} rows={len(rows)} "
          f"skipped={len(skipped)} elapsed={elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
