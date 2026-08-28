/**
 * 지표 교차 검증 — 파이썬(app/strategy/indicators.py)이 생성한 픽스처와 대조.
 * 오차 허용 1e-8 (feature-chart §12). 픽스처 재생성:
 *   cd apps/api && PYTHONPATH=. python -m scripts.gen_indicator_fixture
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { atr, ema, rsi, sma } from "../lib/indicators";

const fixture = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "indicators.json"), "utf-8"),
) as {
  input: { high: number[]; low: number[]; close: number[] };
  expected: Record<string, (number | null)[]>;
};

function compare(actual: (number | null)[], expected: (number | null)[]) {
  expect(actual.length).toBe(expected.length);
  for (let i = 0; i < actual.length; i++) {
    if (expected[i] === null) {
      expect(actual[i]).toBeNull();
    } else {
      expect(Math.abs((actual[i] as number) - (expected[i] as number))).toBeLessThan(1e-8);
    }
  }
}

describe("indicator cross-validation (py <-> ts)", () => {
  const { high, low, close } = fixture.input;

  it("sma20 matches python", () => compare(sma(close, 20), fixture.expected.sma20));
  it("ema20 matches python", () => compare(ema(close, 20), fixture.expected.ema20));
  it("atr20 matches python", () => compare(atr(high, low, close, 20), fixture.expected.atr20));
  it("rsi14 matches python", () => compare(rsi(close, 14), fixture.expected.rsi14));
});
