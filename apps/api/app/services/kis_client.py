"""KIS 국내주식 시세 클라이언트.

참고: https://github.com/koreainvestment/open-trading-api
- 일봉(기간별): GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice, TR FHKST03010100
  (한 호출당 최대 100건 → 날짜 창으로 페이지네이션)
- 현재가:      GET /uapi/domestic-stock/v1/quotations/inquire-price,            TR FHKST01010100
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from app.services.kis_auth import KisAuth

logger = logging.getLogger(__name__)

DAILY_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
DAILY_CHART_TR = "FHKST03010100"
PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
PRICE_TR = "FHKST01010100"
# 주식일별분봉조회 — 과거 최대 1년 보관, 호출당 120건, 시간 커서 내림차순 (실응답 프로브로 확인)
MINUTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
MINUTE_TR = "FHKST03010230"

# 100 거래일 ≈ 5개월 미만 — 달력일 140일 창이면 항상 100건 이하
_WINDOW_DAYS = 140


@dataclass(frozen=True)
class MinuteBar:
    ts: "object"  # datetime (KST aware)
    open: int
    high: int
    low: int
    close: int
    volume: int


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


# 호출 간 최소 간격 — 실전 유량 한도(초당 20건)보다 보수적으로 초당 ~7건
_MIN_INTERVAL = 0.15
_RETRIES = 4


class KisClient:
    def __init__(self, auth: KisAuth, session: requests.Session | None = None) -> None:
        self.auth = auth
        self.session = session or requests.Session()
        self._last_call = 0.0
        self._throttle_lock = threading.Lock()

    def _throttle(self) -> None:
        with self._throttle_lock:
            wait = self._last_call + _MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _get(self, path: str, tr_id: str, params: dict[str, str]) -> dict:
        # 유량 초과 시 KIS 가 500(EGW00201)을 반환 — 스로틀 + 지수 백오프 재시도 (NOTES.md)
        for attempt in range(_RETRIES + 1):
            self._throttle()
            resp = self.session.get(
                self.auth.base_url + path,
                headers=self.auth.headers(tr_id, self.session),
                params=params,
                timeout=10,
            )
            if resp.status_code >= 500 and attempt < _RETRIES:
                delay = 1.0 * (2 ** attempt)
                logger.warning("KIS %s -> %d, retrying in %.0fs (%d/%d)",
                               path, resp.status_code, delay, attempt + 1, _RETRIES)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            body = resp.json()
            # KIS 공통: rt_cd == "0" 이 정상
            if body.get("rt_cd") != "0":
                raise KisError(f"KIS error rt_cd={body.get('rt_cd')} msg={body.get('msg1', '').strip()}")
            return body
        raise KisError("unreachable")

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

    def fetch_minutes_day(self, code: str, day: date) -> list["MinuteBar"]:
        """특정 일자의 1분봉 전체 — 15:30 부터 시간 커서를 뒤로 옮기며 페이지네이션.

        응답 필드(프로브 확인): stck_bsop_date, stck_cntg_hour(HHMMSS),
        stck_oprc/hgpr/lwpr/prpr(분 종가), cntg_vol. KIS 보관 범위(약 1년) 밖이면 빈 목록.
        """
        from datetime import datetime, timedelta, timezone

        kst = timezone(timedelta(hours=9))
        bars: dict[str, MinuteBar] = {}
        cursor = "153000"
        day_str = day.strftime("%Y%m%d")
        while True:
            body = self._get(MINUTE_PATH, MINUTE_TR, {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": day_str,
                "FID_INPUT_HOUR_1": cursor,
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_FAKE_TICK_INCU_YN": "",
            })
            rows = [r for r in body.get("output2", [])
                    if r.get("stck_bsop_date") == day_str and r.get("stck_cntg_hour")]
            if not rows:
                break
            for r in rows:
                hh = r["stck_cntg_hour"]
                ts = datetime(day.year, day.month, day.day,
                              int(hh[:2]), int(hh[2:4]), 0, tzinfo=kst)
                bars[hh] = MinuteBar(
                    ts=ts,
                    open=int(r["stck_oprc"]), high=int(r["stck_hgpr"]),
                    low=int(r["stck_lwpr"]), close=int(r["stck_prpr"]),
                    volume=int(r["cntg_vol"]),
                )
            last_hour = min(r["stck_cntg_hour"] for r in rows)
            if last_hour <= "090000" or len(rows) < 2:
                break
            # 다음 커서 = 마지막 분 − 1분
            t = datetime.strptime(last_hour, "%H%M%S") - timedelta(minutes=1)
            cursor = t.strftime("%H%M%S")
            if cursor < "090000":
                break
        return [bars[k] for k in sorted(bars)]


# ── 주문·체결 조회 (조회 전용 연동, 2026-09-05 지시) ────────────────────────────
# 공식 샘플: examples_llm/domestic_stock/inquire_daily_ccld
DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
# TR: 실전 3개월 이내/이전, 모의 3개월 이내/이전
CCLD_TR = {("prod", "recent"): "TTTC0081R", ("prod", "old"): "CTSC9215R",
           ("vps", "recent"): "VTTC0081R", ("vps", "old"): "VTSC9215R"}


@dataclass
class Execution:
    """증권사 체결 1건 — 필드명은 응답 스키마 변형에 견디도록 후보 키로 파싱한다."""

    order_no: str
    trade_date: date
    code: str
    side: str          # buy | sell
    filled_qty: int
    avg_price: int
    order_qty: int
    remain_qty: int
    name: str = ""


def _first(row: dict, *keys: str, default: str = "") -> str:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return default


def _to_int(v: str) -> int:
    try:
        return int(float(v.replace(",", ""))) if v else 0
    except ValueError:
        return 0


def parse_execution(row: dict) -> Execution | None:
    """KIS 일별주문체결 output1 행 → Execution.

    필드명이 문서 버전마다 다를 수 있어 후보 키를 순서대로 시도한다(방어적 파싱).
    체결수량 0(미체결)은 호출부에서 걸러 쓴다.
    """
    d = _first(row, "ord_dt", "ord_dt1", "trad_dt")
    code = _first(row, "pdno", "PDNO", "stck_shrn_iscd")
    if not d or len(d) != 8 or not code:
        return None
    sll_buy = _first(row, "sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD")
    side = "sell" if sll_buy == "01" else "buy"
    return Execution(
        order_no=_first(row, "odno", "ODNO", "ord_no"),
        trade_date=date(int(d[:4]), int(d[4:6]), int(d[6:8])),
        code=code,
        side=side,
        filled_qty=_to_int(_first(row, "tot_ccld_qty", "ccld_qty", "TOT_CCLD_QTY")),
        avg_price=_to_int(_first(row, "avg_prvs", "ccld_prvs", "AVG_PRVS")),
        order_qty=_to_int(_first(row, "ord_qty", "ORD_QTY")),
        remain_qty=_to_int(_first(row, "rmn_qty", "RMN_QTY")),
        name=_first(row, "prdt_name", "PRDT_NAME"),
    )


class KisTradingClient(KisClient):
    """계좌 조회 전용 클라이언트 — 주문 TR 은 구현하지 않는다(설계상 자동 발주 미도입)."""

    def __init__(self, auth: KisAuth, cano: str, acnt_prdt_cd: str = "01",
                 session: requests.Session | None = None) -> None:
        super().__init__(auth, session)
        self.cano = cano
        self.acnt_prdt_cd = acnt_prdt_cd

    def fetch_executions(self, start: date, end: date, only_filled: bool = True) -> list[Execution]:
        """[start, end] 주문·체결 내역. 연속조회(CTX) 페이지를 끝까지 따라간다."""
        env = self.auth.env if self.auth.env in ("prod", "vps") else "prod"
        recent = (date.today() - start).days <= 89
        tr = CCLD_TR[(env, "recent" if recent else "old")]
        out: list[Execution] = []
        fk = nk = ""
        for _page in range(20):  # 안전 상한
            body = self._get(DAILY_CCLD_PATH, tr, {
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "INQR_STRT_DT": start.strftime("%Y%m%d"), "INQR_END_DT": end.strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",                    # 전체
                "CCLD_DVSN": "01" if only_filled else "00",  # 01=체결
                "INQR_DVSN": "01",                           # 정순
                "INQR_DVSN_3": "00", "PDNO": "",
                "CTX_AREA_FK100": fk, "CTX_AREA_NK100": nk,
            })
            for row in (body.get("output1") or []):
                ex = parse_execution(row)
                if ex and (ex.filled_qty > 0 or not only_filled):
                    out.append(ex)
            nk_next = (body.get("ctx_area_nk100") or "").strip()
            if not nk_next or nk_next == nk:
                break
            fk, nk = (body.get("ctx_area_fk100") or "").strip(), nk_next
        return out
