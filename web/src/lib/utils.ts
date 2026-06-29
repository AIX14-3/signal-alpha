import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind 클래스 병합 헬퍼 (#335 대시보드 컴포넌트 이식용). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
