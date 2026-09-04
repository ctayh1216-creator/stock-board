# stock-board

자동 생성되는 정적 사이트 + 규칙 기반 주식 스크리너입니다. 모든 페이지와 JSON은 스크립트가 만들어 커밋한 결과물이며, 사람이 직접 편집하지 않습니다.

> **면책**: 이 저장소의 모든 내용은 공개 데이터를 기계적으로 가공한 것으로, 투자 권유나 종목 추천이 아닙니다. 투자 판단과 그 결과에 대한 책임은 전적으로 이용자 본인에게 있습니다.

## 퍼블리시 구조

두 개의 워크플로가 이 저장소에 커밋하는 방식으로 배포합니다.

1. **이 저장소의 `discover` 워크플로** (`.github/workflows/discover.yml`)
   - 평일 장중(UTC 13–21시) 매시 7·22·37·52분, 그리고 장 마감 후 21:40 UTC에 실행됩니다.
   - `src/technicals.py` → `src/fundamentals_daily.py` → `src/discover.py` → `src/build_discover.py` 순으로 실행해 `data/*.json` 과 `discover.html` 을 갱신하고, 기본 제공 `GITHUB_TOKEN` 으로 자기 자신에게 커밋·푸시합니다.
2. **별도 비공개 저장소의 워크플로**
   - 15분 간격(UTC 13–21시, `*/15`)으로 `index.html` 과 `data.json` 을 이 저장소로 푸시합니다.
   - 두 워크플로의 푸시가 겹칠 수 있어, `discover` 는 7분 오프셋 크론과 `git pull --rebase` 재시도 루프로 경합을 처리합니다.

## 실행 주기 바꾸기

`.github/workflows/discover.yml` 의 `on.schedule` 크론을 수정하면 됩니다. 비공개 저장소 쪽 퍼블리셔가 `*/15` 로 돌기 때문에, 같은 분(0·15·30·45분)을 피해서 오프셋을 두는 것을 권장합니다.

## 유니버스 갱신

`data/universe_sp500.json` 은 커밋된 소스 데이터이며 CI에서는 절대 재생성하지 않습니다. 구성 종목을 갱신하려면 로컬에서 아래를 실행한 뒤 결과를 커밋하세요.

```bash
python src/fetch_universe.py
```

## 디자인
화면 규칙은 [DESIGN.md](DESIGN.md) 를 따릅니다. 색·서체·여백·글쓰기 원칙이 정리되어 있습니다.
