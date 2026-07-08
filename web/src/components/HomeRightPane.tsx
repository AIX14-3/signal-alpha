"use client";

import { CommunityPopularSection } from "@/components/CommunityPopularSection";
import { LiveAnalysisSection } from "@/components/LiveAnalysisSection";
import { WatchlistSection } from "@/components/WatchlistSection";

// 홈 v2 우 pane = 3섹션 세로 스택.
// ① 관심종목(로그인 분기) ② 실시간 분석 종목(인라인 아코디언: 차트·점수·분석·뉴스, /report 링크)
// ③ 커뮤니티 인기순위. 선택 종목 상세는 ②의 아코디언 안으로 흡수됐다.
export function HomeRightPane() {
  return (
    <aside data-pane="home-right" className="flex min-h-0 flex-col gap-5">
      <WatchlistSection />
      <LiveAnalysisSection />
      <CommunityPopularSection />
    </aside>
  );
}
