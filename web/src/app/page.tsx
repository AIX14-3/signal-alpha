"use client";

import { useEffect } from "react";
import { BackgroundFX } from "@/components/BackgroundFX";
import { HomeLeftPane } from "@/components/HomeLeftPane";
import { HomeRightPane } from "@/components/HomeRightPane";
import { NewsSummaryBanner } from "@/components/NewsSummaryBanner";
import { useHomeStore } from "@/stores/homeStore";

export default function HomePage() {
  const init = useHomeStore((s) => s.init);
  useEffect(() => {
    void init();
  }, [init]);

  return (
    <>
      <BackgroundFX />
      <NewsSummaryBanner />
      {/* FR-1 2-pane — 데스크톱 좌 리스트/우 상세, 모바일 세로 스택(Q3). */}
      <div className="relative z-10 grid grid-cols-1 gap-6 pb-16 lg:grid-cols-[minmax(320px,380px)_1fr]">
        <HomeLeftPane />
        <HomeRightPane />
      </div>
    </>
  );
}
