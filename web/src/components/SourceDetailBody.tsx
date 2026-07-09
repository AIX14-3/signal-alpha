"use client";

import { ExternalLink } from "lucide-react";
import type { SourceDetail, SourceKey } from "@/lib/apiClient";
import {
  directionLabel,
  isExpired,
  relativeDayLabel,
  safeHttpUrl,
  won,
} from "@/lib/format";
import { referenceLinks } from "@/lib/sourceLinks";

// 소스 상세 본문 — 우측 슬라이드오버(SourceDetailPanel)와 전체 페이지
// (/report/[ticker]/[source])가 공유한다. 표현만 담당하고 데이터 로딩은 호출부 몫.

// 장기 출원 추이 — 연도별 출원 건수 막대 차트(외부 라이브러리 없이 CSS 막대, 테마 토큰 사용).
function FilingTrendChart({ data }: { data: { year: number; count: number }[] }) {
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div
      className="mt-4 flex items-end gap-2"
      style={{ height: 140 }}
      role="img"
      aria-label="연도별 특허 출원 건수 추이"
    >
      {data.map((d) => (
        <div key={d.year} className="flex flex-1 flex-col items-center justify-end gap-1">
          <div className="text-[11px] text-muted">{d.count}</div>
          <div
            className="w-full rounded-t bg-sky-deep"
            style={{ height: `${Math.max(4, (d.count / max) * 100)}%` }}
            title={`${d.year}년 ${d.count}건`}
          />
          <div className="text-[11px] text-muted">{d.year}</div>
        </div>
      ))}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="glass mt-4 p-5">
      <div className="text-[13px] font-semibold text-muted">{title}</div>
      {hint && <p className="mt-1 text-[12px] text-muted">{hint}</p>}
      {children}
    </section>
  );
}

export function SourceDetailBody({ detail, source }: { detail: SourceDetail; source: SourceKey }) {
  const dir = directionLabel(detail.direction);
  const linked = detail.items.filter((it) => safeHttpUrl(it.evidence_url));
  const refs = referenceLinks(
    source,
    detail.stock.stock_name ?? detail.stock.stock_code,
    detail.stock.stock_code,
  );

  return (
    <div data-source-detail={source}>
      {/* 판정 요약 */}
      <section className="glass p-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`pill ${dir.tone}`} style={{ padding: "5px 11px" }}>
            {dir.label}
          </span>
          <span className="text-[13px] text-muted">
            점수 {detail.score ?? "–"} · {detail.data_status ?? "—"}
          </span>
        </div>
        <p className="mt-3 text-[14px] text-navy-soft">{detail.summary ?? "요약이 없습니다."}</p>
      </section>

      {/* 분석 근거 — 이 소스가 왜 그 방향으로 판정됐는지 */}
      {detail.narrative_points && detail.narrative_points.length > 0 && (
        <Section title="분석 근거">
          <ul className="mt-3 list-disc space-y-2 pl-5 text-[13.5px] text-navy-soft">
            {detail.narrative_points.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </Section>
      )}

      {/* REPORT 전용 — 밸류에이션 fact */}
      {source === "report" && detail.valuation && (
        <Section title="밸류에이션 (집계)">
          <div className="mt-3 grid grid-cols-2 gap-4">
            <div>
              <div className="text-[12px] text-muted">목표주가</div>
              <div className="text-[15px] font-bold">
                {detail.valuation.target_price != null ? won(detail.valuation.target_price) : "—"}
              </div>
            </div>
            <div>
              <div className="text-[12px] text-muted">방법론</div>
              <div className="text-[15px] font-bold">{detail.valuation.methodology ?? "—"}</div>
            </div>
            <div>
              <div className="text-[12px] text-muted">적용 배수</div>
              <div className="text-[15px] font-bold">
                {detail.valuation.applied_multiple != null ? `${detail.valuation.applied_multiple}x` : "—"}
              </div>
            </div>
            <div>
              <div className="text-[12px] text-muted">분석 리포트 수</div>
              <div className="text-[15px] font-bold">
                {detail.valuation.event_count != null ? `${detail.valuation.event_count}건` : "—"}
              </div>
            </div>
          </div>
        </Section>
      )}

      {/* PATENT 전용 — 장기 출원 추이 */}
      {source === "patent" && detail.patent && detail.patent.filing_trend.length > 0 && (
        <Section
          title="장기 출원 추이 (연도별 출원 건수)"
          hint="특허는 출원 후 약 18개월 뒤 공개됩니다. 아래는 출원 연도별 건수(장기 R&D 흐름)입니다."
        >
          <FilingTrendChart data={detail.patent.filing_trend} />
        </Section>
      )}

      {/* PATENT 전용 — 최근 공개 특허 */}
      {source === "patent" && detail.patent && detail.patent.recent_publications.length > 0 && (
        <Section
          title="최근 공개된 특허"
          hint="최근 공개돼 시장에 노출된 특허입니다(공개일 최신순). 출원은 그보다 앞섭니다."
        >
          <ul className="mt-3 space-y-3">
            {detail.patent.recent_publications.map((p, i) => {
              const since = relativeDayLabel(p.publication_date);
              return (
                <li key={p.application_no ?? i} className="border-t border-line pt-3 first:border-0 first:pt-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13.5px] font-semibold text-navy-soft">
                      {p.title ?? p.application_no ?? "특허"}
                    </span>
                    {since && (
                      <span className="pill flat shrink-0" style={{ padding: "1px 8px", fontSize: 11 }}>
                        공개 {since}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-[12px] text-muted">
                    공개 {p.publication_date?.slice(0, 10) ?? "—"} · 출원{" "}
                    {p.application_date?.slice(0, 10) ?? "—"}
                    {p.tech_category ? ` · ${p.tech_category}` : ""}
                  </div>
                </li>
              );
            })}
          </ul>
        </Section>
      )}

      {/* HIRING 전용 — 최근 공고의 게시일·마감 상태 */}
      {source === "hiring" && detail.hiring && detail.hiring.postings.length > 0 && (
        <Section
          title={`최근 채용 공고 (${detail.hiring.postings.length}건)`}
          hint="게시일 기준 최신순입니다. 마감일이 지난 공고는 '마감'으로 표시됩니다."
        >
          <ul className="mt-3 space-y-3">
            {detail.hiring.postings.map((p, i) => {
              const posted = relativeDayLabel(p.posting_date);
              // 상시채용은 마감일이 없는 정상 상태 — 만료로 읽히면 안 된다.
              const alwaysOpen = Boolean(p.is_always_open);
              const expired = !alwaysOpen && isExpired(p.closing_date);
              const dday = !alwaysOpen && !expired ? relativeDayLabel(p.closing_date) : null;
              const href = safeHttpUrl(p.source_url);
              return (
                <li key={i} className="border-t border-line pt-3 first:border-0 first:pt-0">
                  <div className="flex flex-wrap items-center gap-2">
                    {href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1.5 text-[13.5px] font-semibold text-sky-deep hover:underline"
                      >
                        {p.job_title ?? "채용 공고"}
                        <ExternalLink size={13} aria-hidden="true" />
                      </a>
                    ) : (
                      <span className="text-[13.5px] font-semibold text-navy-soft">
                        {p.job_title ?? "채용 공고"}
                      </span>
                    )}
                    {alwaysOpen ? (
                      <span className="pill up shrink-0" style={{ padding: "1px 8px", fontSize: 11 }}>
                        상시채용
                      </span>
                    ) : expired ? (
                      <span className="pill down shrink-0" style={{ padding: "1px 8px", fontSize: 11 }}>
                        마감
                      </span>
                    ) : dday ? (
                      <span className="pill flat shrink-0" style={{ padding: "1px 8px", fontSize: 11 }}>
                        마감 {dday}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 text-[12px] text-muted">
                    게시 {p.posting_date?.slice(0, 10) ?? "—"}
                    {posted ? ` (${posted})` : ""}
                    {alwaysOpen
                      ? " · 마감일 없음"
                      : p.closing_date_display
                        ? ` · 마감 ${p.closing_date_display}`
                        : ""}
                  </div>
                </li>
              );
            })}
          </ul>
        </Section>
      )}

      {/* 근거가 된 원본 문서 — evidence_url 이 있는 항목만. 실제로 분석에 쓰인 그 문서. */}
      {linked.length > 0 && (
        <Section title={`근거가 된 원본 문서 (${linked.length}건)`} hint="분석에 실제로 사용된 문서입니다.">
          <ul className="mt-3 space-y-3">
            {linked.map((it, i) => (
              <li key={i} className="border-t border-line pt-3 first:border-0 first:pt-0">
                <a
                  href={safeHttpUrl(it.evidence_url)!}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-start gap-1.5 text-[13.5px] font-semibold text-sky-deep hover:underline"
                >
                  <span>{it.title ?? "근거 문서"}</span>
                  <ExternalLink size={13} className="mt-[3px] shrink-0" aria-hidden="true" />
                </a>
                <div className="mt-1 text-[12px] text-muted">
                  {it.event_date?.slice(0, 10) ?? "—"}
                  {it.source_name ? ` · ${it.source_name}` : ""}
                  {it.impact_level ? ` · 영향도 ${it.impact_level}` : ""}
                </div>
                {it.summary && <p className="mt-1 text-[12.5px] text-navy-soft">{it.summary}</p>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* 원본 문서가 없는 소스(집계 지표 기반) — 무엇을 봤는지 정직하게 밝히고 참고 링크만 제공 */}
      {linked.length === 0 && (
        <Section title="관련 자료" hint={refs.explanation}>
          {refs.links.length > 0 && (
            <ul className="mt-3 space-y-2">
              {refs.links.map((l) => (
                <li key={l.href}>
                  <a
                    href={l.href}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 text-[13.5px] font-semibold text-sky-deep hover:underline"
                  >
                    {l.label}
                    <ExternalLink size={13} aria-hidden="true" />
                  </a>
                  <span className="ml-2 text-[12px] text-muted">{l.note}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      <p className="glass mt-4 p-4 text-[12.5px] text-muted">{detail.notice}</p>
    </div>
  );
}
