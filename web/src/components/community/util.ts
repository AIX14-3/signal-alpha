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

export type PostDirection = {
  kind: "long" | "short" | "neutral";
  label: string;
  icon: string;
  // show_pnl=true 이고 pnl_pct 가 있을 때만 채워진다. 없으면 카드가 방향 라벨만 보여준다.
  pctText: string | null;
};

// 게시글 카드/상세의 시그널 블록에 쓸 방향 메타. pnl_pct 부호로만 판정한다
// (user_view 는 watch/research_more/not_relevant 라 상승/하락과 무관).
export function postDirection(post: {
  show_pnl: boolean;
  journal: { user_view: string | null; pnl_pct: number | null } | null;
}): PostDirection {
  const pnl = post.journal?.pnl_pct;
  if (post.show_pnl && pnl != null) {
    const up = pnl >= 0;
    return {
      kind: up ? "long" : "short",
      label: up ? "LONG" : "SHORT",
      icon: up ? "▲" : "▼",
      pctText: `${up ? "+" : ""}${pnl.toFixed(2)}%`,
    };
  }
  return {
    kind: "neutral",
    label: viewLabel(post.journal?.user_view) || "관망",
    icon: "◆",
    pctText: null,
  };
}

const AVATAR_PALETTE = [
  "linear-gradient(135deg,#a78bfa,#7c3aed)",
  "linear-gradient(135deg,#6ee7b7,#10b981)",
  "linear-gradient(135deg,#fca5a5,#ef4444)",
  "linear-gradient(135deg,#7dd3fc,#0ea5e9)",
  "linear-gradient(135deg,#fcd34d,#f59e0b)",
];

// 댓글 아바타용 결정론적 그라디언트 — 작성자 식별자로 팔레트에서 고정 선택(장식 목적, 의미 없음).
export function avatarGradient(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
}

export function avatarInitial(label: string): string {
  return label.trim().charAt(0) || "?";
}
