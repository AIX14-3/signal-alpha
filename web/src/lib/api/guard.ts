// 지정학 리스크 Kill-Switch(guard) — 공개 차단 상태 폴링 + 관리자 통제/추천 심사.

import { apiFetch } from "./core";

export type GuardScope = "report_generation" | "report_view" | "whole_site";
export type GuardMode = "manual" | "advisory" | "auto";

// 공개 차단 상태 — 프론트가 주기 폴링해 노출을 게이트한다(fail-open 은 guardStore 책임).
export type GuardStatus = {
  status: "ok" | "blocked";
  scope: GuardScope;
  reason: string | null;
  resume_at: string | null;
};

export type GuardAdminStatus = GuardStatus & {
  mode: GuardMode;
  triggered_by: string | null;
  updated_at: string | null;
};

export type GuardAudit = {
  action: string;
  scope: GuardScope | null;
  reason: string | null;
  actor: string | null;
  created_at: string | null;
};

export type GuardRecommendation = {
  id: number;
  news_event_id: number | null;
  suggested_scope: GuardScope;
  severity: number | null;
  reason: string | null;
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  decided_at: string | null;
  created_at: string | null;
  news_title: string | null;
  news_url: string | null;
};

export async function getGuardStatus(): Promise<GuardStatus> {
  return apiFetch("/api/guard/status", { auth: "none" });
}

export async function adminGetGuardStatus(): Promise<{
  status: GuardAdminStatus;
  audit: GuardAudit[];
}> {
  return apiFetch("/api/admin/guard/status", { auth: "admin" });
}

export async function adminUpdateGuardStatus(body: {
  status: "ok" | "blocked";
  scope: GuardScope;
  mode: GuardMode;
  reason?: string | null;
  resume_at?: string | null;
}): Promise<{ status: GuardAdminStatus }> {
  return apiFetch("/api/admin/guard/status", {
    method: "PUT",
    auth: "admin",
    body: JSON.stringify(body),
  });
}

export async function adminListGuardRecommendations(
  status: "pending" | "approved" | "rejected" = "pending",
): Promise<{ count: number; items: GuardRecommendation[] }> {
  return apiFetch(`/api/admin/guard/recommendations?status=${status}`, { auth: "admin" });
}

export async function adminDecideGuardRecommendation(
  recommendationId: number,
  decision: "approve" | "reject",
): Promise<{ recommendation: GuardRecommendation; status?: GuardAdminStatus }> {
  return apiFetch(`/api/admin/guard/recommendations/${recommendationId}/${decision}`, {
    method: "POST",
    auth: "admin",
  });
}
