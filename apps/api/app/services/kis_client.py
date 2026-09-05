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
            if resp.status_code >= 500:
                # KIS 는 자격·입력 오류도 500 으로 준다 — 본문에 유량(EGW00201) 외의 코드가 있으면
                # 재시도해도 결과가 같으므로 즉시 사유를 알린다 (2026-09-05: EGW00304 를 15초 재시도하던 문제)
                try:
                    err = resp.json()
                except ValueError:
                    err = None
                code = str((err or {}).get("msg_cd") or "").strip()
                if code and code != "EGW00201":
                    raise KisError(f"KIS error {code} {str((err or {}).get('msg1') or '').strip()}")
                if attempt < _RETRIES:
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
    """계좌 클라이언트 — 조회 TR + **예약주문** TR (2026-09-05 지시로 조회 전용 원칙 변경).

    주문은 사용자가 주문표에서 버튼을 눌러 명시적으로 접수하는 예약주문(CTSC0008U)만 구현한다.
    정규 장중 주문(order-cash)은 도입하지 않는다. 모의투자(vps)는 예약주문 TR 이 없다.
    """

    def __init__(self, auth: KisAuth, cano: str, acnt_prdt_cd: str = "01",
                 session: requests.Session | None = None) -> None:
        super().__init__(auth, session)
        self.cano = cano
        self.acnt_prdt_cd = acnt_prdt_cd

    def probe_balance(self, prdt: str | None = None) -> dict:
        """잔고 1회 조회 — 자격·계좌 유효성 확인용. 실패 시 KisError 를 그대로 올린다."""
        env = self.auth.env if self.auth.env in ("prod", "vps") else "prod"
        body = self._get(BALANCE_PATH, BALANCE_TR[env],
                         _balance_probe_params(self.cano, prdt or self.acnt_prdt_cd))
        holdings = [r for r in (body.get("output1") or []) if _to_int(_first(r, "hldg_qty")) > 0]
        summary = (body.get("output2") or [{}])
        summary = summary[0] if isinstance(summary, list) and summary else {}
        return {
            "ok": True,
            "holdings": len(holdings),
            "deposit": _to_int(_first(summary, "dnca_tot_amt", "prvs_rcdl_excc_amt")),
            "total_eval": _to_int(_first(summary, "tot_evlu_amt", "evlu_amt_smtl_amt")),
        }

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

    # ── 예약주문 (2026-09-05 지시) — 접수 15:40~다음 영업일 07:30, 장 시작 시 자동 주문 ──
    def _post(self, path: str, tr_id: str, body: dict[str, str]) -> dict:
        """주문 계열 POST — 재시도하지 않는다(중복 접수 방지). rt_cd != "0" 은 KisError."""
        self._throttle()
        resp = self.session.post(self.auth.base_url + path,
                                 headers=self.auth.headers(tr_id, self.session), json=body, timeout=10)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code != 200 or str(data.get("rt_cd")) != "0":
            code = str(data.get("msg_cd") or "").strip()
            msg = str(data.get("msg1") or resp.text[:120]).strip()
            raise KisError(f"KIS error {code} {msg} (HTTP {resp.status_code})".replace("  ", " "))
        return data

    def reserve_order(self, code: str, side: str, qty: int, price: int | None,
                      end_date: date | None = None) -> dict:
        """국내주식 예약주문 접수 (CTSC0008U). price None/0 = 시장가.

        반환 {"rsvn_ord_seq": 예약주문순번, "msg": KIS 메시지, "raw": output}.
        """
        if self.auth.env == "vps":
            raise KisError("모의투자 계좌는 예약주문을 지원하지 않습니다 — 실전 계좌를 연결하세요")
        if qty <= 0:
            raise KisError("주문 수량이 0 입니다")
        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "PDNO": code,
            "ORD_QTY": str(int(qty)), "ORD_UNPR": str(int(price or 0)),
            "SLL_BUY_DVSN_CD": "02" if side == "buy" else "01",   # 02 매수 / 01 매도
            "ORD_DVSN_CD": "00" if price else "01",               # 00 지정가 / 01 시장가
            "ORD_OBJT_CBLC_DVSN_CD": "10",                        # 10 현금
        }
        if end_date is not None:
            body["RSVN_ORD_END_DT"] = end_date.strftime("%Y%m%d")  # 기간예약 (최대 30일)
        data = self._post(RESV_ORDER_PATH, RESV_ORDER_TR, body)
        out = data.get("output") or {}
        if isinstance(out, list):
            out = out[0] if out else {}
        seq = str(_first(out, "RSVN_ORD_SEQ", "rsvn_ord_seq")).strip()
        return {"rsvn_ord_seq": seq, "msg": str(data.get("msg1") or "").strip(), "raw": out}

    def cancel_reserved_order(self, rsvn_ord_seq: str, ord_dt: date, orgno: str = "") -> dict:
        """예약주문 취소 (CTSC0009U). 정정은 지원하지 않는다 — 취소 후 재접수."""
        body = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "RSVN_ORD_SEQ": str(rsvn_ord_seq), "RSVN_ORD_ORGNO": orgno or "",
                "RSVN_ORD_ORD_DT": ord_dt.strftime("%Y%m%d")}
        data = self._post(RESV_CANCEL_PATH, RESV_CANCEL_TR, body)
        out = data.get("output") or {}
        return {"msg": str(data.get("msg1") or "").strip(), "raw": out if isinstance(out, dict) else {}}

    def list_reserved_orders(self, start: date, end: date, include_cancelled: bool = True) -> list[dict]:
        """예약주문 조회 (CTSC0004R) — 접수일 [start, end]. 첫 페이지(최대 수십 건)만 읽는다."""
        body = self._get(RESV_LIST_PATH, RESV_LIST_TR, {
            "RSVN_ORD_ORD_DT": start.strftime("%Y%m%d"), "RSVN_ORD_END_DT": end.strftime("%Y%m%d"),
            "TMNL_MDIA_KIND_CD": "00", "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PRCS_DVSN_CD": "0", "CNCL_YN": "Y" if include_cancelled else "N",
            "RSVN_ORD_SEQ": "", "PDNO": "", "SLL_BUY_DVSN_CD": "",
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        })
        rows = body.get("output") or []
        if isinstance(rows, dict):
            rows = [rows]
        out = []
        for r in rows:
            out.append({
                "rsvn_ord_seq": _first(r, "rsvn_ord_seq", "RSVN_ORD_SEQ"),
                "ord_dt": _first(r, "rsvn_ord_ord_dt", "RSVN_ORD_ORD_DT"),
                "rcit_dt": _first(r, "rsvn_ord_rcit_dt", "RSVN_ORD_RCIT_DT"),
                "code": _first(r, "pdno", "PDNO"), "name": _first(r, "kor_item_shtn_name"),
                "side": "buy" if _first(r, "sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD") == "02" else "sell",
                "qty": _to_int(_first(r, "ord_rsvn_qty", "ORD_RSVN_QTY")),
                "price": _to_int(_first(r, "ord_rsvn_unpr", "ORD_RSVN_UNPR")),
                "filled_qty": _to_int(_first(r, "tot_ccld_qty", "TOT_CCLD_QTY")),
                "filled_amt": _to_int(_first(r, "tot_ccld_amt", "TOT_CCLD_AMT")),
                "cancel_dt": _first(r, "cncl_ord_dt", "CNCL_ORD_DT"),
                "order_no": _first(r, "odno", "ODNO"),
                "result": _first(r, "prcs_rslt", "PRCS_RSLT"),
                "ord_dvsn": _first(r, "ord_dvsn_cd", "ORD_DVSN_CD"),
            })
        return out


# ── 예약주문 TR (koreainvestment/open-trading-api 공식 예제 기준, 2026-09-05) ─────────
RESV_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-resv"
RESV_ORDER_TR = "CTSC0008U"
RESV_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-resv-rvsecncl"
RESV_CANCEL_TR = "CTSC0009U"
RESV_LIST_PATH = "/uapi/domestic-stock/v1/trading/order-resv-ccnl"
RESV_LIST_TR = "CTSC0004R"

# ── 잔고 조회 (연결 확인·계좌 탐색용, 2026-09-05) ──────────────────────────────
BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
BALANCE_TR = {"prod": "TTTC8434R", "vps": "VTTC8434R"}


def _balance_probe_params(cano: str, prdt: str) -> dict[str, str]:
    return {"CANO": cano, "ACNT_PRDT_CD": prdt, "AFHR_FLPR_YN": "N", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
