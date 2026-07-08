// 관리자(별도 세션 토큰 sa_admin HttpOnly 쿠키) — 유저·통계·큐운영·스케줄.

import { apiFetch } from "./core";
import type { CommunityAuthor, CommunityPost } from "./community";

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

export type AdminCommunityModerationPostItem = {
  target_type: "post";
  id: number;
  status: string | null;
  report_count: number;
  report_reasons: string[];
  latest_reported_at: string | null;
  post: CommunityPost;
};

export type AdminCommunityModerationCommentItem = {
  target_type: "comment";
  id: number;
  post_id: number;
  post_title: string | null;
  parent_comment_id: number | null;
  body: string | null;
  author: CommunityAuthor;
  status: string | null;
  report_count: number;
  report_reasons: string[];
  latest_reported_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type AdminCommunityModerationItem =
  | AdminCommunityModerationPostItem
  | AdminCommunityModerationCommentItem;

export type AdminCommunityModerationTarget = "all" | "post" | "comment";

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

export async function adminListSchedules(): Promise<{ items: AdminSchedule[] }> {
  return apiFetch("/api/admin/schedules", { auth: "admin" });
}

export async function adminGetQueueOverview(): Promise<AdminQueueOverview> {
  return apiFetch("/api/admin/queue/overview", { auth: "admin" });
}

export async function adminListCommunityModeration(
  params: { target_type?: AdminCommunityModerationTarget; limit?: number; cursor?: string | null } = {},
): Promise<{ items: AdminCommunityModerationItem[]; target_type: AdminCommunityModerationTarget; next_cursor: string | null }> {
  const search = new URLSearchParams();
  if (params.target_type) search.set("target_type", params.target_type);
  if (params.limit) search.set("limit", String(params.limit));
  if (params.cursor != null) search.set("cursor", params.cursor);
  const qs = search.toString();
  return apiFetch(`/api/admin/community/moderation${qs ? `?${qs}` : ""}`, { auth: "admin" });
}

export async function adminRestoreCommunityPost(
  postId: number,
): Promise<{ status: string; target_type: "post"; id: number }> {
  return apiFetch(`/api/admin/community/posts/${postId}/restore`, { method: "POST", auth: "admin" });
}

export async function adminDeleteCommunityPost(
  postId: number,
): Promise<{ status: string; target_type: "post"; id: number }> {
  return apiFetch(`/api/admin/community/posts/${postId}`, { method: "DELETE", auth: "admin" });
}

export async function adminRestoreCommunityComment(
  commentId: number,
): Promise<{ status: string; target_type: "comment"; id: number }> {
  return apiFetch(`/api/admin/community/comments/${commentId}/restore`, { method: "POST", auth: "admin" });
}

export async function adminDeleteCommunityComment(
  commentId: number,
): Promise<{ status: string; target_type: "comment"; id: number }> {
  return apiFetch(`/api/admin/community/comments/${commentId}`, { method: "DELETE", auth: "admin" });
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
