"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { getSourceDetail, type SourceDetail, type SourceKey } from "@/lib/apiClient";
import { directionLabel, safeHttpUrl, SOURCE_META, won } from "@/lib/format";

const VALID: SourceKey[] = ["price", "dart", "hiring", "datalab", "patent", "report"];

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
    <div className="py-10" data-page="source-detail">
      <Link href={`/report/${encodeURIComponent(ticker)}`} className="text-[13px] text-sky-deep">← 리포트로</Link>
      <h1 className="my-2 text-[28px] font-extrabold">{meta.icon} {meta.label} 상세</h1>

      {state === "loading" && <p className="py-10 text-center text-muted">불러오는 중…</p>}

      {state === "error" && <p className="py-10 text-center text-red">{message}</p>}

      {state === "ready" && detail && (
        <>
          <div className="card mt-4 p-6">
            <span className={`pill ${directionLabel(detail.direction).tone}`} style={{ padding: "5px 11px" }}>
              {directionLabel(detail.direction).label}
            </span>
            <span className="ml-2 text-[13px] text-muted">점수 {detail.score ?? "–"} · {detail.data_status ?? "—"}</span>
            <p className="mt-3 text-navy-soft">{detail.summary ?? "요약이 없습니다."}</p>
          </div>

          {source === "report" && detail.valuation && (
            <div className="card mt-4 p-6">
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

          {detail.narrative_points && detail.narrative_points.length > 0 && (
            <div className="card mt-4 p-6">
              <div className="text-[13px] font-semibold text-muted">분석 근거</div>
              <ul className="mt-3 list-disc space-y-2 pl-5 text-[13.5px] text-navy-soft">
                {detail.narrative_points.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          {(detail.items.length > 0 || !(detail.narrative_points && detail.narrative_points.length > 0)) && (
            <div className="card mt-4 overflow-hidden">
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
          <p className="mt-6 rounded-[12px] bg-surface-2 p-4 text-[12.5px] text-muted">{detail.notice}</p>
        </>
      )}
    </div>
  );
}
