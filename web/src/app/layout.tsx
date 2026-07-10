import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import "./globals.css";

export const metadata: Metadata = {
  // OG/canonical 상대경로가 절대 URL 로 해석되도록 base 를 지정(미지정 시 Next 경고).
  // 배포 도메인은 NEXT_PUBLIC_SITE_URL 로 주입, 로컬은 localhost 폴백.
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: "Signal α — AI 투자 신호 분석",
  description: "공시·재무·뉴스·수급·시계열 데이터를 교차검증해 방향성과 근거를 보여주는 서비스",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <link
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css"
          rel="stylesheet"
        />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
