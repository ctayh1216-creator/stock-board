#!/usr/bin/env python
"""discover.py — S&P500 유니버스 규칙 기반 스크리닝.

data/technicals.json + data/fundamentals.json + data/universe_sp500.json 을 병합해
팩터 점수(0-100 백분위)와 4개 전략(모멘텀/가치+퀄리티/실적 모멘텀/과매도 반등)의
상위 종목을 data/discover.json 으로 출력한다.

사용:
    python src/discover.py
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "discover.json"
SFX = ""
CURRENCY = "USD"

CONSENSUS_MAP = {"strong_buy": 5, "buy": 4, "hold": 3, "underperform": 2, "sell": 1}

# screener.py 의 가중치에서 fit 팩터 제외 후 재정규화한 값
FACTOR_WEIGHTS = {
    "value": 0.24, "quality": 0.24, "growth": 0.20,
    "momentum": 0.20, "analyst": 0.12,
}

DISCLAIMER = (
    "투자 자문이 아닙니다. 데이터 출처는 Yahoo Finance 이며 지연·오류가 있을 수 있습니다. "
    "투자 판단과 책임은 이용자 본인에게 있습니다."
)

# ---------------------------------------------------------------- 매매 신호
# 가격 하나로만 판단한다. 재무·뉴스·사람의 해석을 섞지 않는다 —
# 섞는 순간 규칙을 화면에 그대로 공개할 수 없고, 과거 성적도 잴 수 없다.
SIGNAL_RULES = [
    "50일 평균선 아래로 내려오면 청산 구간이에요",
    "1년 평균(200일선) 위 · 한 달 평균(20일선) 위 · RSI 70 미만이면 매수 구간이에요",
    "둘 다 아니면 관망이에요",
]
SIGNAL_LEVELS = ["목표가는 52주 최고가, 손절선은 50일 평균선이에요"]
# 칩에 쓰는 짧은 이름. 「실적이 계속 잘 나오는 회사」를 칩에 그대로 넣었더니
# 모바일에서 칩이 네 줄(251px)을 먹었다. 섹션 제목은 긴 이름 그대로 쓴다.
SHORT_NAME = {
    "bigcap":        "시총 1~10위",
    "momentum":      "잘 오르는 중",
    "earnings":      "실적 좋은",
    "growth":        "빠르게 크는",
    "value_quality": "싸고 잘 버는",
    "dividend":      "배당",
    "stable":        "덜 흔들리는",
    "oversold":      "많이 떨어진",
    "earnings_miss": "실적 어긋난",
}

SIGNAL_LABEL = {"buy": "매수 구간", "watch": "관망", "exit": "청산 구간"}
# 판정 이유는 코드로 내려보내고 문구는 여기 한 군데에만 둔다. 화면은 코드로 찾아 쓴다 —
# 규칙을 화면에서도 다시 계산해야 하는데(시세는 3분, 이 파일은 하루 1회 갱신),
# 문구까지 양쪽에 두면 언젠가 서로 다른 말을 하게 된다.
SIGNAL_REASON = {
    "below_s50":  "50일 평균선 아래로 내려왔어요",
    "trend":      "1년 평균과 한 달 평균 위에 있고, 아직 과열도 아니에요",
    "below_s200": "아직 1년 평균 아래에 있어요",
    "hot":        "단기 과열이라 규칙이 기다리라고 해요",
    "below_s20":  "한 달 평균 아래로 내려왔어요",
}
RSI_MAX = 70          # 이 위는 과열로 본다. 화면도 이 값을 받아서 쓴다.
SPLIT_LO, SPLIT_HI = 0.65, 1.55   # split_gap 판정 경계 — 화면과 공유한다.


def split_gap(r) -> bool:
    """현재가와 과거 시세의 기준이 어긋났는지 (액면분할·병합 직후).

    분할이 나면 현재가만 새 기준으로 바뀌고 이동평균·전일종가는 며칠 옛 기준으로 남는다
    (야후 과거 시세 소급 반영이 늦다). 그대로 두면 멀쩡한 종목이 하루 -48% 로 찍히고
    규칙은 «청산»이라는 거짓 신호를 낸다 — 2026-09-04 APH 2:1 분할에서 실제로 그랬다.
    하루에 그만큼 움직이는 대형주는 사실상 없으므로, 어긋난 날은 판단도 표시도 하지 않는다.
    """
    last, prev = r.get("last"), r.get("prev_close")
    if not (last and prev) or prev <= 0:
        return False
    return not SPLIT_LO < last / prev < SPLIT_HI


def signal_for(r):
    """가격 규칙이 지금 이 종목을 어느 구간이라고 부르는지."""
    last, s20 = r.get("last"), r.get("sma20")
    s50, s200 = r.get("sma50"), r.get("sma200")
    rsi, hi = r.get("rsi14"), r.get("hi52")
    if None in (last, s20, s50, s200, rsi) or not last > 0:
        return None

    if split_gap(r):
        return None

    state, reason = signal_state(last, s20, s50, s200, rsi)
    # 기준선을 같이 실어 보낸다. 화면은 3분마다 들어오는 시세로 이 선들과 다시 견줘
    # 상태를 고쳐 잡는다 — 이 파일은 하루 한 번만 도는데 값은 계속 움직이기 때문이다.
    return {
        "state": state, "reason": reason,
        "stop": f(s50, 2), "s20": f(s20, 2), "s200": f(s200, 2),
        "rsi": f(rsi, 1), "hi": f(hi, 2), "prev": f(r.get("prev_close"), 2),
    }


def signal_state(last, s20, s50, s200, rsi):
    """규칙 본체. 화면(discover_template.html sigState)이 이 순서를 그대로 따라간다."""
    if last < s50:
        return "exit", "below_s50"
    if last > s200 and last > s20 and rsi < RSI_MAX:
        return "buy", "trend"
    if last <= s200:
        return "watch", "below_s200"
    if rsi >= RSI_MAX:
        return "watch", "hot"
    return "watch", "below_s20"


def market_date(market: str) -> str:
    """거래소 현지 날짜. UTC 로 자르면 한국 오전이 전날로 밀린다."""
    tz = ZoneInfo("Asia/Seoul" if market == "kr" else "America/New_York")
    return datetime.now(tz).date().isoformat()


def signal_changes(table, market: str):
    """전 거래일 마지막 상태와 견줘 오늘 구간이 바뀐 종목.

    스냅샷은 baseline(비교 기준)과 latest(오늘 마지막 관측)를 같이 들고 다닌다.
    날짜가 바뀐 첫 실행에서 latest 를 baseline 으로 올린다 — 크론이 몇 시에 돌든
    «전 거래일 마지막 상태 대비»가 유지되고, 주말에는 값이 안 변해 저절로 비어 있다.
    """
    path = DATA / f"signal_state{SFX}.json"
    today = market_date(market)
    now = {r["ticker"]: r["signal"]["state"] for r in table if r.get("signal")}

    snap = {}
    try:
        snap = json.loads(path.read_text())
    except Exception:
        pass
    base, base_date = snap.get("baseline") or {}, snap.get("baseline_date")
    latest, latest_date = snap.get("latest") or {}, snap.get("latest_date")
    if latest_date and latest_date != today:
        base, base_date = latest, latest_date

    # 매수로 올라선 것 · 청산으로 내려온 것 · 관망 순. 볼 값어치 순서다.
    order = {"buy": 0, "exit": 1, "watch": 2}
    changes = [{"ticker": tk, "from": base[tk], "to": st}
               for tk, st in now.items() if base.get(tk) and base[tk] != st]
    changes.sort(key=lambda c: (order.get(c["to"], 9), c["ticker"]))

    path.write_text(json.dumps({
        "baseline_date": base_date, "baseline": base,
        "latest_date": today, "latest": now,
    }, ensure_ascii=False) + "\n")
    return changes, base_date


def extremes(rows, edge: float = 1.0):
    """52주 최고·최저를 새로 쓴 종목.

    pos52w 는 52주 범위 안에서 지금 어디쯤인지(0~100)다. 100 에 붙었으면 오늘이
    그 1년의 꼭대기, 0 이면 바닥이다. hi52/lo52 와 따로 비교할 필요가 없다.
    """
    hi, lo = [], []
    for r in rows:
        pos = r.get("pos52w")
        if pos is None:
            continue
        if pos >= 100 - edge:
            hi.append({"ticker": r["ticker"], "level": f(r.get("hi52"), 2)})
        elif pos <= edge:
            lo.append({"ticker": r["ticker"], "level": f(r.get("lo52"), 2)})
    key = lambda x: x["ticker"]
    return {"high": sorted(hi, key=key), "low": sorted(lo, key=key)}


def earnings_soon(rows, days: int = 7):
    """곧 실적을 발표하는 종목.

    next_earnings 는 이미 499/500 종목치를 받아놓고 상세 시트 맨 아래 한 줄로만
    쓰고 있었다. 날짜순으로 추려 목록으로 낸다 — 매일 들러서 볼 거리가 된다.
    """
    out = []
    for r in rows:
        d = days_until(r.get("next_earnings"))
        if d is None or not 0 <= d <= days:
            continue
        out.append({"ticker": r["ticker"], "date": str(r["next_earnings"])[:10], "days": d,
                    "beats4": r.get("beats4"),
                    "surprise_last": f(r.get("surprise_last"), 1)})
    out.sort(key=lambda x: (x["days"], x["ticker"]))
    return out


# ---------------------------------------------------------------- helpers
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


def pct_rank(s: pd.Series) -> pd.Series:
    """0-100 백분위 순위 (높을수록 좋음). NaN 은 NaN 유지."""
    s = s.astype(float)
    n = int(s.notna().sum())
    if n == 0:
        return s
    if n == 1:
        out = s.copy()
        out[s.notna()] = 50.0
        return out
    return (s.rank(method="average") - 1) / (n - 1) * 100


def mean_of(ranks: list[pd.Series]) -> pd.Series:
    """서브랭크 평균. 전부 NaN 인 행은 NaN."""
    return pd.concat(ranks, axis=1).mean(axis=1, skipna=True)


def sign_pct(v, nd=1):
    """+/- 부호 붙은 퍼센트 문자열."""
    return f"{v:+.{nd}f}%"


def days_until(iso: str | None) -> int | None:
    """오늘부터 iso 날짜까지 남은 일수. 파싱 실패는 None."""
    if not iso:
        return None
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return (d - datetime.now(timezone.utc).date()).days


# ---------------------------------------------------------------- load & merge
def load_rows():
    """세 입력 파일을 티커 기준으로 병합. technicals 가 있는 종목만 반환."""
    tech = json.loads((DATA / f"technicals{SFX}.json").read_text())
    fund = json.loads((DATA / (f"fundamentals{SFX}.json")).read_text())
    univ = json.loads((DATA / (f"universe{SFX}.json" if SFX else "universe_sp500.json")).read_text())

    trows, frows = tech["rows"], fund["rows"]
    rows = []
    for u in univ["rows"]:
        t = u["ticker"]
        tr = trows.get(t)
        if tr is None:
            print(f"[warn] technicals 없음: {t}", file=sys.stderr)
            continue
        fr = frows.get(t) or {}
        mcap = fr.get("mcap")
        fcf = fr.get("fcf")
        fcf_yield = None
        if fcf is not None and mcap:
            fcf_yield = fcf / mcap * 100
        rows.append({
            "ticker": t, "name": u.get("name"), "sector": u.get("sector"),
            **tr, **{k: fr.get(k) for k in (
                "mcap", "pe_fwd", "peg", "ps", "roe", "margin", "rev_g", "eps_g",
                "upside", "consensus", "n_analysts",
                "next_earnings", "surprise_last", "beats4", "eps_hist",
                "div_yield", "beta", "debt_eq")},
            "fcf_yield": fcf_yield,
            "fcf": fcf,
        })
    return rows, tech["generated_at"], fund.get("generated_at")



def build_bigcap(rows, total_mcap):
    """시총 1~10위. 업종과 무관하게 덩치로만 자른다 — 지수를 실제로 움직이는 종목들."""
    ranked = sorted((r for r in rows if r.get("mcap") is not None),
                    key=lambda r: r["mcap"], reverse=True)[:10]
    hits = []
    for i, r in enumerate(ranked):
        mcap = r["mcap"]
        share = mcap / total_mcap * 100 if total_mcap else None
        # 시총은 상장 시장의 통화로 들어온다 — 미국은 달러, 한국은 원.
        if CURRENCY == "KRW":
            unit = f"{mcap / 1e12:.0f}조 원" if mcap >= 1e12 else f"{mcap / 1e8:.0f}억 원"
        else:
            unit = f"{mcap / 1e12:.1f}조 달러" if mcap >= 1e12 else f"{mcap / 1e9:.0f}억 달러"
        why = [f"시가총액 {unit}, 전체 {i + 1}위예요"]
        if share is not None:
            why.append(f"시장 전체의 {share:.1f}%를 혼자 차지해요")
        if r.get("ret_12m") is not None:
            why.append(f"1년간 {sign_pct(r['ret_12m'])}")
        hits.append((mcap, r, why))

    top_share = (sum(r["mcap"] for r in ranked) / total_mcap * 100) if total_mcap else None
    return {
        "key": "bigcap", "name": "시총 1~10위",
        "desc": "덩치로 줄 세워 앞의 열 개예요. 지수가 오르내리는 건 사실상 이들이 움직인 결과라, 시장이 어디로 가는지 보려면 여기부터 봅니다.",
        "rules": [
            "업종과 무관하게 시가총액 순위 1위부터 10위까지예요",
            "순위가 바뀌면 구성도 따라 바뀝니다",
        ],
        "rows": [strat_row(r, sc, w) for sc, r, w in hits],
        "headline": {"label": "시장에서 차지하는 비중", "value": f(top_share, 1), "fmt": "pct0"},
    }, len(hits)


# ---------------------------------------------------------------- scoring
def score_rows(rows):
    """팩터별 0-100 백분위 점수와 가중 composite 를 각 행에 부여."""
    df = pd.DataFrame(rows).set_index("ticker")
    num = df.apply(pd.to_numeric, errors="coerce")

    def pos_neg(col):
        """양수인 값만 남기고 부호 반전 (낮을수록 좋은 지표)."""
        v = num[col].where(num[col] > 0)
        return pct_rank(-v)

    value = mean_of([pos_neg("pe_fwd"), pos_neg("peg"), pos_neg("ps")])
    quality = mean_of([pct_rank(num[c]) for c in ("roe", "margin", "fcf_yield")])
    growth = mean_of([pct_rank(num[c]) for c in ("rev_g", "eps_g")])
    momentum = mean_of([pct_rank(num[c]) for c in ("ret_6m", "ret_12m", "vs_sma200")])

    cons = df["consensus"].map(lambda k: CONSENSUS_MAP.get(k) if isinstance(k, str) else None)
    analyst = mean_of([pct_rank(num["upside"]), pct_rank(cons.astype(float))])

    factors = {"value": value, "quality": quality, "growth": growth,
               "momentum": momentum, "analyst": analyst}

    for row in rows:
        t = row["ticker"]
        scores = {k: f(v.get(t), 1) for k, v in factors.items()}
        avail = {k: s for k, s in scores.items() if s is not None}
        if avail:
            wsum = sum(FACTOR_WEIGHTS[k] for k in avail)
            comp = sum(FACTOR_WEIGHTS[k] * s for k, s in avail.items()) / wsum
            scores["composite"] = f(comp, 1)
        else:
            scores["composite"] = None
        row["scores"] = scores
    return rows


# ---------------------------------------------------------------- strategies

def headline(key: str, hits_rows: list) -> dict | None:
    """카테고리를 정의하는 대표 수치.

    모든 카테고리에 승률을 억지로 붙이는 대신(재무 전략은 과거 재무가 없어 재현 불가),
    그 기준을 그 기준이게 만드는 값을 하나 고른다.
    """
    def avg(field):
        vals = [r.get(field) for r in hits_rows if r.get(field) is not None]
        return sum(vals) / len(vals) if vals else None

    def med(field):
        """어긋난 정도처럼 한쪽 꼬리가 극단인 값은 평균이 대표값이 못 된다.
        COIN 한 종목의 -487% 가 스무 종목의 평균을 -55% 로 끌어내렸다."""
        vals = sorted(r.get(field) for r in hits_rows
                      if r.get(field) is not None and surprise_meaningful(r))
        if not vals:
            return None
        m = len(vals) // 2
        return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2

    spec = {
        "momentum":      ("평균 6개월 수익률", avg("ret_6m"), "pct"),
        "earnings":      ("4번 중 기대 상회",  avg("beats4"), "count"),
        "growth":        ("평균 매출 성장",    avg("rev_g"), "pct"),
        "value_quality": ("평균 선행 PER",     avg("pe_fwd"), "num"),
        "dividend":      ("평균 배당수익률",   avg("div_yield"), "pct0"),
        "stable":        ("평균 변동성",       avg("vol_ann"), "pct0"),
        "oversold":      ("평균 RSI",          avg("rsi14"), "num"),
        "earnings_miss": ("직전 실적 중간값",  med("surprise_last"), "pct"),
        "bigcap":        ("평균 1년 수익률",   avg("ret_12m"), "pct"),
    }.get(key)
    if not spec:
        return None
    label, value, fmt = spec
    if value is None:
        return None
    return {"label": label, "value": f(value, 1), "fmt": fmt}


def strat_row(r, rank_score, why):
    return {
        "ticker": r["ticker"], "name": r["name"], "sector": r["sector"],
        "last": f(r.get("last"), 2), "day_pct": f(r.get("day_pct"), 1),
        "mcap": f(r.get("mcap")), "rank_score": f(rank_score, 1),
        "why": why, "spark": r.get("spark"), "signal": signal_for(r),
    }


def build_momentum(rows, ret6_p75):
    """모멘텀 추세: 주가>200일선, 50일선>200일선, 6개월 수익률 상위 25%, RSI 45-78."""
    hits = []
    for r in rows:
        last, s50, s200 = r.get("last"), r.get("sma50"), r.get("sma200")
        r6, rsi = r.get("ret_6m"), r.get("rsi14")
        mo = r["scores"]["momentum"]
        if None in (last, s50, s200, r6, rsi) or mo is None:
            continue
        if not (last > s200 and s50 > s200 and r6 >= ret6_p75 and 45 <= rsi <= 78):
            continue
        why = [f"6개월 새 {sign_pct(r6)} 올랐어요"]
        if r.get("ret_12m") is not None:
            why.append(f"1년으로 보면 {sign_pct(r['ret_12m'])}")
        why.append("아직 과열 구간은 아니에요" if rsi < 70 else "다만 단기 과열 신호가 보여요")
        hits.append((mo, r, why[:4]))
    hits.sort(key=lambda x: x[0], reverse=True)
    _key = "momentum"
    _rows = [strat_row(r, sc, w) for sc, r, w in hits[:20]]
    return {
        "key": "momentum", "name": "잘 오르고 있는 주식",
        "desc": "오르는 중이고, 아직 안 꺾였어요. 지난 반년 수익률이 500종목 중 상위 25% 안에 든 것만 골랐습니다.",
        "rules": [
            "지금 주가가 1년 평균보다 높아요 (200일 이동평균)",
            "최근 흐름이 장기 흐름보다 위에 있어요 (50일선 > 200일선)",
            f"6개월 수익률이 500개 종목 중 상위 25% 안에 들어요 ({sign_pct(ret6_p75)} 이상)",
            "너무 급하게 오르지도, 식지도 않았어요 (RSI 45~78)",
            "많이 오른 순서로 보여드려요",
        ],
        "rows": _rows, "headline": headline(_key, [r for _, r, _ in hits[:20]]),
    }, len(hits)


def build_value_quality(rows, sector_pe_med):
    """가치+퀄리티: 섹터 대비 싼 Fwd PER + ROE/마진 우량 + 가치 점수 상위."""
    hits = []
    for r in rows:
        pe, roe, margin = r.get("pe_fwd"), r.get("roe"), r.get("margin")
        val, qua = r["scores"]["value"], r["scores"]["quality"]
        med = sector_pe_med.get(r.get("sector"))
        if None in (pe, roe, margin, val, qua) or med is None:
            continue
        if not (pe > 0 and pe <= med and roe >= 15 and margin >= 8 and val >= 55):
            continue
        rank = (val + qua) / 2
        cheap = (1 - pe / med) * 100 if med else None
        why = [f"같은 업종 평균보다 {cheap:.0f}% 싸요" if cheap and cheap >= 1
               else "같은 업종 평균보다 싸요",
               f"넣은 돈 대비 이익을 잘 내요 (ROE {roe:.0f}%)",
               f"매출 100원당 {margin:.0f}원이 이익으로 남아요"]
        hits.append((rank, r, why))
    hits.sort(key=lambda x: x[0], reverse=True)
    _key = "value_quality"
    _rows = [strat_row(r, sc, w) for sc, r, w in hits[:20]]
    return {
        "key": "value_quality", "name": "싸면서 돈 잘 버는 회사",
        "desc": "같은 업종 평균보다 싸게 거래되는데 정작 이익은 잘 내고 있는 회사들이에요. 싼 데는 이유가 있을 수도 있으니, 왜 싼지는 따로 봐야 합니다.",
        "rules": [
            "주가가 같은 업종 평균보다 싸요 (예상 PER 기준)",
            "넣은 돈 대비 이익을 잘 내요 (ROE 15% 이상)",
            "매출 100원당 8원 넘게 이익으로 남겨요",
            "가격 매력도가 500개 중 상위 45% 안에 들어요",
            "싸고 잘 버는 순서로 보여드려요",
        ],
        "rows": _rows, "headline": headline(_key, [r for _, r, _ in hits[:20]]),
    }, len(hits)


def build_earnings(rows):
    """실적 모멘텀: 최근 4분기 서프라이즈 3회 이상 + 직전 서프라이즈 + 성장."""
    hits = []
    for r in rows:
        b4, sl = r.get("beats4"), r.get("surprise_last")
        eps_g, rev_g = r.get("eps_g"), r.get("rev_g")
        if b4 is None or sl is None:
            continue
        if not (b4 >= 3 and sl > 0):
            continue
        if not ((eps_g is not None and eps_g > 0) or (rev_g is not None and rev_g > 10)):
            continue
        sc = r["scores"]
        parts = [v for v in (sc["analyst"], sc["growth"]) if v is not None]
        if not parts:
            continue
        rank = sum(parts) / len(parts)
        why = [f"최근 1년 중 {b4}번 시장 기대를 넘었어요",
               f"직전 실적은 기대보다 {sign_pct(sl)} 잘 나왔어요"]
        if eps_g is not None and eps_g > 0:
            why.append(f"이익이 {sign_pct(eps_g)} 늘고 있어요")
        elif rev_g is not None:
            why.append(f"매출이 {sign_pct(rev_g)} 늘고 있어요")
        d = days_until(r.get("next_earnings"))
        if d is not None and 0 <= d <= 45:
            why.append(f"다음 실적 발표가 {d}일 남았어요")
        hits.append((rank, r, why[:4]))
    hits.sort(key=lambda x: x[0], reverse=True)
    _key = "earnings"
    _rows = [strat_row(r, sc, w) for sc, r, w in hits[:20]]
    return {
        "key": "earnings", "name": "실적이 계속 잘 나오는 회사",
        "desc": "네 번 발표해서 세 번 이상 시장 기대를 넘긴 회사예요.",
        "rules": [
            "최근 4번의 실적 발표 중 3번 이상 기대를 넘었어요",
            "가장 최근 실적도 기대보다 좋았어요",
            "이익이 늘고 있거나, 매출이 10% 넘게 늘고 있어요",
            "전문가 평가와 성장세가 좋은 순서로 보여드려요",
        ],
        "rows": _rows, "headline": headline(_key, [r for _, r, _ in hits[:20]]),
    }, len(hits)


def surprise_meaningful(r) -> bool:
    """서프라이즈 %를 숫자로 쓸 수 있는지.

    이 값은 (실제-예상)/|예상| 이라, 예상 EPS 가 0 근처이거나 적자면 분모가 무너진다.
    COIN 은 예상 0.04 에 실제 -1.49 라서 -3458.9% 가 찍혔다 — 수학은 맞지만
    다른 종목과 크기를 견줄 수도, 화면에 적을 수도 없는 숫자다.
    """
    h = r.get("eps_hist")
    if not h:
        return False
    est = h[-1].get("est")
    return est is not None and est > 0.05


def build_earnings_miss(rows):
    """실적이 어긋나는 회사 — «잘 나오는 회사»의 정확한 반대편.

    직전 한 번 삐끗한 것과 계속 어긋나는 것을 둘 다 잡는다. 한 분기 실수는
    누구나 하지만 네 번 중 두 번을 못 맞추면 그건 회사 사정이거나 시장의 눈이
    잘못 맞춰져 있다는 뜻이다. 어느 쪽인지는 말하지 않는다 — 사실만 적는다.
    """
    hits = []
    for r in rows:
        b4, sl = r.get("beats4"), r.get("surprise_last")
        if b4 is None and sl is None:
            continue
        ok = surprise_meaningful(r)
        big_miss = ok and sl is not None and sl <= -2
        chronic = b4 is not None and b4 <= 2
        if not (big_miss or chronic):
            continue
        # 이름이 «자꾸 어긋나는»이므로 몇 번 어긋났는지를 먼저 본다. 한 번 크게 삐끗한
        # 종목이 1위로 올라오면 이름과 화면이 어긋난다. 크기는 보조로만 쓰고 100 에서
        # 자른다 — 뜻을 잃은 %는 아예 빼서, 분모 작은 한 종목이 판을 가져가지 않게 한다.
        miss_n = 0 if b4 is None else (4 - b4)
        depth = min(-sl, 100) if (ok and sl is not None and sl < 0) else 0
        rank = miss_n * 25 + depth
        why = []
        if ok and sl is not None and sl < 0:
            # 부호를 붙이면 «-141.5% 못 미쳤어요»가 되어 이중부정으로 읽힌다.
            why.append(f"직전 실적이 기대보다 {abs(sl):.1f}% 모자랐어요")
        elif not ok:
            why.append("예상치가 0 근처라 몇 % 빗나갔는지는 뜻이 없어요")
        if b4 is not None:
            why.append(f"최근 4번 중 {4 - b4}번은 기대를 못 맞췄어요" if b4 < 4
                       else "다만 최근 4번은 모두 기대를 넘겼어요")
        rev_g = r.get("rev_g")
        if rev_g is not None:
            why.append(f"매출은 1년 새 {abs(rev_g):.1f}% "
                       + ("줄었어요" if rev_g < 0 else "늘었어요"))
        d = days_until(r.get("next_earnings"))
        if d is not None and 0 <= d <= 45:
            why.append(f"다음 실적 발표가 {d}일 남았어요")
        hits.append((rank, r, why[:4]))
    hits.sort(key=lambda x: x[0], reverse=True)
    _key = "earnings_miss"
    _rows = [strat_row(r, sc, w) for sc, r, w in hits[:20]]
    return {
        "key": "earnings_miss", "name": "실적이 자꾸 어긋나는 회사",
        "desc": "시장이 기대한 숫자를 못 맞추고 있는 회사예요. "
                "싸게 방치된 걸 수도, 정말 나빠지는 중일 수도 있어요.",
        "rules": [
            "직전 실적이 기대보다 2% 넘게 못 미쳤거나,",
            "최근 4번의 실적 발표 중 2번 이상 기대를 못 맞췄어요",
            "자주 어긋난 순서로 보여드려요",
        ],
        "rows": _rows, "headline": headline(_key, [r for _, r, _ in hits[:20]]),
    }, len(hits)


def build_oversold(rows):
    """과매도 반등 후보: RSI 과매도이지만 장기 추세와 퀄리티가 살아있는 종목."""
    hits = []
    for r in rows:
        rsi, vs200 = r.get("rsi14"), r.get("vs_sma200")
        qua = r["scores"]["quality"]
        if None in (rsi, vs200, qua):
            continue
        if not (rsi <= 32 and vs200 >= -12 and qua >= 50):
            continue
        why = ["최근 많이 팔려서 단기적으로 싸진 상태예요",
               (f"그래도 1년 평균보다 {sign_pct(vs200)} 수준은 지키고 있어요"
                if vs200 >= 0 else f"1년 평균보다 {abs(vs200):.0f}% 아래로만 내려왔어요"),
               "회사 자체 실적은 튼튼한 편이에요"]
        hits.append((qua, r, why))
    hits.sort(key=lambda x: x[0], reverse=True)
    _key = "oversold"
    _rows = [strat_row(r, sc, w) for sc, r, w in hits[:20]]
    return {
        "key": "oversold", "name": "많이 떨어진 우량주",
        "desc": "짧은 새 많이 밀렸는데 회사 자체는 멀쩡한 경우예요. 조건이 까다로워서 어떤 날은 한두 종목만 걸리고, 아예 비는 날도 있습니다.",
        "rules": [
            "최근 매도세가 강했어요 (RSI 32 이하)",
            "그래도 1년 평균에서 12% 넘게 벗어나진 않았어요",
            "수익성이 500개 중 상위 절반 안에 들어요",
            "실적이 튼튼한 순서로 보여드려요",
        ],
        "rows": _rows, "headline": headline(_key, [r for _, r, _ in hits[:20]]),
    }, len(hits)


# ---------------------------------------------------------------- table
def build_dividend(rows):
    """배당: 꾸준히 배당을 주면서 현금흐름이 받쳐주는 종목."""
    hits = []
    for r in rows:
        dy, fcf = r.get("div_yield"), r.get("fcf")
        qua = r["scores"]["quality"]
        if None in (dy, qua) or fcf is None:
            continue
        if not (dy >= 2.5 and fcf > 0 and qua >= 40):
            continue
        why = [f"1년에 투자금의 {dy:.1f}%를 배당으로 줘요",
               "벌어들인 현금이 배당을 감당하고 있어요"]
        if r.get("margin") is not None:
            why.append(f"매출 100원당 {r['margin']:.0f}원이 이익으로 남아요")
        hits.append((dy, r, why))
    hits.sort(key=lambda x: x[0], reverse=True)
    _key = "dividend"
    _rows = [strat_row(r, sc, w) for sc, r, w in hits[:20]]
    return {
        "key": "dividend", "name": "배당 주는 회사",
        "desc": "갖고만 있어도 1년에 2.5% 넘게 현금으로 주는 회사예요 — 그것도 벌어들인 돈 안에서요.",
        "rules": [
            "배당수익률이 연 2.5% 이상이에요",
            "벌어들이는 현금이 플러스라 배당을 감당할 수 있어요",
            "수익성이 500개 중 하위권은 아니에요",
            "배당을 많이 주는 순서로 보여드려요",
        ],
        "rows": _rows, "headline": headline(_key, [r for _, r, _ in hits[:20]]),
    }, len(hits)


def build_stable(rows, vol_p30, mcap_med):
    """안정: 가격이 덜 흔들리고 덩치와 수익성이 받쳐주는 종목."""
    hits = []
    for r in rows:
        vol, mcap = r.get("vol_ann"), r.get("mcap")
        beta = r.get("beta")
        qua = r["scores"]["quality"]
        if None in (vol, mcap, qua):
            continue
        if not (vol <= vol_p30 and mcap >= mcap_med and qua >= 50):
            continue
        if beta is not None and beta > 1.1:
            continue
        why = [f"1년간 가격 출렁임이 {vol:.0f}%로 낮은 편이에요"]
        if beta is not None:
            why.append(f"시장이 1% 움직일 때 {beta:.1f}% 정도만 움직여요")
        why.append("덩치가 크고 수익성도 안정적이에요")
        hits.append((qua, r, why))
    hits.sort(key=lambda x: x[0], reverse=True)
    _key = "stable"
    _rows = [strat_row(r, sc, w) for sc, r, w in hits[:20]]
    return {
        "key": "stable", "name": "덜 흔들리는 안정형",
        "desc": "값이 덜 출렁여요. 500종목 중 변동성이 낮은 쪽 30%에 들면서 덩치도 중간 이상인 회사만 남겼습니다.",
        "rules": [
            f"1년 가격 변동성이 500개 중 하위 30%예요 ({vol_p30:.0f}% 이하)",
            "시가총액이 중간값 이상인 큰 회사예요",
            "시장보다 덜 움직여요 (베타 1.1 이하)",
            "수익성이 500개 중 상위 절반 안에 들어요",
        ],
        "rows": _rows, "headline": headline(_key, [r for _, r, _ in hits[:20]]),
    }, len(hits)


def build_growth(rows):
    """성장: 매출·이익이 빠르게 커지는 종목."""
    hits = []
    for r in rows:
        rev_g, eps_g = r.get("rev_g"), r.get("eps_g")
        gro = r["scores"]["growth"]
        if rev_g is None or gro is None:
            continue
        if not (rev_g >= 20 and (eps_g is None or eps_g > 0)):
            continue
        why = [f"매출이 1년 새 {sign_pct(rev_g)} 늘었어요"]
        if eps_g is not None:
            why.append(f"이익은 {sign_pct(eps_g)} 늘었어요")
        if r.get("ret_12m") is not None:
            why.append(f"주가는 1년간 {sign_pct(r['ret_12m'])}")
        hits.append((gro, r, why))
    hits.sort(key=lambda x: x[0], reverse=True)
    _key = "growth"
    _rows = [strat_row(r, sc, w) for sc, r, w in hits[:20]]
    return {
        "key": "growth", "name": "빠르게 크는 회사",
        "desc": "매출이 1년 새 20% 넘게 늘었어요. 회사가 아직 커지는 중이라는 뜻인데, 그만큼 주가에 기대가 미리 들어가 있기도 합니다.",
        "rules": [
            "매출이 1년 새 20% 이상 늘었어요",
            "이익도 줄지 않고 있어요",
            "성장세가 강한 순서로 보여드려요",
        ],
        "rows": _rows, "headline": headline(_key, [r for _, r, _ in hits[:20]]),
    }, len(hits)


PCT1 = ("day_pct", "ret_1w", "ret_1m", "ret_6m", "ret_12m", "rsi14",
        "vs_sma50", "vs_sma200", "pos52w", "vol_ann",
        "roe", "margin", "rev_g", "eps_g", "upside", "surprise_last", "div_yield")
DP2 = ("last", "vol_spike", "pe_fwd", "peg", "ps", "beta", "hi52", "lo52")


def table_row(r):
    out = {"ticker": r["ticker"], "name": r["name"], "sector": r["sector"]}
    for k in DP2:
        out[k] = f(r.get(k), 2)
    for k in PCT1:
        out[k] = f(r.get(k), 1)
    out["mcap"] = f(r.get("mcap"))
    cons = r.get("consensus")
    out["consensus"] = cons if cons in CONSENSUS_MAP else None
    n = r.get("n_analysts")
    out["n_analysts"] = int(n) if isinstance(n, (int, float)) and math.isfinite(n) else None
    ne = r.get("next_earnings")
    out["next_earnings"] = str(ne)[:10] if ne else None
    b4 = r.get("beats4")
    out["beats4"] = int(b4) if isinstance(b4, (int, float)) and math.isfinite(b4) else None
    out["scores"] = r["scores"]
    out["spark"] = r.get("spark")
    out["signal"] = signal_for(r)
    out["eps_hist"] = r.get("eps_hist")
    return out


# ---------------------------------------------------------------- main
def main():
    import argparse
    ap = argparse.ArgumentParser(description="규칙 기반 스크리닝")
    ap.add_argument("--market", choices=("us", "kr"), default="us")
    opts = ap.parse_args()
    global SFX, CURRENCY
    SFX = "_kr" if opts.market == "kr" else ""
    CURRENCY = "KRW" if opts.market == "kr" else "USD"

    rows, tech_at, fund_at = load_rows()
    print(f"[info] 병합 유니버스 {len(rows)}종목", file=sys.stderr)

    # 기준이 어긋난 종목의 등락률은 «—» 로 비운다. 거짓 -48% 를 화면에 두면
    # 하락 종목 수·오늘의 하락 상위까지 같이 틀어진다.
    broken = [r["ticker"] for r in rows if split_gap(r)]
    for r in rows:
        if split_gap(r):
            r["day_pct"] = None
    if broken:
        print(f"[info] 분할 의심 {len(broken)}종목 등락률 제외: {', '.join(broken)}",
              file=sys.stderr)

    rows = score_rows(rows)

    r6 = pd.Series([r.get("ret_6m") for r in rows], dtype=float)
    ret6_p75 = float(np.nanpercentile(r6.to_numpy(dtype=float), 75))
    print(f"[info] 6개월 수익률 75퍼센타일 {ret6_p75:+.1f}%", file=sys.stderr)

    pe = pd.DataFrame({"sector": [r.get("sector") for r in rows],
                       "pe_fwd": [r.get("pe_fwd") for r in rows]})
    pe["pe_fwd"] = pd.to_numeric(pe["pe_fwd"], errors="coerce").where(lambda s: s > 0)
    sector_pe_med = pe.groupby("sector")["pe_fwd"].median().dropna().to_dict()

    vols = pd.Series([r.get("vol_ann") for r in rows], dtype=float)
    vol_p30 = float(np.nanpercentile(vols.to_numpy(dtype=float), 30))
    mcaps = pd.Series([r.get("mcap") for r in rows], dtype=float)
    mcap_med = float(np.nanmedian(mcaps.to_numpy(dtype=float)))
    print(f"[info] 변동성 30퍼센타일 {vol_p30:.1f}% / 시총 중앙값 {mcap_med/1e9:.0f}B",
          file=sys.stderr)

    total_mcap = sum(r["mcap"] for r in rows if r.get("mcap"))

    strategies, counts = [], {}
    for build, args in ((build_bigcap, (rows, total_mcap)),
                        (build_momentum, (rows, ret6_p75)),
                        (build_earnings, (rows,)),
                        (build_growth, (rows,)),
                        (build_value_quality, (rows, sector_pe_med)),
                        (build_dividend, (rows,)),
                        (build_stable, (rows, vol_p30, mcap_med)),
                        (build_oversold, (rows,)),
                        (build_earnings_miss, (rows,))):
        st, n = build(*args)
        st["short"] = SHORT_NAME.get(st["key"], st["name"])
        strategies.append(st)
        counts[st["key"]] = n
        print(f"[info] {st['key']}: {n}종목 충족", file=sys.stderr)

    table = [table_row(r) for r in rows]
    table.sort(key=lambda r: (r["scores"]["composite"] is None,
                              -(r["scores"]["composite"] or 0)))

    tally = {"buy": 0, "watch": 0, "exit": 0, "none": 0}
    for r in table:
        tally[r["signal"]["state"] if r.get("signal") else "none"] += 1
    print(f"[info] 신호: 매수 {tally['buy']} · 관망 {tally['watch']} · "
          f"청산 {tally['exit']} · 판단불가 {tally['none']}", file=sys.stderr)

    ext = extremes(rows)
    print(f"[info] 52주 신고가 {len(ext['high'])} · 신저가 {len(ext['low'])}종목", file=sys.stderr)

    soon = earnings_soon(rows)
    print(f"[info] 7일 내 실적 발표 {len(soon)}종목", file=sys.stderr)

    changes, since = signal_changes(table, opts.market)
    print(f"[info] 오늘 바뀐 신호 {len(changes)}종목 (기준 {since or '없음'})", file=sys.stderr)

    day = [r.get("day_pct") for r in rows if r.get("day_pct") is not None]
    by_day = sorted((r for r in rows if r.get("day_pct") is not None),
                    key=lambda r: r["day_pct"])
    mover = lambda r: {"ticker": r["ticker"], "name": r["name"],
                       "last": f(r.get("last"), 2), "day_pct": f(r.get("day_pct"), 1)}
    market = {
        "advancers": sum(1 for v in day if v > 0),
        "decliners": sum(1 for v in day if v < 0),
        "median_day_pct": f(float(np.median(day)), 2) if day else None,
        "top_gainers": [mover(r) for r in by_day[::-1][:8]],
        "top_losers": [mover(r) for r in by_day[:8]],
    }

    fx = None
    try:
        fx = json.loads((DATA / f"technicals{SFX}.json").read_text()).get("fx")
    except Exception:
        pass

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fx": fx,
        "market": market,
        "technicals_at": tech_at,
        "fundamentals_at": fund_at,
        "market_code": opts.market,
        "currency": CURRENCY,
        "universe_size": len(rows),
        "disclaimer": DISCLAIMER,
        "earnings_soon": soon,
        "extremes": ext,
        "signal": {"rules": SIGNAL_RULES, "levels": SIGNAL_LEVELS, "tally": tally,
                   "changes": changes, "since": since,
                   "labels": SIGNAL_LABEL, "reasons": SIGNAL_REASON,
                   "rsi_max": RSI_MAX, "split": [SPLIT_LO, SPLIT_HI]},
        "strategies": strategies,
        "table": table,
    }
    text = json.dumps(out, ensure_ascii=False, indent=1, allow_nan=False)
    outp = DATA / f"discover{SFX}.json"
    outp.write_text(text + "\n")
    print(f"[info] wrote {outp} ({len(text)/1024:.0f} KB)", file=sys.stderr)
    return counts


if __name__ == "__main__":
    main()
