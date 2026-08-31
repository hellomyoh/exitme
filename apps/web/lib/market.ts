/** 마켓(한국/미국) 컨텍스트 — 통화·종목 표기 헬퍼 (2026-08-31 한/미 분리). 미국 금액은 센트 정수. */

export type Market = "KR" | "US";

export const MARKET_LABEL: Record<Market, string> = { KR: "한국 주식", US: "미국 주식" };

export function marketOf(sp: { get(k: string): string | null } | null): Market {
  return sp?.get("market") === "US" ? "US" : "KR";
}

/** 금액 (KR: 원 / US: 센트→달러, 정수부) */
export function fmtMoneyM(m: Market, v: number): string {
  if (m === "US") return `$${(v / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  return `${Math.round(v).toLocaleString()}원`;
}

/** 가격 (KR: 원 / US: 센트→달러 소수 2자리) */
export function fmtPriceM(m: Market, v: number): string {
  if (m === "US") return `$${(v / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `${Math.round(v).toLocaleString()}원`;
}

/** 전략 레그 이름 */
export function legNameM(m: Market, instrument: string): string {
  if (m === "US") return instrument === "K200" ? "QQQ" : "레버리지 ETF";
  return instrument === "K200" ? "200 ETF" : "레버리지";
}

/** 거래 등록용 종목 옵션 */
export const MARKET_CODES: Record<Market, { code: string; name: string }[]> = {
  KR: [
    { code: "069500", name: "KODEX 200" },
    { code: "102110", name: "TIGER 200" },
    { code: "122630", name: "KODEX 레버리지" },
  ],
  US: [
    { code: "QQQ", name: "QQQ (나스닥100 1x)" },
    { code: "QLD", name: "QLD (2x)" },
    { code: "TQQQ", name: "TQQQ (3x)" },
  ],
};

/** 가격 입력값(사용자 표기 단위) → API 정수 단위 (KR: 원 그대로 / US: 달러→센트) */
export function priceToApi(m: Market, input: string): number {
  const n = Number(input.replaceAll(",", ""));
  return m === "US" ? Math.round(n * 100) : Math.round(n);
}

/** 시뮬레이터 자본 기본값 (KR 1억 원 / US $100,000 = 센트) */
export const DEFAULT_CAPITAL: Record<Market, number> = { KR: 100_000_000, US: 10_000_000 };
