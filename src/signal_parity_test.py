#!/usr/bin/env python
"""signal_parity_test.py — 매매 신호 규칙이 파이썬과 화면에서 같은 답을 내는지 검사한다.

규칙은 원래 discover.py 한 군데에만 있었다. 그런데 discover.py 는 하루 한 번 돌고
시세는 3분마다 들어와서, 화면에서도 같은 규칙으로 상태를 다시 잡아야 했다
(안 그러면 손절선을 뚫고 내려간 종목이 하루 종일 «매수 구간»으로 남는다).

그래서 규칙이 두 벌 존재한다. 문구·경계값은 데이터로 내려보내 한 벌만 두었지만
비교 순서만은 양쪽에 있다 — 그 두 벌이 어긋나는 순간을 잡으려고 이 테스트를 둔다.

사용:
    python src/signal_parity_test.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from discover import RSI_MAX, signal_state  # noqa: E402

# 경계 위·아래를 모두 밟도록 값을 고른다. 100 을 기준선으로 두고 그 언저리를 훑는다.
LASTS = (80.0, 99.9, 100.0, 100.1, 105.0, 120.0)
S20S = (90.0, 100.0, 110.0)
S50S = (95.0, 100.0, 108.0)
S200S = (85.0, 100.0, 115.0)
RSIS = (25.0, 55.0, float(RSI_MAX) - 0.1, float(RSI_MAX), 85.0)

JS = r"""
const {readFileSync} = require("node:fs");
const html = readFileSync(process.argv[2], "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("script block not found"); process.exit(2); }
new Function(m[1])();
const P = globalThis.__DISCOVER_PURE__;
if (!P || !P.sigState) { console.error("sigState not exported"); process.exit(2); }
const cases = JSON.parse(readFileSync(process.argv[3], "utf8"));
console.log(JSON.stringify(cases.map(c => P.sigState(c[0], c[1], c[2], c[3], c[4], c[5]))));
"""


def main() -> int:
    cases = [[a, b, c, d, e, float(RSI_MAX)]
             for a, b, c, d, e in product(LASTS, S20S, S50S, S200S, RSIS)]
    want = [list(signal_state(a, b, c, d, e)) for a, b, c, d, e, _ in cases]

    tmp = ROOT / ".parity_cases.json"
    runner = ROOT / ".parity_runner.cjs"
    try:
        tmp.write_text(json.dumps(cases))
        runner.write_text(JS)
        out = subprocess.run(
            ["node", str(runner), str(ROOT / "discover_template.html"), str(tmp)],
            capture_output=True, text=True, check=True)
        got = json.loads(out.stdout)
    except FileNotFoundError:
        print("[parity] node 가 없어 건너뜁니다", file=sys.stderr)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"[parity] 화면 규칙 실행 실패:\n{e.stderr}", file=sys.stderr)
        return 1
    finally:
        tmp.unlink(missing_ok=True)
        runner.unlink(missing_ok=True)

    bad = [(c, w, g) for c, w, g in zip(cases, want, got) if w != g]
    if bad:
        print(f"[parity] {len(bad)}/{len(cases)}건 불일치 — 규칙 두 벌이 갈라졌습니다",
              file=sys.stderr)
        for c, w, g in bad[:5]:
            print(f"  last={c[0]} s20={c[1]} s50={c[2]} s200={c[3]} rsi={c[4]}"
                  f"  파이썬={w} 화면={g}", file=sys.stderr)
        return 1

    print(f"[parity] {len(cases)}건 모두 일치 — 파이썬과 화면의 신호 규칙이 같습니다",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
