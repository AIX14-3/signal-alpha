// main-server(:8000) API 클라이언트 — 신규 기획 계약과 1:1.
// 도메인별 모듈(lib/api/*)의 배럴. 기존 "@/lib/apiClient" import 경로를 그대로 유지한다.
// 새 코드는 필요하면 lib/api/<domain> 을 직접 import 해도 된다.

export { ApiError, MAIN_API_BASE_URL, apiFetch, refreshSession } from "./api/core";
export * from "./api/auth";
export * from "./api/social";
export * from "./api/stocks";
export * from "./api/reports";
export * from "./api/journal";
export * from "./api/community";
export * from "./api/methodology";
export * from "./api/billing";
export * from "./api/admin";
export * from "./api/guard";
