"use client";

import { CommunityPopularSection } from "@/components/CommunityPopularSection";
import { LiveAnalysisSection } from "@/components/LiveAnalysisSection";

// 홈 대시보드 우 pane = 2섹션 세로 스택.
// ① 실시간 분석 종목(인라인 아코디언: 차트·점수·분석·뉴스, /report 링크) ② 커뮤니티 인기순위.
// 관심종목은 대시보드 상단 가로 밴드(page.tsx)로 승격됐다. 선택 종목 상세는 ①의 아코디언 안으로 흡수.
export function HomeRightPane() {
  return (
    <aside data-pane="home-right" className="flex min-h-0 flex-col gap-5">
      <LiveAnalysisSection />
      <CommunityPopularSection />
    </aside>
  );
}
