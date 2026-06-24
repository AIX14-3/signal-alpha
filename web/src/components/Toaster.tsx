"use client";

import { useToastStore, type ToastTone } from "@/stores/toastStore";

const TONE: Record<ToastTone, string> = {
  info: "bg-navy text-white",
  error: "bg-red text-white",
  success: "bg-green text-white",
};

/** 전역 토스트. 액션 결과(관심종목 한도 초과 등)를 화면 하단 중앙에 표시. */
export function Toaster() {
  const toasts = useToastStore((state) => state.toasts);
  const dismiss = useToastStore((state) => state.dismiss);

  if (toasts.length === 0) return null;

  return (
    <div
      className="pointer-events-none fixed bottom-6 left-1/2 z-[100] flex -translate-x-1/2 flex-col items-center gap-2"
      role="status"
      aria-live="polite"
    >
      {toasts.map((toast) => (
        <button
          key={toast.id}
          type="button"
          onClick={() => dismiss(toast.id)}
          className={`pointer-events-auto rounded-full px-5 py-3 text-[14px] font-semibold shadow-[var(--shadow-card)] ${TONE[toast.tone]}`}
        >
          {toast.message}
        </button>
      ))}
    </div>
  );
}
