"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";
import { useGuardStore } from "@/stores/guardStore";

// whole_site 차단에서도 항상 열어두는 경로 — 운영·결제·로그인 흐름 보호(관리자 잠김 방지).
const WHOLE_SITE_ALLOWLIST = ["/login", "/signup", "/pricing", "/admin", "/auth/callback"];

function isAllowlisted(pathname: string): boolean {
  return WHOLE_SITE_ALLOWLIST.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function GuardGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const status = useGuardStore((state) => state.status);
  const scope = useGuardStore((state) => state.scope);
  const reason = useGuardStore((state) => state.reason);
  const resumeAt = useGuardStore((state) => state.resumeAt);
  const startPolling = useGuardStore((state) => state.startPolling);
  const stopPolling = useGuardStore((state) => state.stopPolling);

  useEffect(() => {
    startPolling();
    return () => stopPolling();
  }, [startPolling, stopPolling]);

  if (status !== "blocked") return <>{children}</>;

  const blockedScreen =
    (scope === "whole_site" && !isAllowlisted(pathname)) ||
    (scope === "report_view" && pathname.startsWith("/report"));

  if (blockedScreen) {
    return <GuardBlockedScreen reason={reason} resumeAt={resumeAt} />;
  }

  // 차단 화면으로 대체되지 않는 페이지(허용목록·홈 등)에는 안내 배너만 얹는다.
  return (
    <>
      <GuardBanner scope={scope} />
      {children}
    </>
  );
}

function GuardBanner({ scope }: { scope: string }) {
  const message =
    scope === "report_generation"
      ? "현재 신규 분석을 일시 중단했습니다. 기존 리포트는 참고용으로만 확인해 주세요."
      : "지정학 리스크로 리포트 서비스를 일시 중단했습니다.";
  return (
    <div className="relative z-10 border-b border-line bg-surface-2 px-6 py-2.5 text-center text-[13.5px] font-medium text-navy-soft">
      ⓘ {message}
    </div>
  );
}

function GuardBlockedScreen({
  reason,
  resumeAt,
}: {
  reason: string | null;
  resumeAt: string | null;
}) {
  return (
    <div className="mx-auto flex max-w-[560px] flex-col items-center py-24 text-center">
      <div className="text-[40px]">⛔</div>
      <h1 className="mt-4 text-[24px] font-extrabold">서비스 일시 중단 안내</h1>
      <p className="mt-4 text-[15px] leading-relaxed text-navy-soft">
        현재 지정학 리스크(전쟁·분쟁 등)로 시장 변동성이 매우 큰 상황입니다.
        <br />
        잘못된 투자 신호를 막기 위해 리포트 서비스를 일시 중단합니다.
      </p>
      <div className="card mt-6 w-full p-5 text-left text-[14px]">
        <div>
          <span className="font-semibold text-muted">사유</span>{" "}
          {reason ?? "지정학 리스크 모니터링 중"}
        </div>
        <div className="mt-1.5">
          <span className="font-semibold text-muted">재개 예정</span>{" "}
          {resumeAt ? resumeAt.slice(0, 16).replace("T", " ") : "상황 안정 시 (미정)"}
        </div>
      </div>
      <div className="mt-8 flex gap-3">
        <Link
          href="/pricing"
          className="rounded-full border border-line px-5 py-[10px] text-[14px] font-semibold text-navy-soft hover:border-navy hover:text-navy"
        >
          요금제 보기
        </Link>
        <Link
          href="/login"
          className="brand-grad rounded-full px-5 py-[10px] text-[14px] font-bold text-white hover:opacity-90"
        >
          로그인
        </Link>
      </div>
    </div>
  );
}
