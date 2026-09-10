"use client";
/** 입력 누락·형식 오류를 **그 칸 옆에** 붉게 보여 주는 공통 훅 (2026-09-10 지시).
 *
 *  배경: 폼마다 검증 방식이 달라 어떤 곳은 버튼이 조용히 비활성이고, 어떤 곳은 메시지가 카드 밖에 떠서
 *  무엇을 빠뜨렸는지 알 수 없었다. 모든 폼이 같은 규칙을 쓰도록 한 곳에 모은다.
 *
 *  쓰는 법:
 *    const fe = useFieldErrors();
 *    const bad = fe.validate({ qty: !qty ? "수량을 넣으세요" : "", price: Number(price) > 0 ? "" : "0보다 큰 수" });
 *    if (bad) { setMsg(`입력을 확인하세요 — ${bad}`); return; }
 *    <input className={`input${fe.cls("qty")}`} onChange={(e) => { setQty(e.target.value); fe.clear("qty"); }} />
 *    {fe.msg("qty")}
 */
import { useState } from "react";

export type FieldRules = Record<string, string | false | null | undefined>;

export function useFieldErrors() {
  const [err, setErr] = useState<Record<string, string>>({});
  /** 규칙을 검사해 표시를 켜고, 문제가 있으면 사유를 이어 붙인 문자열을, 없으면 "" 을 돌려준다. */
  const validate = (rules: FieldRules): string => {
    const next: Record<string, string> = {};
    for (const [k, v] of Object.entries(rules)) if (v) next[k] = v;
    setErr(next);
    return Object.values(next).join(" · ");
  };
  return {
    err,
    validate,
    reset: () => setErr({}),
    clear: (k: string) => setErr((e) => (e[k] ? { ...e, [k]: "" } : e)),
    /** 입력 className 뒤에 붙인다 — 오류면 붉은 테두리 */
    cls: (k: string) => (err[k] ? " !border-down focus:!border-down" : ""),
    /** 칸 아래 사유 한 줄 */
    msg: (k: string) => (err[k] ? <span className="text-[11.5px] font-medium text-down">{err[k]}</span> : null),
    has: Object.values(err).some(Boolean),
  };
}
