// 저널 — 작성·복기·차트·타임라인.

import { apiFetch } from "./core";

// 저널 작성 후 실제 주가 변동 확정 결과 — 거래일 horizon(7td/30td)별. 워커 러너가
// 매일 채우며, 만기 전엔 배열이 비거나 일부만 온다(프론트는 "확정 전" 표시).
export type JournalOutcome = {
  horizon: "7td" | "30td";
  base_trade_date: string;
  base_price: number;
  outcome_trade_date: string;
  outcome_price: number;
  change_pct: number;
  checked_at: string;
};

export type Journal = {
  journal_id: number;
  stock_code: string;
  stock_name: string | null;
  final_signal_id: number | null;
  user_view: string;
  memo: string | null;
  // 회고(결과 확인 후 복기) — outcome 확정 전엔 서버가 저장을 거부(RETROSPECTIVE_NOT_READY).
  retrospective_memo: string | null;
  tags: string[];
  // 작성 시점 신호 스냅샷 — 리포트가 갱신돼도 "그때 판단"의 근거를 보존.
  signal_score_at_time: number | null;
  signal_value_at_time: string | null;
  source_agreement_at_time: string | null;
  // 현재 발행 신호 — "그때 판단 → 지금 신호" 비교용(미발행이면 null).
  current_signal: {
    final_signal_id: number | null;
    score: number | null;
    value: string | null;
    source_agreement: string | null;
  } | null;
  outcomes: JournalOutcome[];
  created_at: string | null;
  updated_at: string | null;
};

export type JournalChartPoint = { trade_date: string; close: number | null };

// 저널 상세 차트 — 작성일 30일 전 ~ 최신 종가 시리즈와 작성 시점 대비 등락.
// 시리즈는 워커 러너가 매일 동기화하며, 동기화 전엔 series 가 빈다.
export type JournalChart = {
  series: JournalChartPoint[];
  base_trade_date: string | null;
  base_price: number | null;
  latest_trade_date: string | null;
  latest_price: number | null;
  change_pct_since_created: number | null;
};

// 종목별 저널 타임라인 — 한 장의 가격 차트 위에 저널 작성 시점 마커.
export type JournalTimelineMarker = {
  journal_id: number;
  created_at: string | null;
  user_view: string;
  memo: string | null;
  retrospective_memo: string | null;
  trade_date: string | null;
  price: number | null;
};

export type JournalTimeline = {
  stock: { stock_code: string; stock_name: string | null };
  series: JournalChartPoint[];
  journals: JournalTimelineMarker[];
  latest_trade_date: string | null;
  latest_price: number | null;
  notice: string;
};

export async function listJournals(params: { stock_code?: string; limit?: number } = {}): Promise<{
  count: number;
  items: Journal[];
}> {
  const search = new URLSearchParams();
  if (params.stock_code) search.set("stock_code", params.stock_code);
  if (params.limit) search.set("limit", String(params.limit));
  const qs = search.toString();
  return apiFetch(`/api/journals${qs ? `?${qs}` : ""}`);
}

export async function createJournal(body: {
  stock_code: string;
  final_signal_id: number;
  user_view: string;
  memo?: string;
  tags?: string[];
}): Promise<Journal> {
  return apiFetch("/api/journals", { method: "POST", body: JSON.stringify(body) });
}

export async function getJournal(id: number): Promise<Journal> {
  return apiFetch(`/api/journals/${id}`);
}

export async function getJournalChart(
  id: number,
): Promise<{ journal: Journal; chart: JournalChart; notice: string }> {
  return apiFetch(`/api/journals/${id}/chart`);
}

export async function updateJournal(
  id: number,
  body: { user_view?: string; memo?: string; tags?: string[]; retrospective_memo?: string },
): Promise<Journal> {
  return apiFetch(`/api/journals/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function getJournalTimeline(stockCode: string): Promise<JournalTimeline> {
  return apiFetch(`/api/journals/timeline/${encodeURIComponent(stockCode)}`);
}

export async function deleteJournal(id: number): Promise<{ status: string }> {
  return apiFetch(`/api/journals/${id}`, { method: "DELETE" });
}
