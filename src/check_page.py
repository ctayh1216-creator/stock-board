#!/usr/bin/env python
"""check_page.py — 빌드 산출물에 필수 정의가 살아 있는지 확인한다.

CSS/JS 블록을 범위로 치환하다 인접 정의를 통째로 날린 사고가 반복돼 만든 가드.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "index.html").read_text()

REQUIRED_FN = ["recordHtml", "renderStrategies", "renderCats", "catCard", "renderNews",
               "renderPortfolio", "renderTable", "renderMarket", "logoHtml", "sparkSvg",
               "mergeLive", "marketStatus", "btCaveatHtml", "mainLabel", "price",
               "sigChip", "sigLevels", "sigRecord", "sigRules",
               "renderChanges", "chgHtml", "renderRecord", "rankRow", "epsTable",
               "sigState", "refreshSignals", "sigLabel", "sigWhy",
               "renderEarnings", "earnHtml", "transHtml",
               "renderWatch", "watchToggle", "renderExtremes", "extHtml",
               "renderSignalRecord", "btCaveatCard"]
REQUIRED_CSS = [".mkts{", ".rec{", ".cat{", ".top{", ".pick{", ".na{", ".hold{", ".cats{",
                ".sig{", ".sig-lv{", ".top-sig{", ".rk-item{", ".eps{"]
REQUIRED_ID = ["cats", "strats", "mkts", "newsList", "pfHero", "uniBody", "status",
               "sigSum", "sigRec", "watch", "chg", "earn", "ext", "recBoard", "v-record", "fxTag"]

bad = []
for fn in REQUIRED_FN:
    if f"function {fn}(" not in HTML:
        bad.append(f"함수 없음: {fn}")
for sel in REQUIRED_CSS:
    if sel not in HTML:
        bad.append(f"CSS 없음: {sel}")
for i in REQUIRED_ID:
    if f'id="{i}"' not in HTML:
        bad.append(f"엘리먼트 없음: #{i}")

# JS 안에서 호출하는데 정의가 없는 함수 잡기
called = set(re.findall(r"\b([a-zA-Z_]\w*)\(", HTML))
defined = set(re.findall(r"function\s+([a-zA-Z_]\w*)\s*\(", HTML))
for fn in REQUIRED_FN:
    if fn in called and fn not in defined:
        bad.append(f"호출되지만 정의 없음: {fn}")

if bad:
    print("\n".join("  ✗ " + b for b in bad), file=sys.stderr)
    sys.exit(1)
print(f"[check] 함수 {len(REQUIRED_FN)} · CSS {len(REQUIRED_CSS)} · 엘리먼트 {len(REQUIRED_ID)} 모두 확인",
      file=sys.stderr)
