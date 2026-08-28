"""pykrx 보조 소스 — 10년 시딩·결측 보충·거래일 캘린더 (ADR-004).

pykrx는 비공식 KRX 데이터 기반이므로 반환값은 반드시 검증 규칙을 거쳐 적재한다.
"""
from __future__ import annotations

from datetime import date


def fetch_daily(code: str, start: date, end: date) -> list[dict]:
    """원주가(adjusted=False) 일봉. 반환: [{trade_date, open, high, low, close, volume}]"""
    from pykrx import stock  # 지연 임포트 — 테스트에서 mock 가능

    df = stock.get_market_ohlcv(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code, adjusted=False
    )
    bars: list[dict] = []
    for idx, row in df.iterrows():
        # 거래정지일은 KRX가 O/H/L 를 0으로 반환하기도 함 → 검증 단계에서 거부됨
        bars.append(
            dict(
                trade_date=idx.date(),
                open=int(row["시가"]),
                high=int(row["고가"]),
                low=int(row["저가"]),
                close=int(row["종가"]),
                volume=int(row["거래량"]),
            )
        )
    return bars


def fetch_etf_name(code: str) -> str:
    from pykrx import stock

    return stock.get_etf_ticker_name(code)


def fetch_trading_days(start: date, end: date, proxy_code: str = "069500") -> set[date]:
    """거래일 집합 — KODEX 200 일봉 존재일을 프록시로 사용 (KRX 휴장 캘린더 단일 소스)."""
    return {b["trade_date"] for b in fetch_daily(proxy_code, start, end)}
