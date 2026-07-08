"use client";

import { useEffect } from "react";
import { BackgroundFX } from "@/components/BackgroundFX";
import { HomeLeftPane } from "@/components/HomeLeftPane";
import { HomeRightPane } from "@/components/HomeRightPane";
import { HomeSidebar } from "@/components/HomeSidebar";
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
      <div className="dash-blobs" aria-hidden="true" />
      <NewsSummaryBanner />
      {/* 대시보드(레퍼런스 오마주) — 글래스 패널: 좌 사이드바 / 중 KPI+뉴스 테이블 / 우 선택 종목 스탯 레일. */}
      <div className="glass relative z-10 mb-16 grid grid-cols-1 gap-5 p-4 sm:p-5 lg:grid-cols-[188px_1fr_340px] lg:gap-6 lg:p-6">
        <HomeSidebar />
        <HomeLeftPane />
        <HomeRightPane />
      </div>
    </>
  );
}
