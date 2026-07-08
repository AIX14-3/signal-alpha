"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { SourceIcon } from "@/components/SourceIcon";
import { getSourceDetail, type SourceDetail, type SourceKey } from "@/lib/apiClient";
import { directionLabel, safeHttpUrl, SOURCE_META, won } from "@/lib/format";

const VALID: SourceKey[] = ["price", "dart", "hiring", "datalab", "patent", "report"];

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

export default function SourceDetailPage() {
  const params = useParams<{ ticker: string; source: string }>();
  const ticker = params.ticker;
  const source = params.source as SourceKey;

  const [detail, setDetail] = useState<SourceDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!VALID.includes(source)) {
      setState("error");
      setMessage("알 수 없는 소스입니다.");
      return;
    }
    let active = true;
    setState("loading");
    getSourceDetail(ticker, source)
      .then((d) => {
        if (!active) return;
        setDetail(d);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (!active) return;
        setState("error");
        setMessage(err instanceof Error ? err.message : "불러오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, [ticker, source]);

  const meta = SOURCE_META[source] ?? { label: source, icon: "📄", hint: "" };

  return (
    <div className="relative py-8" data-page="source-detail">
      <div className="report-aura pointer-events-none fixed inset-0 -z-10" aria-hidden="true" />

      <Link
        href={`/report/${encodeURIComponent(ticker)}`}
        className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-navy-soft hover:text-navy"
      >
        <ArrowLeft size={15} aria-hidden="true" /> 리포트로
      </Link>

      <div className="mt-3 flex items-center gap-3">
        <span className="glass grid h-12 w-12 place-items-center rounded-2xl text-sky-deep" aria-hidden="true">
          <SourceIcon source={source} size={24} />
        </span>
        <h1 className="text-[28px] font-extrabold">{meta.label} 상세</h1>
      </div>

      {state === "loading" && <p className="py-10 text-center text-muted">불러오는 중…</p>}

      {state === "error" && <p className="py-10 text-center text-red">{message}</p>}

      {state === "ready" && detail && (
        <>
          <div className="glass mt-4 p-6">
            <span className={`pill ${directionLabel(detail.direction).tone}`} style={{ padding: "5px 11px" }}>
              {directionLabel(detail.direction).label}
            </span>
            <span className="ml-2 text-[13px] text-muted">점수 {detail.score ?? "–"} · {detail.data_status ?? "—"}</span>
            <p className="mt-3 text-navy-soft">{detail.summary ?? "요약이 없습니다."}</p>
          </div>

          {source === "report" && detail.valuation && (
            <div className="glass mt-4 p-6">
              <div className="text-[13px] font-semibold text-muted">밸류에이션 (집계)</div>
              <div className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
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
            </div>
          )}

          {source === "patent" && detail.patent && detail.patent.filing_trend.length > 0 && (
            <div className="glass mt-4 p-6">
              <div className="text-[13px] font-semibold text-muted">장기 출원 추이 (연도별 출원 건수)</div>
              <p className="mt-1 text-[12px] text-muted">
                특허는 출원 후 약 18개월 뒤 공개됩니다. 아래는 <b>출원</b> 연도별 건수(장기 R&D 흐름)입니다.
              </p>
              <FilingTrendChart data={detail.patent.filing_trend} />
            </div>
          )}

          {source === "patent" && detail.patent && detail.patent.recent_publications.length > 0 && (
            <div className="glass mt-4 overflow-hidden">
              <div className="px-4 pt-4 text-[13px] font-semibold text-muted">최근 공개된 특허</div>
              <p className="px-4 pb-2 pt-1 text-[12px] text-muted">
                최근 <b>공개</b>돼 시장에 노출된 특허입니다(공개일 최신순). 출원은 그보다 앞섭니다.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-[13.5px]">
                  <thead>
                    <tr className="text-muted">
                      <th className="px-4 py-3 text-left font-semibold">특허명</th>
                      <th className="px-4 py-3 text-left font-semibold">공개일</th>
                      <th className="px-4 py-3 text-left font-semibold">출원일</th>
                      <th className="px-4 py-3 text-left font-semibold">기술분류</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.patent.recent_publications.map((p, i) => (
                      <tr key={p.application_no ?? i} className="border-t border-line">
                        <td className="px-4 py-3">{p.title ?? p.application_no ?? "특허"}</td>
                        <td className="px-4 py-3 text-muted">{p.publication_date?.slice(0, 10) ?? "—"}</td>
                        <td className="px-4 py-3 text-muted">{p.application_date?.slice(0, 10) ?? "—"}</td>
                        <td className="px-4 py-3 text-muted">{p.tech_category ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {detail.narrative_points && detail.narrative_points.length > 0 && (
            <div className="glass mt-4 p-6">
              <div className="text-[13px] font-semibold text-muted">분석 근거</div>
              <ul className="mt-3 list-disc space-y-2 pl-5 text-[13.5px] text-navy-soft">
                {detail.narrative_points.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          {!(source === "patent" && detail.patent && detail.patent.recent_publications.length > 0) &&
            (detail.items.length > 0 || !(detail.narrative_points && detail.narrative_points.length > 0)) && (
            <div className="glass mt-4 overflow-hidden">
              <table className="w-full text-[13.5px]">
                <thead>
                  <tr className="text-muted">
                    <th className="px-4 py-3 text-left font-semibold">{source === "report" ? "리포트 제목" : "제목"}</th>
                    <th className="px-4 py-3 text-left font-semibold">{source === "report" ? "발행일" : "날짜"}</th>
                    <th className="px-4 py-3 text-left font-semibold">{source === "report" ? "증권사" : "출처"}</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.items.length === 0 && (
                    <tr><td className="px-4 py-6 text-center text-muted" colSpan={3}>표시할 근거가 없습니다.</td></tr>
                  )}
                  {detail.items.map((it, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className="px-4 py-3">
                        {safeHttpUrl(it.evidence_url) ? (
                          <a href={safeHttpUrl(it.evidence_url)!} target="_blank" rel="noreferrer" className="text-sky-deep">{it.title ?? "근거"} ↗</a>
                        ) : (
                          it.title ?? "근거"
                        )}
                      </td>
                      <td className="px-4 py-3 text-muted">{it.event_date?.slice(0, 10) ?? "—"}</td>
                      <td className="px-4 py-3 text-muted">{it.source_name ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="glass mt-6 p-4 text-[12.5px] text-muted">{detail.notice}</p>
        </>
      )}
    </div>
  );
}
