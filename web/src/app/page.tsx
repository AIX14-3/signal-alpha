import { getDashboardPlaceholder } from "@/lib/apiClient";

const targetStocks = [
  {
    stockCode: "005930",
    stockName: "삼성전자",
    alignmentRate: "준비 중",
    status: "대시보드 자리"
  },
  {
    stockCode: "000660",
    stockName: "SK하이닉스",
    alignmentRate: "준비 중",
    status: "대시보드 자리"
  },
  {
    stockCode: "035420",
    stockName: "네이버",
    alignmentRate: "준비 중",
    status: "대시보드 자리"
  }
];

export default function DashboardPage() {
  const apiPlaceholder = getDashboardPlaceholder();

  return (
    <section>
      <div className="dashboard-header">
        <div>
          <p className="eyebrow">관심 종목 대시보드</p>
          <h1 className="title">Signal Alpha 소스 방향성 일치도</h1>
          <p className="description">
            공시, 증권사 리포트, Alternative Data를 교차검증해 여러 데이터
            소스가 같은 방향을 가리키는지와 근거를 확인하는 초기 화면입니다.
          </p>
        </div>

        <aside className="status-card" aria-label="앱 상태">
          <p className="status-label">초기 렌더링</p>
          <p className="status-value">Ready</p>
        </aside>
      </div>

      <div className="card-grid" aria-label="MVP 관심 종목">
        {targetStocks.map((stock) => (
          <article className="stock-card" key={stock.stockCode}>
            <p className="stock-code">{stock.stockCode}</p>
            <h2 className="stock-name">{stock.stockName}</h2>
            <div className="metric-row">
              <div className="metric">
                <p className="metric-label">일치도</p>
                <p className="metric-value">{stock.alignmentRate}</p>
              </div>
              <div className="metric">
                <p className="metric-label">상태</p>
                <p className="metric-value">{stock.status}</p>
              </div>
            </div>
          </article>
        ))}
      </div>

      <p className="api-note">
        API 클라이언트 자리: {apiPlaceholder}. 이후 main-server의 실제 API로
        연결합니다.
      </p>
    </section>
  );
}
