// 리포트 — 종합 신호 헤드라인 + 소스별 상세.

import { apiFetch } from "./core";
import type { Stock } from "./stocks";

export type SourceKey = "price" | "dart" | "hiring" | "datalab" | "patent" | "report";

export type ReportSource = {
  source: SourceKey;
  direction?: string | null;
  score?: number | null;
  data_status?: string;
  summary?: string | null;
};

// 메타러너 소스별 예측률 — 주가 BASE ⊕ 각 공공데이터로 만든 0–100 'AI 예측 점수'.
// 통합(SRC)은 Report.score(헤드라인)로 노출되고, 여기엔 per-source(주가 1 + 공공데이터 5)만 담긴다.
export type PredictionRate = {
  source: SourceKey;
  score?: number | null; // 0–100 (score_100)
  direction?: string | null; // positive | negative | neutral | unknown
  data_status?: string;
};

// 메타러너 return 채널 (#525 WS-C) — 결정론 집계 점수(score)와 별개의 학습형 수익률 신호.
// 미산출 종목은 null.
export type MlReturn = {
  score: number | null; // 부호 있는 return 점수(0–100 아님)
  direction: string | null; // positive | negative | neutral | unknown
  confidence: number | null; // 방향 합의 신뢰도 [0,1]
};

export type Report = {
  stock: Stock;
  report_version?: {
    final_signal_id: number;
    run_key: string | null;
    signal_date: string | null;
    updated_at: string | null;
  };
  direction: string | null;
  score: number | null;
  alignment_rate: number | null;
  source_agreement?: string | null;
  warning_level?: string | null;
  data_status?: string;
  summary: string | null;
  ml_return?: MlReturn | null;
  sources: ReportSource[];
  prediction_rates?: PredictionRate[];
  notice: string;
};

export type SourceDetailItem = {
  title: string | null;
  summary: string | null;
  event_date: string | null;
  direction: string | null;
  impact_level: string | null;
  evidence_url: string | null;
  source_name: string | null;
  is_official?: boolean | null;
};

// 증권사 리포트 밸류에이션 fact(집계 score_breakdown.REPORT.valuation). REPORT 소스에만 존재.
export type ReportValuation = {
  target_price?: number | null;
  methodology?: string | null;
  applied_multiple?: number | null;
  implied_multiple?: number | null;
  implied_multiple_avg?: number | null;
  forward_eps_est?: number | null;
  eps_fy?: number | null;
  peer_group?: unknown[] | null;
  event_count?: number | null;
  needs_review?: boolean | null;
};

export type SourceDetail = {
  stock: { stock_code: string; stock_name: string | null };
  source: SourceKey;
  direction: string | null;
  score: number | null;
  data_status?: string;
  summary: string | null;
  // 분석 근거 서술 불릿(주식정보처럼 signal_events 가 없는 소스의 근거). 없으면 빈 배열.
  narrative_points?: string[] | null;
  valuation?: ReportValuation | null;
  items: SourceDetailItem[];
  notice: string;
};

export async function getReport(stockCode: string): Promise<Report> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}`);
}

export async function getSourceDetail(stockCode: string, source: SourceKey): Promise<SourceDetail> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}/sources/${source}`);
}
