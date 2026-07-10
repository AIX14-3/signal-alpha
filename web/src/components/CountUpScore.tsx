"use client";

import { useEffect, useState } from "react";
import { scoreText } from "@/lib/format";

// 계기판처럼 0 에서 실제 점수까지 굴러 올라간다. 표시 형식은 scoreText 한 곳만 쓴다
// (소수 한 자리, 정수는 소수점 없음, 없으면 "–") — 애니메이션 중에도 최종값과 같은 규칙이다.
const DURATION_MS = 1100;

/** 끝에서 느려지는 3차 감속. 계기판 바늘이 목표에 붙는 느낌. */
function easeOut(t: number): number {
  return 1 - (1 - t) ** 3;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true
  );
}

export function CountUpScore({ value, className }: { value: number | null | undefined; className?: string }) {
  const settled = value == null || !Number.isFinite(value) ? null : value;
  const [display, setDisplay] = useState<number | null>(settled);

  useEffect(() => {
    if (settled == null || prefersReducedMotion()) {
      setDisplay(settled);
      return;
    }
    let frame = 0;
    let start: number | null = null;
    const tick = (now: number) => {
      start ??= now;
      const t = Math.min(1, (now - start) / DURATION_MS);
      setDisplay(settled * easeOut(t));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [settled]);

  return (
    // tabular-nums: 자릿수마다 글자 폭이 달라지면 숫자가 좌우로 흔들려 계기판이 아니라 덜컹인다.
    <span className={className} style={{ fontVariantNumeric: "tabular-nums" }}>
      {scoreText(display)}
    </span>
  );
}
