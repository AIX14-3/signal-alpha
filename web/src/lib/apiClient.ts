// main-server API 클라이언트 (자리표시 단계).
// 실제 API 연동 전까지 기본 URL만 노출한다. 운영/로컬 모두
// NEXT_PUBLIC_MAIN_API_BASE_URL 환경변수로 주입한다(docker-compose 참고).

export const MAIN_API_BASE_URL =
  process.env.NEXT_PUBLIC_MAIN_API_BASE_URL ?? "http://localhost:8000";

/**
 * 대시보드 초기 화면에서 보여줄 API 클라이언트 자리표시 문자열.
 * 이후 main-server의 실제 엔드포인트 호출로 교체한다.
 */
export function getDashboardPlaceholder(): string {
  return `${MAIN_API_BASE_URL} 연결 예정`;
}
