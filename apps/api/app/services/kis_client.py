"""KIS 국내주식 시세 클라이언트.

참고: https://github.com/koreainvestment/open-trading-api
- 일봉(기간별): GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice, TR FHKST03010100
  (한 호출당 최대 100건 → 날짜 창으로 페이지네이션)
- 현재가:      GET /uapi/domestic-stock/v1/quotations/inquire-price,            TR FHKST01010100
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import requests

from app.services.kis_auth import KisAuth

logger = logging.getLogger(__name__)

DAILY_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
DAILY_CHART_TR = "FHKST03010100"
PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
PRICE_TR = "FHKST01010100"

# 100 거래일 ≈ 5개월 미만 — 달력일 140일 창이면 항상 100건 이하
_WINDOW_DAYS = 140


@dataclass(frozen=True)
class DailyBar:
    trade_date: date
    open: int
    high: int
    low: int
    close: int
    volume: int


class KisError(RuntimeError):
    pass


class KisClient:
    def __init__(self, auth: KisAuth, session: requests.Session | None = None) -> None:
        self.auth = auth
        self.session = session or requests.Session()

    def _get(self, path: str, tr_id: str, params: dict[str, str]) -> dict:
        resp = self.session.get(
            self.auth.base_url + path,
            headers=self.auth.headers(tr_id, self.session),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        # KIS 공통: rt_cd == "0" 이 정상
        if body.get("rt_cd") != "0":
            raise KisError(f"KIS error rt_cd={body.get('rt_cd')} msg={body.get('msg1', '').strip()}")
        return body

    def fetch_daily(self, code: str, start: date, end: date, org_price: bool = True) -> list[DailyBar]:
        """일봉 조회 (원주가 기본 — 원본 보존 원칙, ADR-002).

        [start, end] 구간을 140일 창으로 나눠 호출하고 날짜 오름차순으로 합친다.
        """
        bars: dict[date, DailyBar] = {}
        win_end = end
        while win_end >= start:
            win_start = max(start, win_end - timedelta(days=_WINDOW_DAYS - 1))
            body = self._get(
                DAILY_CHART_PATH,
                DAILY_CHART_TR,
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": win_start.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": win_end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "1" if org_price else "0",
                },
            )
            for row in body.get("output2", []):
                if not row.get("stck_bsop_date"):
                    continue
                d = date(
                    int(row["stck_bsop_date"][:4]),
                    int(row["stck_bsop_date"][4:6]),
                    int(row["stck_bsop_date"][6:8]),
                )
                bars[d] = DailyBar(
                    trade_date=d,
                    open=int(row["stck_oprc"]),
                    high=int(row["stck_hgpr"]),
                    low=int(row["stck_lwpr"]),
                    close=int(row["stck_clpr"]),
                    volume=int(row["acml_vol"]),
                )
            win_end = win_start - timedelta(days=1)
        return [bars[d] for d in sorted(bars)]

    def fetch_price(self, code: str) -> dict:
        """현재가 조회 — output 원본 dict 반환 (stck_prpr 현재가, prdy_vrss 전일대비, acml_vol 거래량)."""
        body = self._get(
            PRICE_PATH,
            PRICE_TR,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        return body["output"]
