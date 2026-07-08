"use client";

import { dateTimeShortKST } from "@/lib/format";
import { useHomeStore } from "@/stores/homeStore";

// 좌 pane(대시보드 메인) = 상단 KPI 3장 + 실시간 뉴스 "트랜잭션 테이블"(레퍼런스 Transaction History 오마주).
// 종목 검색은 헤더(HeaderStockSearch)로 승격. 뉴스 행을 고르면 우 pane 스탯 레일이 갱신된다.
export function HomeLeftPane() {
  const feed = useHomeStore((s) => s.feed);
  const summary = useHomeStore((s) => s.summary);
  const loading = useHomeStore((s) => s.loading);
  const selectedCode = useHomeStore((s) => s.selectedCode);
  const select = useHomeStore((s) => s.select);

  const kpis: { label: string; value: number | null | undefined; unit: string; icon: string }[] = [
    { label: "최근 24시간 뉴스", value: summary?.recent_articles, unit: "건", icon: "📰" },
    { label: "분석 종목", value: summary?.stock_count, unit: "개", icon: "🎯" },
    { label: "누적 분석 기사", value: summary?.total_articles, unit: "건", icon: "🗂️" },
  ];

  return (
    <div className="flex min-h-0 flex-col gap-5">
      {/* KPI 3장 — NewsSummary 실데이터 */}
      <div className="grid grid-cols-3 gap-3">
        {kpis.map((k) => (
          <div key={k.label} className="glass-card p-4">
            <div className="text-[17px]">{k.icon}</div>
            <div className="mt-2 text-[clamp(18px,2.4vw,24px)] font-extrabold leading-none text-navy">
              {loading || k.value == null ? "…" : k.value.toLocaleString()}
              <span className="ml-1 text-[12px] font-semibold text-muted">{k.unit}</span>
            </div>
            <div className="mt-1.5 text-[11.5px] text-muted">{k.label}</div>
          </div>
        ))}
      </div>

      {/* 실시간 뉴스 테이블 */}
      <div className="glass-card overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="text-[14px] font-bold text-navy">실시간 뉴스 피드</div>
          <span className="text-[11.5px] text-muted">행을 누르면 오른쪽에 분석이 열립니다</span>
        </div>
        <div className="grid grid-cols-[1fr_auto_auto] gap-x-3 border-y border-line bg-surface-2/60 px-4 py-2 text-[10.5px] font-bold uppercase tracking-wider text-muted">
          <span>뉴스</span>
          <span className="text-right">종목</span>
          <span className="text-right">시각</span>
        </div>
        {loading ? (
          <ul className="animate-pulse space-y-3 p-4">
            {[0, 1, 2, 3, 4].map((i) => (
              <li key={i} className="h-9 rounded-[8px] bg-surface-2" />
            ))}
          </ul>
        ) : feed.length === 0 ? (
          <p className="p-8 text-center text-[13px] text-muted">아직 수집된 뉴스가 없습니다.</p>
        ) : (
          <ul className="max-h-[540px] divide-y divide-line overflow-y-auto">
            {feed.map((n, i) => (
              <li key={`${n.stock_code}-${i}`}>
                <button
                  type="button"
                  onClick={() => n.stock_code && void select(n.stock_code)}
                  className={`grid w-full grid-cols-[1fr_auto_auto] items-center gap-x-3 px-4 py-3 text-left transition hover:bg-surface-2 ${
                    selectedCode === n.stock_code ? "bg-surface-2" : ""
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[13.5px] font-medium text-navy-soft">{n.title}</span>
                    {n.press && <span className="text-[11px] text-muted">{n.press}</span>}
                  </span>
                  <span
                    className="pill flat shrink-0 justify-self-end text-[11.5px] font-semibold"
                    style={{ padding: "3px 10px" }}
                  >
                    {n.stock_name ?? n.stock_code}
                  </span>
                  <span className="shrink-0 justify-self-end text-[11px] text-muted">
                    {dateTimeShortKST(n.published_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
