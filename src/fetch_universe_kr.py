#!/usr/bin/env python
"""fetch_universe_kr.py — 한국 주요 종목 목록을 만든다.

KRX 공식 목록 API가 인증을 요구해, 시가총액 상위 종목을 직접 관리한다.
시세·재무는 yfinance 로 받으므로 티커는 야후 표기(005930.KS / 247540.KQ)를 쓴다.
분기마다 한 번씩 손으로 갱신하면 충분하다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "universe_kr.json"

# (티커, 종목명, 섹터)
ROWS = [
 ("005930.KS","삼성전자","Technology"),("000660.KS","SK하이닉스","Technology"),
 ("373220.KS","LG에너지솔루션","Industrials"),("207940.KS","삼성바이오로직스","Healthcare"),
 ("005380.KS","현대차","Consumer Cyclical"),("000270.KS","기아","Consumer Cyclical"),
 ("068270.KS","셀트리온","Healthcare"),("005490.KS","POSCO홀딩스","Basic Materials"),
 ("035420.KS","NAVER","Communication Services"),("051910.KS","LG화학","Basic Materials"),
 ("006400.KS","삼성SDI","Technology"),("028260.KS","삼성물산","Industrials"),
 ("105560.KS","KB금융","Financial Services"),("055550.KS","신한지주","Financial Services"),
 ("012330.KS","현대모비스","Consumer Cyclical"),("035720.KS","카카오","Communication Services"),
 ("003670.KS","포스코퓨처엠","Basic Materials"),("086790.KS","하나금융지주","Financial Services"),
 ("015760.KS","한국전력","Utilities"),("032830.KS","삼성생명","Financial Services"),
 ("316140.KS","우리금융지주","Financial Services"),("009150.KS","삼성전기","Technology"),
 ("017670.KS","SK텔레콤","Communication Services"),("030200.KS","KT","Communication Services"),
 ("018260.KS","삼성에스디에스","Technology"),("011200.KS","HMM","Industrials"),
 ("034730.KS","SK","Industrials"),("010130.KS","고려아연","Basic Materials"),
 ("090430.KS","아모레퍼시픽","Consumer Defensive"),("051900.KS","LG생활건강","Consumer Defensive"),
 ("011170.KS","롯데케미칼","Basic Materials"),("047810.KS","한국항공우주","Industrials"),
 ("010950.KS","S-Oil","Energy"),("096770.KS","SK이노베이션","Energy"),
 ("267250.KS","HD현대","Industrials"),("329180.KS","HD현대중공업","Industrials"),
 ("042660.KS","한화오션","Industrials"),("012450.KS","한화에어로스페이스","Industrials"),
 ("064350.KS","현대로템","Industrials"),("079550.KS","LIG넥스원","Industrials"),
 ("259960.KS","크래프톤","Communication Services"),("036570.KS","엔씨소프트","Communication Services"),
 ("251270.KS","넷마블","Communication Services"),("352820.KS","하이브","Communication Services"),
 ("041510.KQ","에스엠","Communication Services"),("122870.KQ","와이지엔터테인먼트","Communication Services"),
 ("035900.KQ","JYP Ent.","Communication Services"),
 ("247540.KQ","에코프로비엠","Basic Materials"),("086520.KQ","에코프로","Basic Materials"),
 ("091990.KQ","셀트리온헬스케어","Healthcare"),("196170.KQ","알테오젠","Healthcare"),
 ("328130.KQ","루닛","Healthcare"),("145020.KQ","휴젤","Healthcare"),
 ("214450.KQ","파마리서치","Healthcare"),("068760.KQ","셀트리온제약","Healthcare"),
 ("326030.KS","SK바이오팜","Healthcare"),("128940.KS","한미약품","Healthcare"),
 ("000100.KS","유한양행","Healthcare"),("069620.KS","대웅제약","Healthcare"),
 ("302440.KS","SK바이오사이언스","Healthcare"),
 ("066570.KS","LG전자","Consumer Cyclical"),("034220.KS","LG디스플레이","Technology"),
 ("108320.KQ","LX세미콘","Technology"),("240810.KQ","원익IPS","Technology"),
 ("357780.KQ","솔브레인","Technology"),("058470.KQ","리노공업","Technology"),
 ("039030.KQ","이오테크닉스","Technology"),("140860.KQ","파크시스템스","Technology"),
 ("095340.KQ","ISC","Technology"),("178920.KQ","PI첨단소재","Basic Materials"),
 ("042700.KS","한미반도체","Technology"),("000990.KS","DB하이텍","Technology"),
 ("036930.KQ","주성엔지니어링","Technology"),("084370.KQ","유진테크","Technology"),
 ("098460.KQ","고영","Technology"),("222080.KQ","씨아이에스","Industrials"),
 ("112610.KS","씨에스윈드","Industrials"),("267260.KS","HD현대일렉트릭","Industrials"),
 ("010120.KS","LS ELECTRIC","Industrials"),("006260.KS","LS","Industrials"),
 ("241560.KS","두산밥캣","Industrials"),("034020.KS","두산에너빌리티","Industrials"),
 ("000150.KS","두산","Industrials"),("009540.KS","HD한국조선해양","Industrials"),
 ("010140.KS","삼성중공업","Industrials"),("003490.KS","대한항공","Industrials"),
 ("020560.KS","아시아나항공","Industrials"),
 ("139480.KS","이마트","Consumer Defensive"),("023530.KS","롯데쇼핑","Consumer Cyclical"),
 ("097950.KS","CJ제일제당","Consumer Defensive"),("271560.KS","오리온","Consumer Defensive"),
 ("033780.KS","KT&G","Consumer Defensive"),("004370.KS","농심","Consumer Defensive"),
 ("280360.KS","롯데웰푸드","Consumer Defensive"),("007310.KS","오뚜기","Consumer Defensive"),
 ("003230.KS","삼양식품","Consumer Defensive"),("005300.KS","롯데칠성","Consumer Defensive"),
 ("161890.KS","한국콜마","Consumer Defensive"),("192820.KS","코스맥스","Consumer Defensive"),
 ("237880.KQ","클리오","Consumer Defensive"),("241710.KQ","코스메카코리아","Consumer Defensive"),
 ("018880.KS","한온시스템","Consumer Cyclical"),("204320.KS","HL만도","Consumer Cyclical"),
 ("161390.KS","한국타이어앤테크놀로지","Consumer Cyclical"),("073240.KS","금호타이어","Consumer Cyclical"),
 ("011790.KS","SKC","Basic Materials"),("298050.KS","효성첨단소재","Basic Materials"),
 ("001570.KS","금양","Basic Materials"),("004020.KS","현대제철","Basic Materials"),
 ("103140.KS","풍산","Basic Materials"),("014680.KS","한솔케미칼","Basic Materials"),
 ("120110.KS","코오롱인더","Basic Materials"),("285130.KS","SK케미칼","Basic Materials"),
 ("009830.KS","한화솔루션","Basic Materials"),("456040.KS","OCI","Basic Materials"),
 ("047050.KS","포스코인터내셔널","Industrials"),("001040.KS","CJ","Industrials"),
 ("180640.KS","한진칼","Industrials"),("000720.KS","현대건설","Industrials"),
 ("006360.KS","GS건설","Industrials"),("028050.KS","삼성E&A","Industrials"),
 ("047040.KS","대우건설","Industrials"),("375500.KS","DL이앤씨","Industrials"),
 ("086280.KS","현대글로비스","Industrials"),("088980.KS","맥쿼리인프라","Real Estate"),
 ("293940.KS","신한알파리츠","Real Estate"),("365550.KS","ESR켄달스퀘어리츠","Real Estate"),
 ("323410.KS","카카오뱅크","Financial Services"),("377300.KS","카카오페이","Financial Services"),
 ("071050.KS","한국금융지주","Financial Services"),("016360.KS","삼성증권","Financial Services"),
 ("006800.KS","미래에셋증권","Financial Services"),("039490.KS","키움증권","Financial Services"),
 ("029780.KS","삼성카드","Financial Services"),("024110.KS","기업은행","Financial Services"),
 ("138040.KS","메리츠금융지주","Financial Services"),("000810.KS","삼성화재","Financial Services"),
 ("001450.KS","현대해상","Financial Services"),("005830.KS","DB손해보험","Financial Services"),
 ("088350.KS","한화생명","Financial Services"),
 ("053800.KQ","안랩","Technology"),("095660.KQ","네오위즈","Communication Services"),
 ("263750.KQ","펄어비스","Communication Services"),("293490.KQ","카카오게임즈","Communication Services"),
 ("112040.KQ","위메이드","Communication Services"),("194480.KQ","데브시스터즈","Communication Services"),
 ("376300.KQ","디어유","Communication Services"),("053270.KQ","구영테크","Consumer Cyclical"),
 ("214320.KQ","이노션","Communication Services"),("030000.KS","제일기획","Communication Services"),
 ("282330.KS","BGF리테일","Consumer Defensive"),("007070.KS","GS리테일","Consumer Defensive"),
 ("069960.KS","현대백화점","Consumer Cyclical"),("004170.KS","신세계","Consumer Cyclical"),
 ("008770.KS","호텔신라","Consumer Cyclical"),("034230.KQ","파라다이스","Consumer Cyclical"),
 ("035250.KS","강원랜드","Consumer Cyclical"),("114090.KQ","GKL","Consumer Cyclical"),
 ("036460.KS","한국가스공사","Utilities"),("267290.KS","경동도시가스","Utilities"),
 ("051600.KS","한전KPS","Utilities"),("052690.KS","한전기술","Utilities"),
]


def main() -> int:
    seen, rows = set(), []
    for t, n, sec in ROWS:
        if t in seen:
            print(f"중복 제거: {t}", file=sys.stderr)
            continue
        seen.add(t)
        rows.append({"ticker": t, "name": n, "sector": sec})
    rows.sort(key=lambda r: r["ticker"])

    sectors = {r["sector"] for r in rows}
    if len(rows) < 100:
        sys.exit(f"종목이 너무 적습니다: {len(rows)}")
    out = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "market": "KR", "currency": "KRW", "count": len(rows), "rows": rows}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"[universe_kr] {len(rows)}종목 / {len(sectors)}섹터 → {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
