export default function Home() {
  return (
    <main style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      <div style={{ textAlign: "center" }}>
        <h1 style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>StockLab</h1>
        <p style={{ opacity: 0.7 }}>
          검증(백테스트) → 실행(주문표·실전 기록) → 관리(대시보드)
        </p>
        <p style={{ opacity: 0.5, fontSize: "0.85rem" }}>
          모의·과거 데이터 기반이며 투자 권유가 아닙니다.
        </p>
      </div>
    </main>
  );
}
