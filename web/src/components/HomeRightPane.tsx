"use client";

import { LiveAnalysisSection } from "@/components/LiveAnalysisSection";

// 홈 대시보드 우 pane = 실시간 분석 종목(인라인 아코디언: 차트·점수·분석·뉴스, /report 링크).
// 관심종목은 상단 가로 밴드로, 커뮤니티 인기순위는 2컬럼 아래 전체 폭 밴드(page.tsx)로 분리됐다.
export function HomeRightPane() {
  return (
    <aside data-pane="home-right" className="flex min-h-0 flex-col gap-5">
      <LiveAnalysisSection />
    </aside>
  );
}
