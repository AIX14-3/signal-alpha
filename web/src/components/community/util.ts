// 커뮤니티 표기 공용 헬퍼.

import type { CommunityAuthor } from "@/lib/apiClient";

// 작성자 표시명 — 닉네임 우선, 없으면 회원코드, 둘 다 없으면 익명.
export function authorLabel(author: CommunityAuthor | null | undefined): string {
  if (!author) return "익명";
  return author.nickname ?? author.member_code ?? "익명";
}

// ISO 타임스탬프 → "YYYY.MM.DD" (데이터 확정 후 클라이언트에서만 렌더).
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}.${m}.${day}`;
}

// 저널 user_view 코드 → 라벨(mypage 와 동일 어휘).
const VIEW_LABEL: Record<string, string> = {
  watch: "관심",
  research_more: "더 조사",
  not_relevant: "관련 없음",
};

export function viewLabel(view: string | null | undefined): string {
  if (!view) return "";
  return VIEW_LABEL[view] ?? view;
}
