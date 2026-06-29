// 로딩 스켈레톤 프리미티브 (현재 앱엔 "불러오는 중…" 텍스트뿐이라 신설).
import { cn } from "@/lib/utils";

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-neutral-200/70", className)} />;
}

/** 리포트 페이지용 스켈레톤: 종합 카드 + 소스 카드 그리드. */
export function ReportSkeleton() {
  return (
    <div className="py-10">
      <Skeleton className="h-5 w-40" />
      <Skeleton className="mt-2 h-9 w-64" />
      <div className="card mt-5 flex flex-wrap items-center gap-6 p-6">
        <Skeleton className="h-[120px] w-[120px] rounded-full" />
        <div className="flex-1 space-y-3">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      </div>
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-[140px] rounded-[18px]" />
        ))}
      </div>
    </div>
  );
}
