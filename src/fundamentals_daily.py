"""S&P 500 일일 펀더멘털 수집기.

data/universe_sp500.json 의 전 종목에 대해 yfinance t.info 로
밸류에이션·수익성·성장·애널리스트 지표를 수집하고,
시가총액 상위 200 종목에 한해 다음 실적발표일(t.calendar)과
최근 어닝 서프라이즈 이력(t.earnings_history)을 추가로 수집해
data/fundamentals.json 으로 출력한다.

사용법:
    python src/fundamentals_daily.py              # 전체 재수집
    python src/fundamentals_daily.py --if-stale 12  # 출력이 12시간 이내면 스킵

투자 권유가 아니며, 공개 데이터 기반 참고 자료이다.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import warnings
from datetime import datetime, timezone, date
from pathlib import Path

import yfinance as yf

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = ROOT / "data" / "universe_sp500.json"
OUT_PATH = ROOT / "data" / "fundamentals.json"

INFO_THROTTLE_SEC = 0.35   # t.info 호출 간 대기
EXTRA_THROTTLE_SEC = 0.35  # 추가 호출 간 대기
# 실적 일정·서프라이즈를 수집할 시총 상위 종목 수. 0 이면 전 종목.
# 200 으로 두던 시절엔 «실적 데이터가 없는 종목»이 절반이었는데, 없던 게 아니라
# 우리가 안 물어본 것이었다. 어닝 기준 카테고리를 만들면서 전 종목으로 넓혔다.
TOP_N_EXTRA = 0


def f(x):
    """NaN/Inf/변환불가 값을 None 으로 바꾸는 안전 변환 헬퍼."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def null_row() -> dict:
    """수집 실패 종목용 전체 null 행."""
    return {
        "mcap": None, "pe_fwd": None, "peg": None, "ps": None,
        "roe": None, "margin": None, "rev_g": None, "eps_g": None,
        "fcf": None, "upside": None, "consensus": None, "n_analysts": None,
        "next_earnings": None, "surprise_last": None, "beats4": None,
        "eps_hist": None,
        "div_yield": None, "beta": None, "debt_eq": None,
    }


def row_from_info(info: dict) -> dict:
    """t.info 딕셔너리에서 기본 지표 행을 만든다(추가 필드는 null)."""
    row = null_row()
    row["mcap"] = f(info.get("marketCap"))
    row["pe_fwd"] = f(info.get("forwardPE"))
    row["peg"] = f(info.get("trailingPegRatio"))
    if row["peg"] is None:
        row["peg"] = f(info.get("pegRatio"))
    row["ps"] = f(info.get("priceToSalesTrailing12Months"))

    roe = f(info.get("returnOnEquity"))
    row["roe"] = roe * 100 if roe is not None else None
    margin = f(info.get("profitMargins"))
    row["margin"] = margin * 100 if margin is not None else None
    rev_g = f(info.get("revenueGrowth"))
    row["rev_g"] = rev_g * 100 if rev_g is not None else None
    eps_g = f(info.get("earningsGrowth"))
    row["eps_g"] = eps_g * 100 if eps_g is not None else None

    row["fcf"] = f(info.get("freeCashflow"))

    target = f(info.get("targetMeanPrice"))
    price = f(info.get("currentPrice"))
    if target is not None and price is not None and price > 0:
        row["upside"] = (target / price - 1) * 100

    # yfinance 1.7 은 dividendYield 를 퍼센트 단위로 준다 (KO 2.4 = 2.4%).
    row["div_yield"] = f(info.get("dividendYield"))
    row["beta"] = f(info.get("beta"))
    row["debt_eq"] = f(info.get("debtToEquity"))

    key = info.get("recommendationKey")
    row["consensus"] = key if isinstance(key, str) and key else None
    n = f(info.get("numberOfAnalystOpinions"))
    row["n_analysts"] = int(n) if n is not None else None
    return row


def fetch_info(ticker: str) -> dict | None:
    """t.info 를 1회 재시도 포함으로 가져온다. 실패 시 None."""
    for attempt in (1, 2):
        try:
            info = yf.Ticker(ticker).info
            if isinstance(info, dict) and info:
                return info
        except Exception as e:  # noqa: BLE001
            print(f"    {ticker}: info 시도 {attempt} 실패 ({type(e).__name__})",
                  file=sys.stderr)
        if attempt == 1:
            time.sleep(1.0)
    return None


def fetch_extras(ticker: str) -> dict:
    """시총 상위 종목용 추가 지표: 다음 실적발표일 + 서프라이즈 이력."""
    out = {"next_earnings": None, "surprise_last": None, "beats4": None,
           "eps_hist": None}
    t = yf.Ticker(ticker)

    # 다음 실적발표일 -----------------------------------------------------
    for attempt in (1, 2):
        try:
            cal = t.calendar
            dates = (cal or {}).get("Earnings Date") or []
            today = date.today()
            upcoming = sorted(d for d in dates if isinstance(d, date))
            if upcoming:
                nxt = next((d for d in upcoming if d >= today), upcoming[0])
                out["next_earnings"] = nxt.isoformat()
            break
        except Exception as e:  # noqa: BLE001
            print(f"    {ticker}: calendar 시도 {attempt} 실패 ({type(e).__name__})",
                  file=sys.stderr)
            if attempt == 1:
                time.sleep(1.0)

    # 어닝 서프라이즈 이력 -------------------------------------------------
    for attempt in (1, 2):
        try:
            eh = t.earnings_history
            if eh is not None and len(eh) > 0 and "surprisePercent" in eh.columns:
                last4 = eh.tail(4)
                sp = f(last4["surprisePercent"].iloc[-1])
                out["surprise_last"] = sp * 100 if sp is not None else None
                beats = 0
                for v in last4["surprisePercent"]:
                    fv = f(v)
                    if fv is not None and fv > 0:
                        beats += 1
                out["beats4"] = beats
                # 분기별 예상·실제까지 남긴다. 같은 호출로 이미 받아온 값인데
                # 요약 두 개만 남기고 버리고 있었다 — 상세 시트에서 표로 편다.
                hist = []
                for q, row in last4.iterrows():
                    est, act = f(row.get("epsEstimate")), f(row.get("epsActual"))
                    if est is None and act is None:
                        continue
                    pct = f(row.get("surprisePercent"))
                    hist.append({
                        "q": str(q)[:10],
                        "est": round(est, 3) if est is not None else None,
                        "act": round(act, 3) if act is not None else None,
                        "pct": round(pct * 100, 1) if pct is not None else None,
                    })
                out["eps_hist"] = hist or None
            break
        except Exception as e:  # noqa: BLE001
            print(f"    {ticker}: earnings_history 시도 {attempt} 실패 ({type(e).__name__})",
                  file=sys.stderr)
            if attempt == 1:
                time.sleep(1.0)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="S&P 500 일일 펀더멘털 수집")
    ap.add_argument("--market", choices=("us", "kr"), default="us")
    ap.add_argument("--if-stale", type=float, metavar="H", default=None,
                    help="출력 파일이 H시간 이내면 재수집 없이 종료")
    args = ap.parse_args()

    global UNIVERSE_PATH, OUT_PATH
    if args.market == "kr":
        UNIVERSE_PATH = ROOT / "data" / "universe_kr.json"
        OUT_PATH = ROOT / "data" / "fundamentals_kr.json"

    if args.if_stale is not None and OUT_PATH.exists():
        # mtime은 git checkout이 리셋하므로 파일 내용의 generated_at 기준으로 판정한다.
        try:
            prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            gen = datetime.fromisoformat(prev["generated_at"].replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
        except (ValueError, KeyError, json.JSONDecodeError):
            age_h = None  # 파싱 실패 -> 재수집
        if age_h is not None and 0 <= age_h < args.if_stale:
            print(f"출력이 {age_h:.1f}h 전 생성됨 (< {args.if_stale}h), 스킵",
                  file=sys.stderr)
            return 0

    universe = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers = [r["ticker"] for r in universe["rows"]]
    total = len(tickers)
    print(f"유니버스 {total} 종목 수집 시작", file=sys.stderr)

    t0 = time.time()
    rows: dict[str, dict] = {}
    failed: list[str] = []

    # 1단계: 전 종목 t.info ------------------------------------------------
    for i, tk in enumerate(tickers, 1):
        info = fetch_info(tk)
        if info is None:
            rows[tk] = null_row()
            failed.append(tk)
        else:
            rows[tk] = row_from_info(info)
        if i % 25 == 0 or i == total:
            print(f"  info {i}/{total} ({time.time() - t0:.0f}s, 실패 {len(failed)})",
                  file=sys.stderr)
        time.sleep(INFO_THROTTLE_SEC)

    # 2단계: 시총 상위 TOP_N_EXTRA 종목 추가 수집 --------------------------
    ranked = sorted((tk for tk in rows if rows[tk]["mcap"] is not None),
                    key=lambda tk: rows[tk]["mcap"], reverse=True)
    top = ranked if TOP_N_EXTRA <= 0 else ranked[:TOP_N_EXTRA]
    print(f"{len(top)} 종목 실적 일정/서프라이즈 수집", file=sys.stderr)
    for i, tk in enumerate(top, 1):
        rows[tk].update(fetch_extras(tk))
        if i % 25 == 0 or i == len(top):
            print(f"  extra {i}/{len(top)} ({time.time() - t0:.0f}s)",
                  file=sys.stderr)
        time.sleep(EXTRA_THROTTLE_SEC)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": total,
        "failed": failed,
        "rows": rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, allow_nan=False, indent=1),
        encoding="utf-8",
    )
    print(f"완료: {OUT_PATH} ({total} 종목, 실패 {len(failed)}, "
          f"{time.time() - t0:.0f}s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
