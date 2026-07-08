"use client";

import Link from "next/link";
import { useEffect } from "react";
import { directionLabel, safeHttpUrl, SOURCE_META, SOURCE_ORDER } from "@/lib/format";
import { useHomeStore } from "@/stores/homeStore";
import { useReportStore } from "@/stores/reportStore";

// 우 pane(대시보드 스탯 레일) = 선택 종목 요약. 레퍼런스 우측 컬럼 오마주:
// 스탯 타일(PAYMENTS) · 소스 신호 라인 · 소스별 진행바(Balance/Orders) · 전체 리포트/뉴스(SUPER FILES).
export function HomeRightPane() {
  const selectedCode = useHomeStore((s) => s.selectedCode);
  const stockNews = useHomeStore((s) => s.stockNews);

  const report = useReportStore((s) => s.report);
  const reportLoading = useReportStore((s) => s.loading);
  const loadReport = useReportStore((s) => s.load);

  // 선택 종목이 바뀌면 리포트(공개)를 로드 — 뉴스는 homeStore.select 가 이미 페치.
  useEffect(() => {
    if (selectedCode) void loadReport(selectedCode);
  }, [selectedCode, loadReport]);

  if (!selectedCode) {
    return (
      <aside className="glass-card grid min-h-[400px] place-items-center p-8 text-center text-[13px] text-muted">
        왼쪽에서 종목이나 뉴스를 선택하세요.
      </aside>
    );
  }

  // FR-4 stale 가드: 응답이 현재 선택과 일치할 때만 유효(늦게 도착한 이전 종목 리포트가 뒤덮는 것 방지).
  const fresh = report && report.stock.stock_code === selectedCode ? report : null;
  const name = fresh?.stock.stock_name ?? selectedCode;
  const dir = directionLabel(fresh?.direction);
  const byKey = new Map((fresh?.sources ?? []).map((s) => [s.source, s] as const));
  const scores = SOURCE_ORDER.map((k) => byKey.get(k)?.score ?? null);

  return (
    <aside className="flex min-h-0 flex-col gap-4">
      {/* 종목 헤더 */}
      <div>
        <div className="text-[12px] text-muted">{selectedCode}</div>
        <h2 className="text-[20px] font-extrabold leading-tight">{name}</h2>
      </div>

      {/* 스탯 타일 2장 (PAYMENTS 오마주) */}
      <div className="grid grid-cols-2 gap-3">
        <div className="brand-grad rounded-[14px] p-4 text-white">
          <div className="text-[11.5px] opacity-90">종합 점수</div>
          <div className="mt-1 text-[26px] font-extrabold leading-none">{fresh?.score ?? "–"}</div>
          <div className="mt-1 text-[10.5px] opacity-80">0–100</div>
        </div>
        <div className="glass-card p-4">
          <div className="text-[11.5px] text-muted">데이터 방향</div>
          <div className="mt-3">
            <span className={`pill ${dir.tone}`} style={{ padding: "5px 13px" }}>
              {dir.label}
            </span>
          </div>
        </div>
      </div>

      {/* 소스별 신호 라인 — 실데이터(소스별 0–100 점수) */}
      <SourceSparkline scores={scores} loading={reportLoading && !fresh} />

      {/* 왜 이 신호일까 요약 */}
      <div className="glass-card p-4">
        <div className="text-[13px] font-bold text-navy">왜 이 신호일까</div>
        <p className="mt-2 text-[13px] leading-relaxed text-navy-soft">
          {fresh?.summary ?? (reportLoading ? "분석을 불러오는 중…" : "통합 요약이 아직 없습니다.")}
        </p>
      </div>

      {/* 소스별 신호 진행바 (Balance/Orders 오마주) — 각 행은 소스 상세로 링크 */}
      <div className="glass-card p-4">
        <div className="mb-3 text-[13px] font-bold text-navy">소스별 신호</div>
        <div className="space-y-2.5">
          {SOURCE_ORDER.map((k) => {
            const src = byKey.get(k);
            const sc = src?.score ?? null;
            const meta = SOURCE_META[k];
            return (
              <Link
                key={k}
                href={`/report/${encodeURIComponent(selectedCode)}/${k}`}
                className="block rounded-[8px] p-1.5 transition hover:bg-surface-2"
              >
                <div className="flex items-center justify-between text-[12px]">
                  <span className="text-navy-soft">
                    {meta.icon} {meta.label}
                  </span>
                  <span className="font-semibold text-muted">{sc ?? "–"}</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2">
                  <div className="brand-grad h-full rounded-full" style={{ width: `${sc ?? 0}%` }} />
                </div>
              </Link>
            );
          })}
        </div>
      </div>

      {/* 전체 리포트 + 종목 뉴스 (SUPER FILES 오마주) */}
      <div className="glass-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-[13px] font-bold text-navy">
            {name} 뉴스{stockNews ? ` ${stockNews.count}건` : ""}
          </div>
          <Link
            href={`/report/${encodeURIComponent(selectedCode)}`}
            className="text-[12px] font-semibold text-sky-deep hover:underline"
          >
            전체 리포트 →
          </Link>
        </div>
        <ul className="space-y-1">
          {(stockNews?.items ?? []).slice(0, 4).map((n, i) => {
            const href = safeHttpUrl(n.url);
            const row = (
              <div className="flex items-center gap-2 rounded-[10px] px-2 py-1.5 hover:bg-surface-2">
                <span className="text-[13px]">📄</span>
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
          {(!stockNews || stockNews.count === 0) && (
            <li className="px-2 py-3 text-[12px] text-muted">이 종목의 뉴스가 아직 없습니다.</li>
          )}
        </ul>
      </div>
    </aside>
  );
}

// 소스별 0–100 점수를 잇는 미니 라인(실데이터). 유효 점수 2개 미만이면 안내만 표시.
function SourceSparkline({ scores, loading }: { scores: (number | null)[]; loading: boolean }) {
  const valid = scores.filter((v): v is number => v != null);
  const W = 300;
  const H = 54;
  const n = scores.length;

  let content;
  if (loading) {
    content = <div className="h-[54px] animate-pulse rounded-[8px] bg-surface-2" />;
  } else if (valid.length < 2) {
    content = <p className="py-4 text-center text-[12px] text-muted">신호 점수가 아직 충분하지 않습니다.</p>;
  } else {
    // null 은 직전 값으로 이어붙여 선이 끊기지 않게 한다(결측 소스 표시는 아래 진행바에서).
    let last = valid[0];
    const pts = scores.map((v, i) => {
      const val = v ?? last;
      last = val;
      const x = n === 1 ? 0 : (i / (n - 1)) * W;
      const y = H - (val / 100) * (H - 6) - 3;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    content = (
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-[54px] w-full" aria-hidden="true">
        <defs>
          <linearGradient id="sparkgrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#0ea5e9" />
            <stop offset="1" stopColor="#10b981" />
          </linearGradient>
        </defs>
        <polyline
          fill="none"
          stroke="url(#sparkgrad)"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={pts.join(" ")}
        />
      </svg>
    );
  }

  return (
    <div className="glass-card p-4">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[13px] font-bold text-navy">소스별 신호 점수</span>
        <span className="text-[11px] text-muted">0–100</span>
      </div>
      {content}
    </div>
  );
}
