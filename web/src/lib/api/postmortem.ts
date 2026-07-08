// 매매 의사결정 부검 — 브로커 연동·체결·계획·단건/패턴 부검. main-server /api/postmortem 계약과 1:1.
// 전 엔드포인트 구독 전용(402). 키 평문은 응답에 없다(등록/조회 메타만). 수량·가격은 정밀도
// 보존 위해 문자열. 사후확신 금지 — 관측신호는 그때 볼 수 있었던 것(PIT).

import { apiFetch } from "./core";

// ---- 브로커 연동 --------------------------------------------------------
export type BrokerName = "kiwoom" | "toss";

export type BrokerCredential = {
  id: number;
  broker: BrokerName;
  account_ref: string;
  is_mock: boolean;
  status: "active" | "error" | "revoked";
  last_synced_at: string | null;
  last_error: string | null;
  created_at: string | null;
};

export type BrokerConnectBody = {
  broker: BrokerName;
  app_key: string;
  app_secret: string;
  account_ref?: string;
  is_mock?: boolean;
};

export function listBrokers(): Promise<{ count: number; items: BrokerCredential[] }> {
  return apiFetch(`/api/postmortem/brokers`);
}

export function connectBroker(body: BrokerConnectBody): Promise<BrokerCredential> {
  return apiFetch(`/api/postmortem/brokers`, { method: "POST", body: JSON.stringify(body) });
}

export function disconnectBroker(credentialId: number): Promise<{ status: string }> {
  return apiFetch(`/api/postmortem/brokers/${credentialId}`, { method: "DELETE" });
}

export function requestSync(): Promise<{ status: string; requested: number }> {
  return apiFetch(`/api/postmortem/sync`, { method: "POST" });
}

// ---- 체결내역 -----------------------------------------------------------
export type TradeFill = {
  id: number;
  broker: BrokerName;
  stock_code: string;
  stock_id: number | null;
  side: "buy" | "sell";
  filled_at: string | null;
  quantity: string | null;
  price: string | null;
  fee: string | null;
};

export function listFills(stockCode?: string): Promise<{ count: number; items: TradeFill[] }> {
  const qs = stockCode ? `?stock_code=${encodeURIComponent(stockCode)}` : "";
  return apiFetch(`/api/postmortem/fills${qs}`);
}

// ---- 매매 계획(선택) ----------------------------------------------------
export type TradePlan = {
  id: number;
  stock_code: string;
  stock_id: number | null;
  thesis: string;
  target_price: string | null;
  stop_price: string | null;
  sell_condition: string | null;
  planned_at: string | null;
  updated_at: string | null;
};

export type TradePlanBody = {
  stock_code: string;
  thesis?: string;
  target_price?: number | null;
  stop_price?: number | null;
  sell_condition?: string | null;
};

export function listTradePlans(): Promise<{ count: number; items: TradePlan[] }> {
  return apiFetch(`/api/postmortem/plans`);
}

export function upsertTradePlan(body: TradePlanBody): Promise<TradePlan> {
  return apiFetch(`/api/postmortem/plans`, { method: "POST", body: JSON.stringify(body) });
}

export function deleteTradePlan(stockCode: string): Promise<{ status: string }> {
  return apiFetch(`/api/postmortem/plans/${encodeURIComponent(stockCode)}`, { method: "DELETE" });
}

// ---- 단건 부검(라운드트립 + Plan vs Actual + 3분류 + 관측신호) ----------
export type PlanVsActual = {
  has_plan: boolean;
  evaluated?: boolean;
  thesis?: string | null;
  planned_stop_pct?: number;
  stop_violated?: boolean;
  planned_target_pct?: number;
  reached_target?: boolean;
  sell_condition?: string | null;
};

// verdict: on_plan_or_ok(부진 아님) / observable_signal(관측 가능 신호 있었음, ①·②) /
//          hindsight_only(그때 신호 없음 = 착시, 실수 아님 ③) / open(미청산)
export type Classification = {
  verdict: "on_plan_or_ok" | "observable_signal" | "hindsight_only" | "open";
  not_a_mistake?: boolean;
  insider_sell_count?: number;
};

export type ObservedSignal = {
  signal_date: string | null;
  kind: "insider_sell" | "insider_buy";
  detail: Record<string, unknown> | null;
};

export type RoundTrip = {
  opened_at: string | null;
  closed_at: string | null;
  is_open: boolean;
  quantity: string | null;
  avg_buy_price: string | null;
  avg_sell_price: string | null;
  realized_pnl_pct: number | null;
  holding_days: number | null;
  plan_vs_actual: PlanVsActual;
  classification: Classification;
  observed_signals: ObservedSignal[];
};

// FR-7: 워커 LLM 복기 서술(사후확신 없는 중립 요약). 미구성/비활성이면 null.
export type PostmortemNarrative = {
  summary: string;
  key_facts: string[];
  model: string | null;
};

export type TradePostmortem = {
  stock_code: string;
  stock_name: string | null;
  has_plan: boolean;
  round_trips: RoundTrip[];
  narrative: PostmortemNarrative | null;
};

export function getTradePostmortem(stockCode: string): Promise<TradePostmortem> {
  return apiFetch(`/api/postmortem/trades/${encodeURIComponent(stockCode)}`);
}

// ---- 패턴 부검 ----------------------------------------------------------
export type PatternSummary = {
  suppressed: boolean;
  sample: number;
  min_sample?: number;
  win_rate?: number;
  avg_win_pct?: number | null;
  avg_loss_pct?: number | null;
  avg_hold_win_days?: number | null;
  avg_hold_loss_days?: number | null;
  disposition_effect?: boolean;
  narrative?: PostmortemNarrative | null;
};

export function getPatterns(): Promise<PatternSummary> {
  return apiFetch(`/api/postmortem/patterns`);
}
