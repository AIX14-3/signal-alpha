// main-server(:8000) API 클라이언트 — 신규 기획 계약과 1:1.
// 베이스 URL 은 NEXT_PUBLIC_MAIN_API_BASE_URL 로 주입.

import { clearUserTokens, getAccessToken, setUserTokens } from "@/lib/session";

export const MAIN_API_BASE_URL =
  process.env.NEXT_PUBLIC_MAIN_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  code?: string;
  status: number;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

type AuthMode = "user" | "admin" | "none";
type FetchInit = RequestInit & { auth?: AuthMode };

function authHeader(mode: AuthMode): Record<string, string> {
  // admin 은 sa_admin HttpOnly 쿠키로 인증되므로 헤더를 붙이지 않는다(credentials:include 가 송신).
  if (mode === "user") {
    const token = getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  return {};
}

async function rawFetch(path: string, init: FetchInit): Promise<Response> {
  const { auth = "user", headers, ...rest } = init;
  return fetch(`${MAIN_API_BASE_URL}${path}`, {
    ...rest,
    credentials: "include", // refresh 토큰 HttpOnly 쿠키(sa_refresh) 송수신
    headers: {
      "Content-Type": "application/json",
      ...authHeader(auth),
      ...(headers as Record<string, string> | undefined),
    },
  });
}

async function parse<T>(res: Response): Promise<T> {
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    const code = detail && typeof detail === "object" ? detail.code : undefined;
    const message =
      detail && typeof detail === "object"
        ? detail.message
        : typeof detail === "string"
          ? detail
          : res.statusText;
    throw new ApiError(message ?? "요청에 실패했습니다.", res.status, code);
  }
  return data as T;
}

// refresh 토큰은 HttpOnly 쿠키로 자동 송신되므로 body 가 필요 없다.
// 성공 시 새 access 토큰만 인메모리에 저장한다. 부팅 hydrate 와 401 자동복구가 함께 사용한다.
export async function refreshSession(): Promise<boolean> {
  const res = await rawFetch("/api/auth/refresh", { method: "POST", auth: "none" });
  if (!res.ok) {
    clearUserTokens();
    return false;
  }
  const data = (await res.json()) as { access_token: string };
  setUserTokens(data.access_token);
  return true;
}

async function apiFetch<T>(path: string, init: FetchInit = {}): Promise<T> {
  let res = await rawFetch(path, init);
  if (res.status === 401 && (init.auth ?? "user") === "user") {
    const refreshed = await refreshSession();
    if (refreshed) res = await rawFetch(path, init);
  }
  return parse<T>(res);
}

/* ===== 타입 ===== */
export type User = {
  id: number;
  member_code: string | null;
  nickname: string | null;
  email: string | null;
  phone_masked: string | null;
  agreed_risk: boolean;
  subscription_active: boolean;
};

export type AuthResult = {
  user: User;
  access_token: string;
  // refresh 토큰은 HttpOnly 쿠키로만 전달된다(응답 body 에는 없음).
  refresh_token?: string;
  token_type?: string;
  notice?: string;
};

export type Stock = {
  id: number;
  stock_code: string;
  stock_name: string;
  market: string | null;
  sector: string | null;
};

export type WatchlistItem = { stock: Stock; created_at: string | null };

export type SourceKey = "price" | "dart" | "hiring" | "datalab" | "patent" | "report";

export type ReportSource = {
  source: SourceKey;
  direction?: string | null;
  score?: number | null;
  data_status?: string;
  summary?: string | null;
  locked: boolean;
};

// 메타러너 소스별 예측률 — 주가 BASE ⊕ 각 공공데이터로 만든 0–100 'AI 예측 점수'.
// 통합(SRC)은 Report.score(헤드라인)로 노출되고, 여기엔 per-source(주가 1 + 공공데이터 5)만 담긴다.
export type PredictionRate = {
  source: SourceKey;
  score?: number | null; // 0–100 (score_100)
  direction?: string | null; // positive | negative | neutral | unknown
  data_status?: string;
  locked: boolean;
};

export type ReportAccess = {
  unlocked: boolean;
  is_member: boolean;
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
  access: ReportAccess;
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


export type Provider = "naver" | "google" | "kakao";
export type SocialLink = { provider: Provider; linked: boolean; linked_at?: string | null };

export type Plan = {
  plan_type: string;
  plan_display_name: string;
  max_watchlist: number;
  signal_delay_hours: number;
  journal_max_entries: number;
  has_alt_data: boolean;
  has_detail_report: boolean;
  has_backtesting: boolean;
  price_monthly: number;
  price_yearly: number;
};

export type Subscription = {
  plan_type: string;
  status: string;
  started_at: string | null;
  expires_at: string | null;
  // 갱신 중지형 해지 예약 시각. 설정돼 있으면 만료일까지만 이용(이후 종료).
  cancelled_at?: string | null;
  billing_cycle: string | null;
  days_remaining?: number | null;
  expiring_soon?: boolean;
};

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
  tags: string[];
  // 작성 시점 신호 스냅샷 — 리포트가 갱신돼도 "그때 판단"의 근거를 보존.
  signal_score_at_time: number | null;
  signal_value_at_time: string | null;
  source_agreement_at_time: string | null;
  outcomes: JournalOutcome[];
  created_at: string | null;
  updated_at: string | null;
};

export type CheckoutInfo = {
  payment_id: string;
  amount: number;
  order_name: string;
  currency: string;
  plan_type: string;
  customer: {
    email: string | null;
    full_name: string | null;
    phone_number: string | null;
  };
};

/* ===== 인증(포트원 V2 본인인증) ===== */
export async function signup(body: {
  identity_verification_id: string;
  email: string;
  nickname: string;
  agreed_risk: boolean;
  agreed_terms?: string[];
}): Promise<AuthResult> {
  return apiFetch("/api/auth/signup", { method: "POST", auth: "none", body: JSON.stringify(body) });
}

export async function login(body: { identity_verification_id: string }): Promise<AuthResult> {
  return apiFetch("/api/auth/login", { method: "POST", auth: "none", body: JSON.stringify(body) });
}

export async function logout(): Promise<void> {
  // refresh 토큰은 HttpOnly 쿠키로 자동 송신 → 서버가 쿠키를 읽어 세션 폐기·삭제한다.
  await apiFetch("/api/auth/logout", { method: "POST", auth: "none" });
}

export async function getMe(): Promise<User> {
  return apiFetch("/api/users/me");
}

export async function updateMe(body: { nickname?: string | null; email?: string | null }): Promise<User> {
  return apiFetch("/api/users/me", { method: "PATCH", body: JSON.stringify(body) });
}

export async function deleteMe(): Promise<{ status: string }> {
  return apiFetch("/api/users/me", { method: "DELETE" });
}

/* ===== 소셜 연동 ===== */
export async function listSocial(): Promise<{ items: SocialLink[] }> {
  return apiFetch("/api/auth/social");
}

export async function linkSocial(
  provider: Provider,
  body: { code: string; redirect_uri?: string; state?: string },
): Promise<SocialLink> {
  return apiFetch(`/api/auth/social/link/${provider}`, { method: "POST", body: JSON.stringify(body) });
}

export async function socialLogin(
  provider: Provider,
  body: { code: string; redirect_uri?: string; state?: string },
): Promise<AuthResult> {
  return apiFetch(`/api/auth/social/login/${provider}`, {
    method: "POST",
    auth: "none",
    body: JSON.stringify(body),
  });
}

export async function unlinkSocial(provider: Provider): Promise<SocialLink> {
  return apiFetch(`/api/auth/social/${provider}`, { method: "DELETE" });
}

/* ===== 종목/관심종목(무제한) ===== */
export async function searchStocks(query: string): Promise<{ items: Stock[] }> {
  return apiFetch(`/api/stocks/search?query=${encodeURIComponent(query)}`, { auth: "none" });
}

export async function listStocks(limit = 100): Promise<{ items: Stock[] }> {
  return apiFetch(`/api/stocks?limit=${limit}`, { auth: "none" });
}

export async function listWatchlists(): Promise<{ count: number; items: WatchlistItem[] }> {
  return apiFetch("/api/watchlists");
}

export async function addWatchlist(stockCode: string): Promise<WatchlistItem> {
  return apiFetch("/api/watchlists", { method: "POST", body: JSON.stringify({ stock_code: stockCode }) });
}

export async function removeWatchlist(stockCode: string): Promise<{ status: string }> {
  return apiFetch(`/api/watchlists/${encodeURIComponent(stockCode)}`, { method: "DELETE" });
}

/* ===== 리포트 ===== */
export async function getReport(stockCode: string): Promise<Report> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}`);
}


export async function getSourceDetail(stockCode: string, source: SourceKey): Promise<SourceDetail> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}/sources/${source}`);
}

/* ===== 저널 ===== */
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
  body: { user_view?: string; memo?: string; tags?: string[] },
): Promise<Journal> {
  return apiFetch(`/api/journals/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function deleteJournal(id: number): Promise<{ status: string }> {
  return apiFetch(`/api/journals/${id}`, { method: "DELETE" });
}

/* ===== 구독/결제 ===== */
export async function listPlans(): Promise<{ plans: Plan[] }> {
  return apiFetch("/api/subscriptions/plans", { auth: "none" });
}

export async function getMySubscription(): Promise<{ subscription: Subscription | null; plan: Plan | null }> {
  return apiFetch("/api/subscriptions/me");
}

export async function checkout(cycle: "monthly" | "yearly" = "monthly"): Promise<CheckoutInfo> {
  return apiFetch("/api/payments/checkout", { method: "POST", body: JSON.stringify({ cycle }) });
}

export async function confirmPayment(body: { payment_id: string }): Promise<{
  subscription: Subscription;
}> {
  return apiFetch("/api/payments/confirm", { method: "POST", body: JSON.stringify(body) });
}

export async function cancelPayment(): Promise<{
  status: string;
  expires_at: string | null;
  notice: string;
}> {
  return apiFetch("/api/payments/cancel", { method: "POST" });
}

export async function resumePayment(): Promise<{
  status: string;
  expires_at: string | null;
  notice: string;
}> {
  return apiFetch("/api/payments/resume", { method: "POST" });
}

export async function refundPayment(): Promise<{
  status: string;
  amount: number;
  kind: string;
  notice: string;
}> {
  return apiFetch("/api/payments/refund", { method: "POST" });
}

export type PaymentHistoryItem = {
  payment_id: string;
  status: string;
  amount: number;
  order_name?: string;
  paid_at: string | null;
};

export async function paymentHistory(): Promise<{ items: PaymentHistoryItem[] }> {
  return apiFetch("/api/payments/history");
}

export type Receipt = {
  payment_id: string;
  order_name: string;
  amount: number;
  status: string;
  paid_at: string | null;
  customer: { email: string | null; name: string | null };
};

export async function paymentReceipt(paymentId: string): Promise<Receipt> {
  return apiFetch(`/api/payments/${encodeURIComponent(paymentId)}/receipt`);
}

/* ===== 관리자(별도 세션 토큰) ===== */
export type AdminUser = {
  id: number;
  email: string | null;
  nickname: string | null;
  member_code: string | null;
  created_at: string | null;
  subscription: { plan_type: string; status: string } | null;
};

export type AdminStats = {
  mrr: number;
  total_users: number;
  active_subscriptions: number;
  by_plan: Record<string, number>;
  revenue_monthly: { month: string; amount: number }[];
};

export async function adminLogin(body: { email: string; password: string }): Promise<{
  expires_at: string;
  admin: { id: number; email: string };
}> {
  // 세션 토큰은 sa_admin HttpOnly 쿠키로 설정됨(body 에 토큰 없음).
  return apiFetch("/api/admin/login", { method: "POST", auth: "none", body: JSON.stringify(body) });
}

export async function adminMe(): Promise<{ admin: { id: number; email: string } }> {
  return apiFetch("/api/admin/me", { auth: "admin" });
}

export async function adminLogout(): Promise<{ status: string }> {
  return apiFetch("/api/admin/logout", { method: "POST", auth: "admin" });
}

export async function adminListUsers(params: { page?: number; size?: number; q?: string } = {}): Promise<{
  total: number;
  page: number;
  size: number;
  items: AdminUser[];
}> {
  const search = new URLSearchParams();
  if (params.page) search.set("page", String(params.page));
  if (params.size) search.set("size", String(params.size));
  if (params.q) search.set("q", params.q);
  const qs = search.toString();
  return apiFetch(`/api/admin/users${qs ? `?${qs}` : ""}`, { auth: "admin" });
}

export async function adminUpdateUser(
  userId: number,
  body: { nickname?: string; email?: string },
): Promise<AdminUser> {
  return apiFetch(`/api/admin/users/${userId}`, {
    method: "PATCH",
    auth: "admin",
    body: JSON.stringify(body),
  });
}

export async function adminDeleteUser(userId: number): Promise<{ status: string; user_id: number }> {
  return apiFetch(`/api/admin/users/${userId}`, { method: "DELETE", auth: "admin" });
}

export async function adminSetSubscription(
  userId: number,
  body: { plan_type: string; status?: string; expires_at?: string },
): Promise<{ status: string }> {
  return apiFetch(`/api/admin/users/${userId}/subscription`, {
    method: "POST",
    auth: "admin",
    body: JSON.stringify(body),
  });
}

export async function adminCancelSubscription(userId: number): Promise<{ status: string }> {
  return apiFetch(`/api/admin/users/${userId}/subscription`, { method: "DELETE", auth: "admin" });
}

export async function adminRefund(userId: number): Promise<{ status: string; user_id: number }> {
  return apiFetch(`/api/admin/users/${userId}/refund`, { method: "POST", auth: "admin" });
}

export async function adminGetStats(): Promise<AdminStats> {
  return apiFetch("/api/admin/stats", { auth: "admin" });
}

export type AdminSchedule = {
  id: number;
  name: string | null;
  enabled: boolean;
  run_at_local: string | null; // "HH:MM"
  timezone: string | null;
  targets: string[];
  dart_limit: number | null;
  price_modes: string[];
  report_limit: number | null;
  report_days_back: number | null;
  report_max_pages: number | null;
  alternative_collect_enabled: boolean | null;
  alternative_analyze_enabled: boolean | null;
  alternative_collect_timeout_seconds: number | null;
  alternative_analyze_timeout_seconds: number | null;
  backpressure_max_waiting: number | null;
  backpressure_max_failed: number | null;
  frequency_minutes: number | null;
  active_from_local: string | null;
  active_until_local: string | null;
  last_run_at: string | null;
  last_status: string | null;
  last_detail: unknown;
  next_run_at: string | null;
  health_status: string | null;
  health_label: string | null;
  health_detail: string | null;
  manual_trigger_requested_at: string | null;
  updated_by: string | null;
  updated_at: string | null;
};

export type AdminScheduleRun = {
  id: number;
  schedule_id: number | null;
  schedule_name: string | null;
  trigger_reason: string | null;
  targets: string[];
  status: string | null;
  detail: unknown;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
};

export type AdminScheduleDryRun = {
  would_fire: boolean;
  evaluated_at: string | null;
  decision: {
    agent?: string;
    policy?: string;
    action?: string;
    reason?: string;
    schedule_id?: number;
    schedule_name?: string;
    targets?: string[];
  };
  next_run_at: string | null;
  backpressure: {
    reason?: string | null;
    max_waiting?: number | null;
    max_failed?: number | null;
    waiting?: number | null;
    failed?: number | null;
  };
  policy?: Record<string, unknown>;
};

export type AdminQueueOverview = {
  queue: {
    total: number;
    totals_by_status: Record<string, number>;
    items: { task_type: string; status: string; count: number }[];
    dead_letter?: {
      total: number;
      unreplayed: number;
      items: unknown[];
    };
  };
  failed_tasks: {
    count: number;
    items: AdminQueueTask[];
  };
  dead_letters: {
    count: number;
    items: AdminDeadLetter[];
  };
  schedule_summary: {
    total: number;
    attention_count: number;
    by_health_status: Record<string, number>;
  };
  events: AdminQueueEvent[];
};

export type AdminQueueTask = {
  id: number;
  task_type: string;
  status: string;
  stock_code?: string | null;
  retry_count?: number | null;
  max_retry_count?: number | null;
  last_error?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
};

export type AdminDeadLetter = {
  id: number;
  task_type: string;
  stock_code?: string | null;
  replayed_at?: string | null;
  error_message?: string | null;
  created_at?: string | null;
};

export type AdminQueueEvent = {
  type: string;
  severity: string;
  message: string;
  count?: number | null;
};

export async function adminListSchedules(): Promise<{ items: AdminSchedule[] }> {
  return apiFetch("/api/admin/schedules", { auth: "admin" });
}

export async function adminGetQueueOverview(): Promise<AdminQueueOverview> {
  return apiFetch("/api/admin/queue/overview", { auth: "admin" });
}

export async function adminSweepStaleQueue(body: {
  running_timeout_minutes?: number;
  retrying_timeout_minutes?: number;
} = {}): Promise<Record<string, number>> {
  return apiFetch("/api/admin/queue/sweep-stale", {
    method: "POST",
    auth: "admin",
    body: JSON.stringify(body),
  });
}

export async function adminRetryQueueTask(taskId: number): Promise<AdminQueueTask> {
  return apiFetch(`/api/admin/queue/tasks/${taskId}/retry`, { method: "POST", auth: "admin" });
}

export async function adminReplayDeadLetters(deadLetterIds: number[]): Promise<{
  replayed_count: number;
  results: unknown[];
}> {
  return apiFetch("/api/admin/queue/dead-letter/replay", {
    method: "POST",
    auth: "admin",
    body: JSON.stringify({ dead_letter_ids: deadLetterIds }),
  });
}

export async function adminReconcileDeadLetters(limit = 100): Promise<Record<string, number>> {
  return apiFetch("/api/admin/queue/dead-letter/reconcile", {
    method: "POST",
    auth: "admin",
    body: JSON.stringify({ limit }),
  });
}

export async function adminListScheduleRuns(
  scheduleId: number,
  limit = 5,
): Promise<{ items: AdminScheduleRun[] }> {
  return apiFetch(`/api/admin/schedules/${scheduleId}/runs?limit=${limit}`, { auth: "admin" });
}

export async function adminDryRunSchedule(scheduleId: number): Promise<AdminScheduleDryRun> {
  return apiFetch(`/api/admin/schedules/${scheduleId}/dry-run`, { method: "POST", auth: "admin" });
}

export async function adminUpdateSchedule(
  scheduleId: number,
  body: {
    enabled?: boolean;
    run_at_local?: string;
    timezone?: string;
    targets?: string[];
    dart_limit?: number;
    price_modes?: string[];
    report_limit?: number;
    report_days_back?: number;
    report_max_pages?: number;
    alternative_collect_enabled?: boolean;
    alternative_analyze_enabled?: boolean;
    alternative_collect_timeout_seconds?: number;
    alternative_analyze_timeout_seconds?: number;
    backpressure_max_waiting?: number;
    backpressure_max_failed?: number;
    frequency_minutes?: number;
    active_from_local?: string;
    active_until_local?: string;
  },
): Promise<AdminSchedule> {
  return apiFetch(`/api/admin/schedules/${scheduleId}`, {
    method: "PUT",
    auth: "admin",
    body: JSON.stringify(body),
  });
}

export async function adminTriggerSchedule(scheduleId: number): Promise<AdminSchedule> {
  return apiFetch(`/api/admin/schedules/${scheduleId}/trigger`, { method: "POST", auth: "admin" });
}
