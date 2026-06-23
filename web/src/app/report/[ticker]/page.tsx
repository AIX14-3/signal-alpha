"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, getSignalByTicker } from "@/lib/apiClient";
import { PipelineStepper } from "@/components/PipelineStepper";
import { WatchlistButton } from "@/components/WatchlistButton";
import { ReportView, type ReportData } from "@/components/report/ReportView";
import type { RiskItem } from "@/components/report/RiskList";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function mapRisks(value: unknown): RiskItem[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 5).map((raw) => {
    const item = asRecord(raw);
    return {
      level: String(item.impact_level ?? item.level ?? "LOW"),
      title: String(item.title ?? item.summary ?? "리스크"),
      detail: typeof item.summary === "string" ? item.summary : null,
    };
  });
}

function toReportData(ticker: string, row: Record<string, unknown>): ReportData {
  return {
    stockCode: String(row.ticker ?? ticker),
    stockName: String(row.name ?? ticker),
    market: typeof row.market === "string" ? row.market : null,
    meta: typeof row.sector === "string" ? row.sector : null,
    finalScore: numberOrNull(row.final_score),
    direction: typeof row.signal === "string" ? row.signal : null,
    summary: typeof row.summary === "string" ? row.summary : null,
    sourceAgreement: typeof row.source_agreement === "string" ? row.source_agreement : null,
    scoreBreakdown: asRecord(row.score_breakdown) as ReportData["scoreBreakdown"],
    risks: mapRisks(row.caution_evidence),
    notice: typeof row.notice === "string" ? row.notice : undefined,
  };
}

export default function ReportPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = params.ticker;
  const [data, setData] = useState<ReportData | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "pending" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setState("loading");
    getSignalByTicker(ticker)
      .then((row) => {
        if (!active) return;
        setData(toReportData(ticker, row));
        setState("ready");
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err instanceof ApiError && err.status === 404) {
          setState("pending");
        } else {
          setError(err instanceof Error ? err.message : "리포트를 불러오지 못했습니다.");
          setState("error");
        }
      });
    return () => {
      active = false;
    };
  }, [ticker]);

  if (state === "loading") {
    return <p className="py-16 text-center text-muted">리포트를 불러오는 중…</p>;
  }

  if (state === "pending") {
    return (
      <div className="py-12">
        <h1 className="mb-2 text-[28px] font-extrabold">{ticker} 분석 준비 중</h1>
        <p className="mb-6 text-[14px] text-muted">
          아직 발행된 시그널이 없습니다. 분석 파이프라인 진행 상태를 확인하세요.
        </p>
        <PipelineStepper ticker={ticker} />
      </div>
    );
  }

  if (state === "error" || !data) {
    return <p className="py-16 text-center text-red">{error ?? "리포트를 불러오지 못했습니다."}</p>;
  }

  return (
    <div>
      <div className="flex justify-end pt-6">
        <WatchlistButton stockCode={data.stockCode} />
      </div>
      <ReportView data={data} />
    </div>
  );
}
