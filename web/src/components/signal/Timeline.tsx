// 실API 근거 타임라인 — 일반 이벤트 배열을 받는다(대시보드 StoryItem 의존 없음).

export interface TimelinePoint {
  label: string;
  time: string;
  /** true=긍정(오렌지), false=부정(빨강), null/undefined=중립(회색) */
  up?: boolean | null;
}

export function Timeline({ points }: { points: TimelinePoint[] }) {
  if (!points || points.length === 0) return null;
  return (
    <div className="mt-4 flex w-full items-start overflow-x-auto pb-2">
      {points.map((t, i) => {
        const color =
          t.up === true ? "bg-orange-500" : t.up === false ? "bg-red-400" : "bg-neutral-300";
        return (
          <div key={`${t.label}-${i}`} className="flex min-w-0 flex-1 items-center">
            <div className="flex shrink-0 flex-col items-center text-center">
              <div className={`h-3 w-3 rounded-full ${color}`} />
              <span className="mt-1 whitespace-nowrap text-[9px] font-bold text-neutral-700">
                {t.label}
              </span>
              <span className="text-[8px] text-neutral-400">{t.time}</span>
            </div>
            {i < points.length - 1 && (
              <div className="mx-1 h-0.5 min-w-[12px] flex-1 bg-neutral-200" />
            )}
          </div>
        );
      })}
    </div>
  );
}
