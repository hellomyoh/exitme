/**
 * 차트 표시용 지표 — 서버(app/strategy/indicators.py)와 동일 수식.
 * 교차 검증: tests/indicators.test.ts 가 파이썬 생성 픽스처와 오차 < 1e-8 대조 (feature-chart §12).
 * 수식 변경 시 반드시 양쪽(py/TS)을 함께 갱신하고 픽스처를 재생성한다.
 */

export function sma(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let acc = 0;
  for (let i = 0; i < values.length; i++) {
    acc += values[i];
    if (i >= n) acc -= values[i - n];
    if (i >= n - 1) out[i] = acc / n;
  }
  return out;
}

export function ema(values: number[], n: number): (number | null)[] {
  if (values.length === 0) return [];
  const out: (number | null)[] = new Array(values.length).fill(null);
  const alpha = 2 / (n + 1);
  let prev = values[0];
  out[0] = prev;
  for (let i = 1; i < values.length; i++) {
    prev = alpha * values[i] + (1 - alpha) * prev;
    out[i] = prev;
  }
  return out;
}

export function trueRange(high: number[], low: number[], close: number[]): number[] {
  if (high.length === 0) return [];
  const tr = [high[0] - low[0]];
  for (let i = 1; i < high.length; i++) {
    tr.push(Math.max(high[i] - low[i], Math.abs(high[i] - close[i - 1]), Math.abs(low[i] - close[i - 1])));
  }
  return tr;
}

/** Wilder ATR — atr[n-1] = mean(tr[0..n-1]), 이후 (prev*(n-1)+tr)/n */
export function atr(high: number[], low: number[], close: number[], n = 20): (number | null)[] {
  const tr = trueRange(high, low, close);
  const out: (number | null)[] = new Array(tr.length).fill(null);
  if (tr.length < n) return out;
  let prev = tr.slice(0, n).reduce((a, b) => a + b, 0) / n;
  out[n - 1] = prev;
  for (let i = n; i < tr.length; i++) {
    prev = (prev * (n - 1) + tr[i]) / n;
    out[i] = prev;
  }
  return out;
}

/** Wilder RSI */
export function rsi(close: number[], n = 14): (number | null)[] {
  const out: (number | null)[] = new Array(close.length).fill(null);
  if (close.length <= n) return out;
  const gains: number[] = [];
  const losses: number[] = [];
  for (let i = 1; i < close.length; i++) {
    const d = close[i] - close[i - 1];
    gains.push(Math.max(d, 0));
    losses.push(Math.max(-d, 0));
  }
  let avgG = gains.slice(0, n).reduce((a, b) => a + b, 0) / n;
  let avgL = losses.slice(0, n).reduce((a, b) => a + b, 0) / n;
  const calc = (g: number, l: number) => (l === 0 ? 100 : 100 - 100 / (1 + g / l));
  out[n] = calc(avgG, avgL);
  for (let i = n + 1; i < close.length; i++) {
    avgG = (avgG * (n - 1) + gains[i - 1]) / n;
    avgL = (avgL * (n - 1) + losses[i - 1]) / n;
    out[i] = calc(avgG, avgL);
  }
  return out;
}
