import Link from "next/link";

const MENUS = [
  { href: "/dashboard", label: "대시보드", desc: "총자산 · 레짐 게이지 · 손익 캘린더" },
  { href: "/chart", label: "차트", desc: "캔들 · 지표 · 드로잉" },
  { href: "/simulator", label: "시뮬레이터", desc: "RAVG v2 백테스트 · 절제 비교" },
  { href: "/signals", label: "주문표", desc: "다음 거래일 지정가 주문 · 조건부 지시문" },
  { href: "/portfolio", label: "실전매매", desc: "체결 기록 · FIFO 손익 · TWR/XIRR" },
  { href: "/login", label: "로그인", desc: "가입 · 세션" },
];

export default function Home() {
  return (
    <main style={{ display: "grid", placeItems: "center", minHeight: "100vh", padding: 16 }}>
      <div style={{ maxWidth: 560, width: "100%" }}>
        <h1 style={{ fontSize: "2rem", marginBottom: 4 }}>StockLab</h1>
        <p style={{ opacity: 0.7, marginBottom: 4 }}>검증(백테스트) → 실행(주문표·실전 기록) → 관리(대시보드)</p>
        <p style={{ opacity: 0.5, fontSize: "0.85rem", marginBottom: 20 }}>모의·과거 데이터 기반이며 투자 권유가 아닙니다.</p>
        <nav style={{ display: "grid", gap: 8 }}>
          {MENUS.map((m) => (
            <Link key={m.href} href={m.href}
              style={{ background: "#1a1a22", border: "1px solid #33333f", borderRadius: 8, padding: "12px 16px", color: "#e6e6ea", textDecoration: "none", display: "flex", justifyContent: "space-between" }}>
              <b>{m.label}</b><span style={{ opacity: 0.55, fontSize: 13 }}>{m.desc}</span>
            </Link>
          ))}
        </nav>
      </div>
    </main>
  );
}
