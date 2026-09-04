#!/usr/bin/env python
"""build_discover.py — discover_template.html → index.html (사이트 루트).

템플릿이 자체 완결(폰트·스타일 포함)이라 복사만 한다.
예전 주소(discover.html)로 들어온 방문자를 위해 리다이렉트 페이지도 함께 만든다.
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "discover_template.html"
OUT = ROOT / "index.html"
LEGACY = ROOT / "discover.html"
SITE = "https://ctayh1216-creator.github.io/stock-board/"

if not SRC.exists():
    sys.exit(f"템플릿이 없습니다: {SRC}")
shutil.copyfile(SRC, OUT)

LEGACY.write_text(
    '<!doctype html>\n<html lang="ko">\n<meta charset="utf-8">\n'
    f'<meta http-equiv="refresh" content="0; url={SITE}">\n'
    f'<link rel="canonical" href="{SITE}">\n'
    '<title>종목 발굴</title>\n'
    f'<p>새 주소로 이동합니다 — <a href="{SITE}">{SITE}</a></p>\n</html>\n'
)
print(f"[build_discover] wrote {OUT} ({OUT.stat().st_size:,} bytes) + legacy redirect",
      file=sys.stderr)
