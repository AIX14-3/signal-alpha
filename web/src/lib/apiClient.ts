// main-server(:8000) API 클라이언트. Phase 2 설계도 계약과 1:1.
// 운영/로컬 모두 NEXT_PUBLIC_MAIN_API_BASE_URL 로 베이스 URL 주입.

import {
  clearUserTokens,
  getAccessToken,
  getAdminToken,
  getRefreshToken,
  setUserTokens,
} from "@/lib/session";

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

type FetchInit = RequestInit & { auth?: "user" | "admin" | "none" };

function authHeader(mode: "user" | "admin" | "none"): Record<string, string> {
  if (mode === "admin") {
    const token = getAdminToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
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

async function tryRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  const res = await rawFetch("/api/auth/refresh", {
    method: "POST",
    auth: "none",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) {
    clearUserTokens();
    return false;
  }
  const data = (await res.json()) as { access_token: string; refresh_token: string };
  setUserTokens(data.access_token, data.refresh_token);
  return true;
}

async function apiFetch<T>(path: string, init: FetchInit = {}): Promise<T> {
  let res = await rawFetch(path, init);
  // 토큰 만료 → 1회 갱신 후 재시도 (user 인증 경로만).
  if (res.status === 401 && (init.auth ?? "user") === "user" && getRefreshToken()) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      res = await rawFetch(path, init);
    }
  }
  return parse<T>(res);
}

/* ===== 타입 ===== */
export type User = {
  id: number;
  email: string;
  nickname: string | null;
  agreed_risk: boolean;
  is_verified: boolean;
};

export type AuthResult = {
  user: User;
  access_token: string;
  refresh_token: string;
  notice?: string;
};

export type Stock = {
  id: number;
  stock_code: string;
  stock_name: string;
  market: string | null;
  sector: string | null;
};

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
  billing_cycle: string | null;
};

export type SignalSource = {
  source: string;
  direction: string;
  score: number | null;
  data_status: string;
  summary: string | null;
  risk_flags: unknown[];
  evidence: SignalEvidence[];
};

export type SignalEvidence = {
  id: number;
  title: string | null;
  summary: string | null;
  event_date: string | null;
  direction: string | null;
  impact_level: string | null;
  evidence_url: string | null;
  source_name: string | null;
};

export type SignalDetail = {
  signal_id: number;
  stock: { id: number; stock_code: string; stock_name: string; market: string | null; sector: string | null };
  direction: string;
  score: number | null;
  alignment_rate: number | null;
  source_agreement: string | null;
  warning_level: string | null;
  summary: string | null;
  positive_evidence: unknown[];
  caution_evidence: unknown[];
  sources: SignalSource[];
  notice?: string;
};

export type AnalysisStatus = {
  ticker: string;
  overall: "pending" | "running" | "success" | "failed";
  stages: { task_type: string; status: string; updated_at: string | null }[];
  notice?: string;
};

/* ===== 인증 ===== */
export async function signup(body: {
  email: string;
  password: string;
  agreed_risk: boolean;
  nickname?: string;
}): Promise<AuthResult> {
  return apiFetch("/api/auth/signup", { method: "POST", auth: "none", body: JSON.stringify(body) });
}

export async function login(body: { email: string; password: string }): Promise<AuthResult> {
  return apiFetch("/api/auth/login", { method: "POST", auth: "none", body: JSON.stringify(body) });
}

export async function logout(refreshToken: string): Promise<void> {
  await apiFetch("/api/auth/logout", {
    method: "POST",
    auth: "none",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
}

export async function getMe(): Promise<User> {
  return apiFetch("/api/users/me");
}

/* ===== 종목/관심종목 ===== */
export async function searchStocks(query: string): Promise<{ items: Stock[] }> {
  return apiFetch(`/api/stocks/search?query=${encodeURIComponent(query)}`, { auth: "none" });
}

export type WatchlistItem = { stock: Stock; notification_enabled: boolean; created_at: string | null };

export async function listWatchlists(): Promise<{ limit: number; count: number; items: WatchlistItem[] }> {
  return apiFetch("/api/watchlists");
}

export async function addWatchlist(stockCode: string): Promise<WatchlistItem> {
  return apiFetch("/api/watchlists", { method: "POST", body: JSON.stringify({ stock_code: stockCode }) });
}

export async function removeWatchlist(stockCode: string): Promise<{ status: string }> {
  return apiFetch(`/api/watchlists/${encodeURIComponent(stockCode)}`, { method: "DELETE" });
}

/* ===== 시그널/리포트 ===== */
export async function getSignalByTicker(ticker: string): Promise<Record<string, unknown>> {
  return apiFetch(`/signals/${encodeURIComponent(ticker)}`, { auth: "none" });
}

export async function getSignalDetail(signalId: number): Promise<SignalDetail> {
  return apiFetch(`/api/signals/${signalId}`);
}

export async function getSignalByStock(stockCode: string): Promise<Record<string, unknown>> {
  return apiFetch(`/api/signals/by-stock/${encodeURIComponent(stockCode)}`);
}

/* ===== 분석 진행 ===== */
export async function getAnalysisStatus(ticker: string): Promise<AnalysisStatus> {
  return apiFetch(`/api/analytics/${encodeURIComponent(ticker)}/status`, { auth: "none" });
}

/* ===== 구독 ===== */
export async function listPlans(): Promise<{ plans: Plan[] }> {
  return apiFetch("/api/subscriptions/plans", { auth: "none" });
}

export async function getMySubscription(): Promise<{ subscription: Subscription | null; plan: Plan | null }> {
  return apiFetch("/api/subscriptions/me");
}

export async function changeSubscription(body: {
  plan_type: string;
  action?: "subscribe" | "cancel";
  billing_cycle?: "monthly" | "yearly";
}): Promise<{ subscription: Subscription | null; plan: Plan | null }> {
  return apiFetch("/api/subscriptions", { method: "POST", body: JSON.stringify(body) });
}

/* ===== 관리자 (별도 세션 토큰) ===== */
export type AdminUser = {
  id: number;
  email: string;
  nickname: string | null;
  member_code: string | null;
  created_at: string | null;
  subscription: { plan_type: string; status: string } | null;
};

export async function adminLogin(body: { email: string; password: string }): Promise<{
  session_token: string;
  expires_at: string;
  admin: { id: number; email: string };
}> {
  return apiFetch("/api/admin/login", { method: "POST", auth: "none", body: JSON.stringify(body) });
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

export type AdminStats = {
  mrr: number;
  total_users: number;
  active_subscriptions: number;
  by_plan: Record<string, number>;
  revenue_monthly: { month: string; amount: number }[];
};

export async function adminGetStats(): Promise<AdminStats> {
  return apiFetch("/api/admin/stats", { auth: "admin" });
}
