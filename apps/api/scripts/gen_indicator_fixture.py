"""지표 교차 검증 픽스처 생성 — TS(vitest)가 이 JSON을 읽어 동일 입력·기대 출력으로 대조.

사용: docker compose run --rm api python -m scripts.gen_indicator_fixture
출력: apps/web/tests/fixtures/indicators.json (커밋 대상)
결정론: LCG 고정 시드 — 재실행 시 항상 같은 파일.
"""
from __future__ import annotations

import json
import pathlib

from app.strategy import indicators as ind


def lcg(seed: int = 42):
    state = seed
    while True:
        state = (state * 1103515245 + 12345) % (2**31)
        yield state / (2**31)


def make_series(n: int = 300) -> dict[str, list[float]]:
    rnd = lcg()
    close, high, low = [], [], []
    price = 70000.0
    for _ in range(n):
        price *= 1.0 + (next(rnd) - 0.5) * 0.04
        spread = price * next(rnd) * 0.02
        close.append(round(price, 4))
        high.append(round(price + spread, 4))
        low.append(round(price - spread, 4))
    return {"high": high, "low": low, "close": close}


def main() -> None:
    s = make_series()
    fixture = {
        "input": s,
        "expected": {
            "sma20": ind.sma(s["close"], 20),
            "ema20": ind.ema(s["close"], 20),
            "atr20": ind.atr(s["high"], s["low"], s["close"], 20),
            "rsi14": ind.rsi(s["close"], 14),
        },
    }
    web_dir = pathlib.Path(__file__).resolve().parents[2] / "web"
    # 컨테이너(/srv/app)에서는 web 디렉터리가 없음 — 임시 경로에 기록
    out = web_dir / "tests" / "fixtures" if web_dir.exists() else pathlib.Path("/srv/web-fixtures")
    out.mkdir(parents=True, exist_ok=True)
    (out / "indicators.json").write_text(json.dumps(fixture), encoding="utf-8")
    print(f"fixture written: {out / 'indicators.json'}")


if __name__ == "__main__":
    main()
