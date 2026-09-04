#!/usr/bin/env python
"""backtest.py — 가격 기반 전략의 과거 성적을 재현한다.

매월 말, **그 시점까지의 데이터만** 써서 규칙을 적용하고 다음 한 달 수익률을 잰다
(walk-forward). SPY 를 같은 기간 벤치마크로 나란히 둔다.

재무 지표가 필요한 전략(가치·배당·실적·성장·안정)은 과거 재무 이력이 없어 재현할 수 없다.
그런 전략은 backtested=false 로 표시하고 이유를 적는다 — 숨기지 않는다.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "backtest.json"   # main() 에서 시장에 맞게 교체

LOOKBACK = 252     # 200일선·6개월 수익률에 필요한 최소 이력
TOP_N = 20
BENCH = "SPY"                  # 한국 시장은 KODEX 200(069500.KS)


def f(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def wilder_rsi(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def load_prices(tickers: list[str]) -> pd.DataFrame:
    syms = tickers + [BENCH]
    print(f"[backtest] {len(syms)}종목 3년 시세 수집", file=sys.stderr)
    px = yf.download(syms, period="3y", interval="1d", auto_adjust=True,
                     progress=False, group_by="ticker", threads=True)
    cols = {}
    for s in syms:
        try:
            c = px[s]["Close"] if isinstance(px.columns, pd.MultiIndex) else px["Close"]
            c = c.dropna()
            if len(c) > LOOKBACK + 40:
                cols[s] = c
        except Exception:
            continue
    df = pd.DataFrame(cols).sort_index()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    print(f"[backtest] 사용 가능 {len(df.columns)}종목, {df.index[0].date()}~{df.index[-1].date()}",
          file=sys.stderr)
    return df


def month_ends(idx: pd.DatetimeIndex, lookback: int) -> list[pd.Timestamp]:
    """이력이 충분한 구간의 월말 거래일."""
    usable = idx[lookback:]
    s = pd.Series(usable, index=usable)
    return list(s.groupby([usable.year, usable.month]).last())


def pick_momentum(px: pd.DataFrame, t: pd.Timestamp) -> list[str]:
    """잘 오르고 있는 주식 — 200일선 위 · 정배열 · 6개월 상위 25% · RSI 45~78."""
    hist = px.loc[:t]
    if len(hist) < LOOKBACK:
        return []
    last = hist.iloc[-1]
    sma50 = hist.tail(50).mean()
    sma200 = hist.tail(200).mean()
    r6 = hist.iloc[-1] / hist.iloc[-126] - 1
    rsi = wilder_rsi(hist).iloc[-1]

    cand = pd.DataFrame({"last": last, "s50": sma50, "s200": sma200, "r6": r6, "rsi": rsi}).dropna()
    cand = cand.drop(index=[BENCH], errors="ignore")
    if cand.empty:
        return []
    p75 = cand["r6"].quantile(0.75)
    hit = cand[(cand["last"] > cand["s200"]) & (cand["s50"] > cand["s200"]) &
               (cand["r6"] >= p75) & (cand["rsi"].between(45, 78))]
    return list(hit.sort_values("r6", ascending=False).head(TOP_N).index)


def pick_oversold(px: pd.DataFrame, t: pd.Timestamp) -> list[str]:
    """많이 떨어진 우량주 — 가격 조건만 재현(퀄리티 점수는 과거 재무가 없어 제외)."""
    hist = px.loc[:t]
    if len(hist) < LOOKBACK:
        return []
    last = hist.iloc[-1]
    sma200 = hist.tail(200).mean()
    rsi = wilder_rsi(hist).iloc[-1]
    vs200 = (last / sma200 - 1) * 100

    cand = pd.DataFrame({"rsi": rsi, "vs200": vs200}).dropna()
    cand = cand.drop(index=[BENCH], errors="ignore")
    hit = cand[(cand["rsi"] <= 32) & (cand["vs200"] >= -12)]
    return list(hit.sort_values("rsi").head(TOP_N).index)


def pick_signal(px: pd.DataFrame, t: pd.Timestamp) -> list[str]:
    """매매 신호가 '매수 구간'이라 부른 종목 — 화면 규칙(discover.signal_for)과 같다.

    상위 N개로 자르지 않는다. 재는 질문이 '매수 구간이라 부른 종목들이
    다음 달 지수보다 나았나'이므로, 부른 것을 전부 같은 금액으로 담는다.
    """
    hist = px.loc[:t]
    if len(hist) < LOOKBACK:
        return []
    cand = pd.DataFrame({
        "px": hist.iloc[-1],
        "s20": hist.tail(20).mean(),
        "s50": hist.tail(50).mean(),
        "s200": hist.tail(200).mean(),
        "rsi": wilder_rsi(hist).iloc[-1],
    }).dropna().drop(index=[BENCH], errors="ignore")
    hit = cand[(cand["px"] >= cand["s50"]) & (cand["px"] > cand["s200"]) &
               (cand["px"] > cand["s20"]) & (cand["rsi"] < 70)]
    return list(hit.index)


def pick_all(px: pd.DataFrame, t: pd.Timestamp) -> list[str]:
    """대조군 — 신호를 안 보고 그냥 전 종목을 들고 있었을 때.

    타이밍 규칙의 값어치는 지수 대비가 아니라 '같은 종목을 그냥 들고 있기' 대비로 봐야
    드러난다. 신호가 지수를 이겨도 그냥 들고 있는 것보다 못하면 규칙이 손해를 낸 것이다.
    """
    hist = px.loc[:t]
    if len(hist) < LOOKBACK:
        return []
    live = hist.iloc[-1].dropna()
    return [s for s in live.index if s != BENCH]


def run(px: pd.DataFrame, picker, label: str) -> dict | None:
    ends = month_ends(px.index, LOOKBACK)
    if len(ends) < 4:
        return None
    rows = []
    for a, b in zip(ends[:-1], ends[1:]):
        names = picker(px, a)
        if not names:
            continue
        seg = px.loc[[a, b]]
        rets = (seg.loc[b, names] / seg.loc[a, names] - 1) * 100
        rets = rets.dropna()
        if rets.empty:
            continue
        bench = (px.loc[b, BENCH] / px.loc[a, BENCH] - 1) * 100
        rows.append({"month": b.strftime("%Y-%m"), "n": int(len(rets)),
                     "ret": f(rets.mean()), "bench": f(bench)})
    if len(rows) < 3:
        return None

    r = np.array([x["ret"] for x in rows], float)
    bch = np.array([x["bench"] for x in rows], float)
    cum = float(np.prod(1 + r / 100) - 1) * 100
    cumb = float(np.prod(1 + bch / 100) - 1) * 100
    print(f"[backtest] {label}: {len(rows)}개월 평균 {r.mean():+.2f}% "
          f"(SPY {bch.mean():+.2f}%), 승률 {(r > bch).mean() * 100:.0f}%", file=sys.stderr)
    return {
        "backtested": True,
        "months": len(rows),
        "avg_monthly": f(r.mean()),
        "bench_avg_monthly": f(bch.mean()),
        "win_rate": f((r > bch).mean() * 100, 1),
        "best": f(r.max()), "worst": f(r.min()),
        "cumulative": f(cum, 1), "bench_cumulative": f(cumb, 1),
        "monthly": rows,
    }


NOT_BACKTESTED = {
    "value_quality": "과거 시점의 PER·ROE 이력이 없어 그때 무엇을 골랐을지 재현할 수 없습니다.",
    "earnings": "과거 실적 서프라이즈 이력이 없어 재현할 수 없습니다.",
    "growth": "과거 매출·이익 증가율 이력이 없어 재현할 수 없습니다.",
    "dividend": "과거 배당수익률 이력이 없어 재현할 수 없습니다.",
    "stable": "과거 시가총액·수익성 이력이 없어 재현할 수 없습니다.",
}

CAVEATS = [
    "지금 시점의 구성 종목만 사용했습니다. 그동안 목록에서 빠진 회사는 빠져 있어, 실제보다 좋게 나올 수 있습니다(생존 편향).",
    "수수료·세금·슬리피지를 넣지 않았습니다.",
    "매월 말에 그 기준에 걸린 종목을 같은 금액으로 사서 한 달 뒤 전부 파는 것으로 계산했습니다. "
    "카테고리는 상위 20종목, 매매 신호는 매수 구간에 든 종목 전부입니다.",
    "과거 성적이 앞으로를 보장하지 않습니다.",
]


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="가격 기반 전략 과거 성적")
    ap.add_argument("--market", choices=("us", "kr"), default="us")
    opts = ap.parse_args()

    global OUT, BENCH
    kr = opts.market == "kr"
    OUT = DATA / ("backtest_kr.json" if kr else "backtest.json")
    BENCH = "069500.KS" if kr else "SPY"      # 코스피200 ETF
    uni = "universe_kr.json" if kr else "universe_sp500.json"

    tickers = [r["ticker"] for r in json.loads((DATA / uni).read_text())["rows"]]
    px = load_prices(tickers)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": "코스피 200" if kr else "S&P 500",
        "top_n": TOP_N,
        "caveats": CAVEATS,
        "strategies": {},
    }
    for key, picker, label in (("momentum", pick_momentum, "잘 오르고 있는 주식"),
                               ("oversold", pick_oversold, "많이 떨어진 우량주")):
        res = run(px, picker, label)
        if res:
            if key == "oversold":
                res["partial"] = "가격 조건만 재현했습니다. 실제 화면은 수익성 조건이 하나 더 붙습니다."
            out["strategies"][key] = res

    sig = run(px, pick_signal, "매매 신호 (매수 구간)")
    if sig:
        hold = run(px, pick_all, "대조군 (그냥 다 들고 있기)")
        if hold:
            sig["hold_all_avg_monthly"] = hold["avg_monthly"]
            sig["hold_all_cumulative"] = hold["cumulative"]
        out["signal"] = sig

    for key, why in NOT_BACKTESTED.items():
        out["strategies"][key] = {"backtested": False, "reason": why}

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"[backtest] wrote {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
