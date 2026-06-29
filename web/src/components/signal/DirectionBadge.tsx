// 실API 리포트 페이지용 방향 배지 — #335 오렌지 칩 스타일 + 현재 정책 라벨.
// (대시보드(features/signal-dashboard)의 동명 컴포넌트와 분리. 이쪽은 정책 문구 사용.)
import { directionLabel } from "@/lib/format";
import { dirChipClass } from "@/lib/signal/utils";

interface DirectionBadgeProps {
  direction: string | null | undefined;
  className?: string;
}

export function DirectionBadge({ direction, className = "" }: DirectionBadgeProps) {
  const { label } = directionLabel(direction);
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-semibold ${dirChipClass(direction)} ${className}`}
    >
      {label}
    </span>
  );
}
