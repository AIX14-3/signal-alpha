"use client";

import Link from "next/link";
import { useEffect } from "react";
import { WatchlistButton } from "@/components/WatchlistButton";
import { type ReportSource } from "@/lib/apiClient";
import { directionLabel, safeHttpUrl, SOURCE_META, SOURCE_ORDER } from "@/lib/format";
import { useHomeStore } from "@/stores/homeStore";
import { useReportStore } from "@/stores/reportStore";

// 우 pane = 선택 종목 상세. FR-7 "왜 이 신호"(리포트 summary+소스별) + FR-6 뉴스 목록 + FR-8 관심.
export function HomeRightPane() {
  const selectedCode = useHomeStore((s) => s.selectedCode);
  const stockNews = useHomeStore((s) => s.stockNews);
  const stockNewsLoading = useHomeStore((s) => s.stockNewsLoading);

  const report = useReportStore((s) => s.report);
  const reportLoading = useReportStore((s) => s.loading);
  const loadReport = useReportStore((s) => s.load);

  // 선택 종목이 바뀌면 리포트(공개)를 로드 — 뉴스는 homeStore.select 가 이미 페치.
  useEffect(() => {
    if (selectedCode) void loadReport(selectedCode);
  }, [selectedCode, loadReport]);

  if (!selectedCode) {
    return (
      <section className="card grid min-h-[400px] place-items-center p-8 text-center text-muted">
        왼쪽에서 종목이나 뉴스를 선택하세요.
      </section>
    );
  }

  // FR-4 stale 가드: reportStore 는 공유·가드 없음 → 응답이 현재 선택과 일치할 때만 유효.
  // (빠른 연속 선택 시 늦게 도착한 이전 종목 리포트가 뒤덮는 것 방지)
  const freshReport = report && report.stock.stock_code === selectedCode ? report : null;
  const name = freshReport?.stock.stock_name ?? selectedCode;

  return (
    <section className="flex min-h-0 flex-col gap-5">
      {/* 헤더: 종목명 + 관심 + 전체 리포트 링크(Q5) */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-[12.5px] text-muted">{selectedCode}</div>
          <h2 className="text-[24px] font-extrabold leading-tight">{name}</h2>
        </div>
        <div className="flex items-center gap-2">
          <WatchlistButton stockCode={selectedCode} />
          <Link
            href={`/report/${encodeURIComponent(selectedCode)}`}
            className="rounded-full brand-grad px-4 py-2 text-[13.5px] font-bold text-white hover:opacity-90"
          >
            전체 리포트 →
          </Link>
        </div>
      </div>

      {/* FR-7 왜 이 신호일까 — freshReport 로 stale 방지 */}
      <WhySignal report={freshReport} loading={reportLoading || (!!report && !freshReport)} code={selectedCode} />

      {/* FR-6 종목 뉴스 목록 */}
      <div className="card overflow-hidden">
        <div className="border-b border-line px-4 py-3 text-[13px] font-bold text-navy">
          {name} 뉴스{stockNews ? ` ${stockNews.count}건` : ""}
        </div>
        {stockNewsLoading ? (
          <ul className="animate-pulse space-y-3 p-4">
            {[0, 1, 2].map((i) => (
              <li key={i} className="h-10 rounded-[8px] bg-surface-2" />
            ))}
          </ul>
        ) : !stockNews || stockNews.count === 0 ? (
          // Q2: 뉴스 0건이어도 위 "왜 이 신호"는 유지, 여기만 안내.
          <p className="p-6 text-center text-[13px] text-muted">
            아직 이 종목의 뉴스가 없습니다.
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {stockNews.items.map((n, i) => {
              const href = safeHttpUrl(n.url);
              const inner = (
                <>
                  <div className="flex items-center gap-2 text-[12px] text-muted">
                    {n.press && <span>{n.press}</span>}
                    {n.published_at && (
                      <span className="ml-auto shrink-0">{n.published_at.slice(0, 16).replace("T", " ")}</span>
                    )}
                  </div>
                  <div className="mt-1 text-[13.5px] text-navy-soft">{n.title}</div>
                </>
              );
              return (
                <li key={i} className="px-4 py-3">
                  {href ? (
                    <a href={href} target="_blank" rel="noopener noreferrer" className="block hover:opacity-80">
                      {inner}
                    </a>
                  ) : (
                    inner
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

function WhySignal({
  report,
  loading,
  code,
}: {
  report: ReturnType<typeof useReportStore.getState>["report"];
  loading: boolean;
  code: string;
}) {
  if (loading && !report) {
    return <div className="card h-[160px] animate-pulse bg-surface-2" />;
  }
  if (!report) {
    return (
      <div className="card p-5 text-[13.5px] text-muted">리포트를 준비 중입니다.</div>
    );
  }
  const dir = directionLabel(report.direction);
  const byKey = new Map(report.sources.map((s) => [s.source, s] as const));

  return (
    <div className="card p-5">
      <div className="flex items-center gap-2">
        <h3 className="text-[16px] font-bold">왜 이 신호일까</h3>
        <span className={`pill ${dir.tone}`} style={{ padding: "3px 10px" }}>{dir.label}</span>
        {report.score != null && (
          <span className="ml-auto text-[13px] text-muted">종합 {report.score}</span>
        )}
      </div>
      <p className="mt-3 text-[14px] leading-relaxed text-navy-soft">
        {report.summary ?? "통합 요약이 아직 없습니다."}
      </p>

      {/* 소스별 근거 서술 (report page 미러, 상세는 전체 리포트로) */}
      <div className="mt-4 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {SOURCE_ORDER.map((key) => {
          const src: ReportSource | undefined = byKey.get(key);
          const meta = SOURCE_META[key];
          if (!src) return null;
          const sdir = directionLabel(src.direction);
          return (
            <Link
              key={key}
              href={`/report/${encodeURIComponent(code)}/${key}`}
              className="rounded-[10px] border border-line p-3 transition hover:bg-surface-2"
            >
              <div className="flex items-center gap-1.5 text-[13px] font-bold">
                {meta.icon} {meta.label}
                <span className={`pill ${sdir.tone} ml-auto`} style={{ padding: "2px 8px", fontSize: 11 }}>
                  {sdir.label}
                </span>
              </div>
              <p className="mt-1.5 line-clamp-2 text-[12.5px] text-navy-soft">
                {src.summary ?? "데이터 요약 준비 중"}
              </p>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
