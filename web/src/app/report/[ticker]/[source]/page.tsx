"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, getSourceDetail, type SourceDetail, type SourceKey } from "@/lib/apiClient";
import { DirectionBadge } from "@/components/signal/DirectionBadge";
import { Skeleton } from "@/components/signal/Skeleton";
import { Timeline, type TimelinePoint } from "@/components/signal/Timeline";
import { SOURCE_META } from "@/lib/format";

const VALID: SourceKey[] = ["price", "dart", "hiring", "datalab", "report"];

export default function SourceDetailPage() {
  const params = useParams<{ ticker: string; source: string }>();
  const ticker = params.ticker;
  const source = params.source as SourceKey;

  const [detail, setDetail] = useState<SourceDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "locked" | "error">("loading");
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
        if (err instanceof ApiError && (err.status === 401 || err.status === 402)) {
          setState("locked");
          setMessage(err.message);
        } else {
          setState("error");
          setMessage(err instanceof Error ? err.message : "불러오지 못했습니다.");
        }
      });
    return () => {
      active = false;
    };
  }, [ticker, source]);

  const meta = SOURCE_META[source] ?? { label: source, icon: "📄", hint: "" };

  return (
    <div className="py-10">
      <Link href={`/report/${encodeURIComponent(ticker)}`} className="text-[13px] text-sky-deep">← 리포트로</Link>
      <h1 className="my-2 text-[28px] font-extrabold">{meta.icon} {meta.label} 상세</h1>

      {state === "loading" && (
        <div className="mt-4 space-y-4">
          <div className="card space-y-3 p-6">
            <Skeleton className="h-6 w-24" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
          <div className="card space-y-3 p-6">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-5 w-full" />
            ))}
          </div>
        </div>
      )}

      {state === "locked" && (
        <div className="card mt-4 p-8 text-center">
          <div className="text-[28px]">🔒</div>
          <p className="mt-2 text-navy-soft">{message}</p>
          <Link href={`/report/${encodeURIComponent(ticker)}`} className="brand-grad mt-4 inline-block rounded-full px-6 py-3 font-bold text-white">
            리포트에서 열람하기
          </Link>
        </div>
      )}

      {state === "error" && <p className="py-10 text-center text-red">{message}</p>}

      {state === "ready" && detail && (
        <>
          <div className="card mt-4 p-6">
            <DirectionBadge direction={detail.direction} />
            <span className="ml-2 text-[13px] text-muted">점수 {detail.score ?? "–"} · {detail.data_status ?? "—"}</span>
            <p className="mt-3 text-navy-soft">{detail.summary ?? "요약이 없습니다."}</p>
          </div>

          {(() => {
            const points: TimelinePoint[] = detail.items
              .filter((it) => it.event_date)
              .slice(0, 8)
              .map((it) => ({
                label: (it.source_name ?? it.title ?? "근거").slice(0, 8),
                time: (it.event_date ?? "").slice(5, 10),
                up: null,
              }));
            return points.length >= 2 ? (
              <div className="card mt-4 p-5">
                <div className="text-[13px] font-semibold text-navy-soft">최근 근거 타임라인</div>
                <Timeline points={points} />
              </div>
            ) : null;
          })()}

          <div className="card mt-4 overflow-hidden">
            <table className="w-full text-[13.5px]">
              <thead>
                <tr className="text-muted">
                  <th className="px-4 py-3 text-left font-semibold">제목</th>
                  <th className="px-4 py-3 text-left font-semibold">날짜</th>
                  <th className="px-4 py-3 text-left font-semibold">출처</th>
                </tr>
              </thead>
              <tbody>
                {detail.items.length === 0 && (
                  <tr><td className="px-4 py-6 text-center text-muted" colSpan={3}>표시할 근거가 없습니다.</td></tr>
                )}
                {detail.items.map((it, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="px-4 py-3">
                      {it.evidence_url ? (
                        <a href={it.evidence_url} target="_blank" rel="noreferrer" className="text-sky-deep">{it.title ?? "근거"} ↗</a>
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
          <p className="mt-6 rounded-[12px] bg-surface-2 p-4 text-[12.5px] text-muted">{detail.notice}</p>
        </>
      )}
    </div>
  );
}
