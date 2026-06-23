"use client";

import { useEffect } from "react";
import { useAnalysisStore } from "@/stores/analysisStore";

const STATUS_TONE: Record<string, string> = {
  success: "bg-green text-white",
  running: "brand-grad text-white",
  retrying: "brand-grad text-white",
  pending: "bg-surface-2 text-muted",
  failed: "bg-red text-white",
  skipped: "bg-surface-2 text-muted",
};

/** 분석 진행 파이프라인 스테퍼. analysisStore를 폴링한다. */
export function PipelineStepper({ ticker }: { ticker: string }) {
  const start = useAnalysisStore((state) => state.start);
  const poll = useAnalysisStore((state) => state.poll);
  const status = useAnalysisStore((state) => state.status);
  const polling = useAnalysisStore((state) => state.polling);

  useEffect(() => {
    start(ticker);
    void poll();
    const timer = setInterval(() => void poll(), 3000);
    return () => clearInterval(timer);
  }, [ticker, start, poll]);

  const stages = status?.stages ?? [];

  return (
    <div className="card p-6">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-[15px] font-bold">분석 진행</h3>
        <span className="text-[12.5px] text-muted">
          {status ? `상태: ${status.overall}` : "불러오는 중…"}
          {polling ? " · 진행 중" : ""}
        </span>
      </div>
      {stages.length === 0 ? (
        <p className="text-[14px] text-muted">대기 중인 분석 단계가 없습니다.</p>
      ) : (
        <ol className="space-y-2">
          {stages.map((stage) => (
            <li key={stage.task_type} className="flex items-center gap-3">
              <span
                className={`pill px-3 py-1 text-[11px] font-bold ${
                  STATUS_TONE[stage.status] ?? "bg-surface-2 text-muted"
                }`}
              >
                {stage.status}
              </span>
              <span className="text-[14px] text-navy-soft">{stage.task_type}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
