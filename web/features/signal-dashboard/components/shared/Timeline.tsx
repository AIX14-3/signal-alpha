import type { StoryItem } from '@/types/signal';

export function Timeline({ item }: { item: StoryItem }) {
  return (
    <div className="mt-4 flex w-full items-start overflow-x-auto pb-2">
      {item.timeline.map((t, i) => {
        const color =
          t.up === true ? 'bg-orange-500' : t.up === false ? 'bg-red-400' : 'bg-neutral-300';
        return (
          <div key={t.label} className="flex min-w-0 flex-1 items-center">
            <div className="flex shrink-0 flex-col items-center text-center">
              <div className={`h-3 w-3 rounded-full ${color}`} />
              <span className="mt-1 whitespace-nowrap text-[9px] font-bold text-neutral-700">
                {t.label}
              </span>
              <span className="text-[8px] text-neutral-400">{t.time}</span>
            </div>
            {i < item.timeline.length - 1 && (
              <div className="mx-1 h-0.5 min-w-[12px] flex-1 bg-neutral-200" />
            )}
          </div>
        );
      })}
    </div>
  );
}
