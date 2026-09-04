#!/usr/bin/env python
"""logos.py — 종목 로고를 한 번만 내려받아 assets/logos/ 에 보관한다.

방문자 브라우저가 외부 로고 서버로 직접 요청하지 않도록(속도·프라이버시·서비스
중단 위험) 빌드 시점에 받아 저장소에 함께 배포한다. 이미 있는 파일은 건너뛴다.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "assets" / "logos"
SRC = "https://assets.parqet.com/logos/symbol/{t}?format=png&size=128"
UA = {"User-Agent": "Mozilla/5.0 (compatible; stock-board/1.0)"}
MIN_BYTES = 200


def fetch(ticker: str) -> bytes | None:
    req = urllib.request.Request(SRC.format(t=ticker), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            if r.status != 200:
                return None
            b = r.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    # PNG 시그니처 + 최소 크기 확인 (플레이스홀더/에러 페이지 방지)
    if len(b) < MIN_BYTES or not b.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return b


def main() -> int:
    tickers = [r["ticker"] for r in
               json.loads((DATA / "universe_sp500.json").read_text())["rows"]]
    OUT.mkdir(parents=True, exist_ok=True)

    have = sum(1 for t in tickers if (OUT / f"{t}.png").exists())
    todo = [t for t in tickers if not (OUT / f"{t}.png").exists()]
    print(f"[logos] 보유 {have} / 대상 {len(tickers)} → {len(todo)}개 수집", file=sys.stderr)

    got, miss = 0, []
    for i, t in enumerate(todo):
        b = fetch(t)
        if b:
            (OUT / f"{t}.png").write_bytes(b)
            got += 1
        else:
            miss.append(t)
        if i % 50 == 49:
            print(f"[logos] {i + 1}/{len(todo)} (성공 {got})", file=sys.stderr)
        time.sleep(0.08)

    (OUT / "_missing.json").write_text(json.dumps(sorted(miss), ensure_ascii=False))
    size = sum(f.stat().st_size for f in OUT.glob("*.png")) / 1024 / 1024
    print(f"[logos] 완료 — 성공 {got}, 없음 {len(miss)}, 총 {size:.1f}MB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
