"use client";

import Link from "next/link";
import { useEffect } from "react";
import { StockPriceChart } from "@/components/StockPriceChart";
import { directionLabel, safeHttpUrl, SOURCE_META, SOURCE_ORDER } from "@/lib/format";
import { useHomeStore } from "@/stores/homeStore";
import { useReportStore } from "@/stores/reportStore";

// 우② 실시간 분석 종목 — 종목 리스트. 행 클릭 시 인라인 아코디언으로 펼쳐
// 실시간 차트 · 분석 점수 · 분석 내용 · 관련 뉴스를 보여준다(FR-5/6/7).
export function LiveAnalysisSection() {
  const chips = useHomeStore((s) => s.chips);
  const loading = useHomeStore((s) => s.loading);
  const selectedCode = useHomeStore((s) => s.selectedCode);
  const toggle = useHomeStore((s) => s.toggle);

  return (
    <section data-section="live-analysis" className="glass-card overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-[14px] font-bold text-navy">실시간 분석 종목</h2>
        <span className="flex items-center gap-1.5 text-[11.5px] text-muted">
          <span className="live-dot" /> 상시 분석 중
        </span>
      </div>

      {loading ? (
        <ul className="animate-pulse space-y-2 p-4">
          {[0, 1, 2, 3].map((i) => (
            <li key={i} className="h-11 rounded-[10px] bg-surface-2" />
          ))}
        </ul>
      ) : chips.length === 0 ? (
        <p className="p-8 text-center text-[13px] text-muted">분석 종목을 불러오는 중입니다.</p>
      ) : (
        <ul className="divide-y divide-line">
          {chips.map((s) => {
            const open = selectedCode === s.stock_code;
            return (
              <li key={s.stock_code}>
                <button
                  type="button"
                  data-flow="analysis-accordion-toggle"
                  aria-expanded={open}
                  onClick={() => void toggle(s.stock_code)}
                  className={`flex w-full items-center justify-between px-4 py-3 text-left transition hover:bg-surface-2 ${
                    open ? "bg-surface-2" : ""
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[14px] font-semibold text-navy">
                      {s.stock_name ?? s.stock_code}
                    </span>
                    <span className="text-[11.5px] text-muted">{s.stock_code}</span>
                  </span>
                  <span className={`text-[13px] text-muted transition ${open ? "rotate-180" : ""}`}>
                    ▾
                  </span>
                </button>
                {open && <AnalysisDetail stockCode={s.stock_code} />}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// 아코디언 펼침 내용 — 차트(homeStore.stockPrices) + 점수/분석(reportStore) + 뉴스(homeStore.stockNews).
function AnalysisDetail({ stockCode }: { stockCode: string }) {
  const stockPrices = useHomeStore((s) => s.stockPrices);
  const stockPricesLoading = useHomeStore((s) => s.stockPricesLoading);
  const stockNews = useHomeStore((s) => s.stockNews);
  const stockNewsLoading = useHomeStore((s) => s.stockNewsLoading);

  const report = useReportStore((s) => s.report);
  const reportLoading = useReportStore((s) => s.loading);
  const loadReport = useReportStore((s) => s.load);

  useEffect(() => {
    void loadReport(stockCode);
  }, [stockCode, loadReport]);

  // stale 가드: 응답이 현재 종목과 일치할 때만 유효.
  const fresh = report && report.stock.stock_code === stockCode ? report : null;
  const dir = directionLabel(fresh?.direction);
  const byKey = new Map((fresh?.sources ?? []).map((s) => [s.source, s] as const));
  const series = stockPrices && stockPrices.stock.stock_code === stockCode ? stockPrices.series : [];

  return (
    <div data-flow="analysis-detail" className="space-y-4 border-t border-line bg-surface-2/30 p-4">
      {/* 실시간 차트 */}
      <StockPriceChart series={series} loading={stockPricesLoading} />

      {/* 분석 점수 + 데이터 방향 */}
      <div className="grid grid-cols-2 gap-3">
        <div className="brand-grad rounded-[12px] p-3 text-white">
          <div className="text-[11px] opacity-90">종합 점수</div>
          <div className="mt-1 text-[24px] font-extrabold leading-none">{fresh?.score ?? "–"}</div>
          <div className="mt-1 text-[10px] opacity-80">0–100</div>
        </div>
        <div className="glass-card grid place-items-center p-3">
          <span className={`pill ${dir.tone}`} style={{ padding: "5px 13px" }}>
            {dir.label}
          </span>
        </div>
      </div>

      {/* 분석된 내용(왜 이 신호일까) */}
      <div>
        <div className="mb-1 text-[12.5px] font-bold text-navy">왜 이 신호일까</div>
        <p className="text-[13px] leading-relaxed text-navy-soft">
          {fresh?.summary ?? (reportLoading ? "분석을 불러오는 중…" : "통합 요약이 아직 없습니다.")}
        </p>
      </div>

      {/* 소스별 신호 점수 */}
      <div className="space-y-1.5">
        {SOURCE_ORDER.map((k) => {
          const sc = byKey.get(k)?.score ?? null;
          const meta = SOURCE_META[k];
          return (
            <div key={k} className="flex items-center gap-2 text-[12px]">
              <span className="w-24 shrink-0 text-navy-soft">
                {meta.icon} {meta.label}
              </span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
                <span className="brand-grad block h-full rounded-full" style={{ width: `${sc ?? 0}%` }} />
              </span>
              <span className="w-7 shrink-0 text-right font-semibold text-muted">{sc ?? "–"}</span>
            </div>
          );
        })}
      </div>

      {/* 관련 뉴스 + 전체 리포트 링크 */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <div className="text-[12.5px] font-bold text-navy">
            관련 뉴스{stockNews ? ` ${stockNews.count}건` : ""}
          </div>
          <Link
            href={`/report/${encodeURIComponent(stockCode)}`}
            className="text-[12px] font-semibold text-sky-deep hover:underline"
          >
            전체 리포트 →
          </Link>
        </div>
        {stockNewsLoading ? (
          <p className="py-2 text-[12px] text-muted">뉴스를 불러오는 중…</p>
        ) : !stockNews || stockNews.count === 0 ? (
          <p className="py-2 text-[12px] text-muted">이 종목의 뉴스가 아직 없습니다.</p>
        ) : (
          <ul className="space-y-1">
            {stockNews.items.slice(0, 5).map((n, i) => {
              const href = safeHttpUrl(n.url);
              const row = (
                <div className="flex items-center gap-2 rounded-[8px] px-2 py-1.5 hover:bg-surface-2">
                  <span className="text-[12px]">📄</span>
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-navy-soft">{n.title}</span>
                  {n.press && <span className="shrink-0 text-[11px] text-muted">{n.press}</span>}
                </div>
              );
              return (
                <li key={i}>
                  {href ? (
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      {row}
                    </a>
                  ) : (
                    row
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
