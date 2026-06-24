// main-server(:8000) API 클라이언트 — 신규 기획 계약과 1:1.
// 베이스 URL 은 NEXT_PUBLIC_MAIN_API_BASE_URL 로 주입.

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

type AuthMode = "user" | "admin" | "none";
type FetchInit = RequestInit & { auth?: AuthMode };

function authHeader(mode: AuthMode): Record<string, string> {
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
  if (res.status === 401 && (init.auth ?? "user") === "user" && getRefreshToken()) {
    const refreshed = await tryRefresh();
    if (refreshed) res = await rawFetch(path, init);
  }
  return parse<T>(res);
}

/* ===== 타입 ===== */
export type User = {
  id: number;
  member_code: string | null;
  nickname: string | null;
  phone_masked: string | null;
  agreed_risk: boolean;
  subscription_active: boolean;
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

export type WatchlistItem = { stock: Stock; created_at: string | null };

export type SourceKey = "price" | "dart" | "hiring" | "datalab" | "report";

export type ReportSource = {
  source: SourceKey;
  direction?: string | null;
  score?: number | null;
  data_status?: string;
  summary?: string | null;
  locked: boolean;
};

export type ReportAccess = {
  unlocked: boolean;
  is_member: boolean;
  issued_via?: "free" | "subscription";
  free_remaining?: number;
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
  sources: ReportSource[];
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

export type SourceDetail = {
  stock: { stock_code: string; stock_name: string | null };
  source: SourceKey;
  direction: string | null;
  score: number | null;
  data_status?: string;
  summary: string | null;
  items: SourceDetailItem[];
  notice: string;
};

export type Quota = {
  free_quota: number;
  free_used: number;
  free_remaining: number;
  subscription_active: boolean;
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
  billing_cycle: string | null;
};

export type Journal = {
  journal_id: number;
  stock_code: string;
  stock_name: string | null;
  final_signal_id: number | null;
  user_view: string;
  memo: string | null;
  tags: string[];
  created_at: string | null;
  updated_at: string | null;
};

export type CheckoutInfo = {
  payment_id: string;
  amount: number;
  order_name: string;
  currency: string;
  plan_type: string;
};

/* ===== 인증(포트원 V2 본인인증) ===== */
export async function signup(body: {
  identity_verification_id: string;
  agreed_risk: boolean;
  nickname?: string;
  agreed_terms?: string[];
}): Promise<AuthResult> {
  return apiFetch("/api/auth/signup", { method: "POST", auth: "none", body: JSON.stringify(body) });
}

export async function login(body: { identity_verification_id: string }): Promise<AuthResult> {
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

export async function updateMe(body: { nickname?: string | null }): Promise<User> {
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

export async function issueReport(stockCode: string): Promise<Report> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}/issue`, { method: "POST" });
}

export async function getQuota(): Promise<Quota> {
  return apiFetch("/api/reports/quota");
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

export async function checkout(): Promise<CheckoutInfo> {
  return apiFetch("/api/payments/checkout", { method: "POST" });
}

export async function confirmPayment(body: { payment_id: string }): Promise<{
  subscription: Subscription;
}> {
  return apiFetch("/api/payments/confirm", { method: "POST", body: JSON.stringify(body) });
}

export async function cancelPayment(): Promise<{ status: string }> {
  return apiFetch("/api/payments/cancel", { method: "POST" });
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
  session_token: string;
  expires_at: string;
  admin: { id: number; email: string };
}> {
  return apiFetch("/api/admin/login", { method: "POST", auth: "none", body: JSON.stringify(body) });
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

export async function adminGetStats(): Promise<AdminStats> {
  return apiFetch("/api/admin/stats", { auth: "admin" });
}
