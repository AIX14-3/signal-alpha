import { Battery, Signal, Wifi } from 'lucide-react';

export function PhoneMockup() {
  return (
    <div className="relative w-[260px] sm:w-[280px] md:w-[300px]">
      <div className="absolute inset-4 rounded-[3rem] bg-orange-400/25 blur-3xl" />

      {/* 본체 */}
      <div className="phone-frame relative rounded-[3rem] bg-gradient-to-b from-neutral-700 via-neutral-800 to-neutral-950 p-[11px] shadow-[0_32px_64px_-12px_rgba(0,0,0,0.45)] ring-1 ring-white/10">
        {/* 측면 버튼 */}
        <div className="absolute -left-[3px] top-[88px] h-7 w-[3px] rounded-l-md bg-neutral-500/80" />
        <div className="absolute -left-[3px] top-[128px] h-11 w-[3px] rounded-l-md bg-neutral-500/80" />
        <div className="absolute -left-[3px] top-[168px] h-11 w-[3px] rounded-l-md bg-neutral-500/80" />
        <div className="absolute -right-[3px] top-[120px] h-14 w-[3px] rounded-r-md bg-neutral-500/80" />

        {/* 스크린 */}
        <div className="relative aspect-[9/19.5] overflow-hidden rounded-[2.35rem] bg-white">
          {/* 다이나믹 아일랜드 */}
          <div className="absolute left-1/2 top-[10px] z-20 h-[26px] w-[96px] -translate-x-1/2 rounded-full bg-black shadow-inner">
            <div className="absolute right-3 top-1/2 h-2 w-2 -translate-y-1/2 rounded-full bg-neutral-800 ring-1 ring-neutral-700" />
          </div>

          {/* 상태바 */}
          <div className="relative z-10 flex items-center justify-between px-7 pb-1 pt-[14px] text-[11px] font-semibold text-neutral-900">
            <span className="tabular-nums">9:41</span>
            <div className="flex items-center gap-1.5">
              <Signal className="h-3 w-3" strokeWidth={2.5} />
              <Wifi className="h-3 w-3" strokeWidth={2.5} />
              <Battery className="h-3.5 w-3.5" strokeWidth={2.5} />
            </div>
          </div>

          {/* 앱 헤더 */}
          <div className="border-b border-neutral-100 px-5 pb-3 pt-2">
            <div className="flex items-center gap-2">
              <div className="brand-mark flex h-6 w-6 items-center justify-center rounded-md">
                <span className="text-[10px] font-black leading-none text-white">α</span>
              </div>
              <span className="text-xs font-bold text-neutral-900">
                Signal <span className="text-orange-600">α</span>
              </span>
            </div>
          </div>

          {/* 콘텐츠 */}
          <div className="flex flex-1 flex-col px-5 pb-10 pt-5">
            <p className="text-[11px] font-medium text-neutral-400">종합 신뢰도</p>
            <p className="mt-1 text-5xl font-black tracking-tight text-orange-600">87</p>
            <div className="mt-4 h-2 overflow-hidden rounded-full bg-neutral-100">
              <div className="h-full w-[87%] rounded-full bg-gradient-to-r from-orange-500 to-orange-400" />
            </div>

            <div className="mt-5 grid grid-cols-3 gap-2 text-center text-[10px]">
              {[
                { score: 85, label: 'DART' },
                { score: 82, label: 'RAG' },
                { score: 93, label: 'Alt' },
              ].map((item) => (
                <div
                  key={item.label}
                  className="rounded-xl border border-orange-100 bg-orange-50/80 py-2.5 shadow-sm"
                >
                  <b className="text-sm text-orange-600">{item.score}</b>
                  <p className="mt-0.5 font-medium text-neutral-500">{item.label}</p>
                </div>
              ))}
            </div>

            {/* 미니 차트 힌트 */}
            <div className="mt-5 rounded-xl border border-neutral-100 bg-neutral-50 p-3">
              <p className="text-[9px] font-bold uppercase tracking-wide text-neutral-400">
                SK하이닉스 · 1M
              </p>
              <svg viewBox="0 0 200 48" className="mt-2 h-12 w-full" preserveAspectRatio="none">
                <defs>
                  <linearGradient id="phoneChartGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#ef4444" stopOpacity="0.3" />
                    <stop offset="100%" stopColor="#ef4444" stopOpacity="0" />
                  </linearGradient>
                </defs>
                <polygon
                  fill="url(#phoneChartGrad)"
                  points="0,48 0,32 25,28 50,30 75,22 100,18 125,20 150,12 175,8 200,6 200,48"
                />
                <polyline
                  fill="none"
                  stroke="#ef4444"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  points="0,32 25,28 50,30 75,22 100,18 125,20 150,12 175,8 200,6"
                />
              </svg>
            </div>

            <div className="mt-auto flex items-center justify-between rounded-xl bg-orange-50 px-3 py-2.5">
              <span className="text-[10px] font-medium text-neutral-600">3소스 HIGH 일치</span>
              <span className="rounded-full bg-orange-500 px-2 py-0.5 text-[9px] font-bold text-white">
                POSITIVE
              </span>
            </div>
          </div>

          {/* 홈 인디케이터 */}
          <div className="absolute bottom-[6px] left-1/2 z-10 h-[4px] w-[108px] -translate-x-1/2 rounded-full bg-neutral-900/20" />
        </div>
      </div>
    </div>
  );
}
