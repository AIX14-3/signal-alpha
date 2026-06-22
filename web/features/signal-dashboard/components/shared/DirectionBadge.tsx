import { dirChipClass } from '@/lib/signal/utils';
import type { Direction } from '@/types/signal';

interface DirectionBadgeProps {
  direction: Direction | string;
  className?: string;
}

export function DirectionBadge({ direction, className = '' }: DirectionBadgeProps) {
  return (
    <span
      className={`rounded border px-2 py-0.5 text-[10px] ${dirChipClass(direction)} ${className}`}
    >
      {direction}
    </span>
  );
}
