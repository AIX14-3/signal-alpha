import type { Metadata } from "next";
import type { ReactNode } from "react";

type Params = { ticker: string };

// 종목 리포트 세그먼트 레이아웃 — 화면(page.tsx, 클라이언트)은 그대로 두고 여기서
// 종목별 SEO metadata/OG 만 서버에서 생성한다. Next 15 는 generateMetadata 를 서버
// 컴포넌트에서만 export 할 수 있으므로, client page 를 서버로 바꾸지 않고 메타데이터를
// 붙이는 표준 경로가 이 세그먼트 layout 이다(레이아웃은 children 만 렌더 → DOM/화면 무변경).
export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}): Promise<Metadata> {
  const { ticker } = await params;
  const code = decodeURIComponent(ticker);
  const title = `${code} 종목 신호 리포트 — Signal α`;
  const description = `${code} 종목의 공시·재무·리포트·채용·특허·검색 트렌드 등 다중 소스 데이터를 교차검증해 방향성과 근거를 보여주는 신호 리포트입니다. 예측이 아닌 관측·합의 기반입니다.`;
  const path = `/report/${encodeURIComponent(code)}`;
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: {
      type: "article",
      title,
      description,
      url: path,
      siteName: "Signal α",
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
    },
  };
}

export default function ReportTickerLayout({ children }: { children: ReactNode }) {
  return children;
}
