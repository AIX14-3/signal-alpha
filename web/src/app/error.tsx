"use client";

import { useEffect } from "react";

// App Router 라우트 세그먼트 에러 경계(레이아웃 내부에서 렌더). 클라이언트 컴포넌트 필수.
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // 관측 도구 연동 전까지는 콘솔로 남긴다.
    console.error(error);
  }, [error]);

  return (
    <div className="relative z-10 mx-auto flex min-h-[60vh] max-w-lg flex-col items-center justify-center px-6 py-16 text-center">
      <p className="text-sm font-semibold tracking-[0.3em] text-red">오류</p>
      <h1 className="mt-3 text-2xl font-bold text-navy">잠시 문제가 발생했어요</h1>
      <p className="mt-3 text-muted">
        데이터를 불러오는 중 오류가 났습니다. 잠시 후 다시 시도해 주세요.
      </p>
      {error.digest ? (
        <p className="mt-2 text-xs text-muted">오류 코드: {error.digest}</p>
      ) : null}
      <button
        type="button"
        onClick={reset}
        className="mt-8 inline-flex items-center rounded-full bg-navy px-5 py-2.5 text-sm font-semibold text-white transition hover:opacity-90"
      >
        다시 시도
      </button>
    </div>
  );
}
