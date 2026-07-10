"use client";

import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { SourceDetailBody } from "@/components/SourceDetailBody";
import { SourceIcon } from "@/components/SourceIcon";
import { getSourceDetail, type SourceDetail, type SourceKey } from "@/lib/apiClient";
import { SOURCE_META } from "@/lib/format";

// 소스 상세 우측 슬라이드오버. 열림 여부는 리포트 페이지가 URL 쿼리(?source=dart)로 소유하고,
// 이 컴포넌트는 열린 소스의 상세를 받아 그리는 일만 한다(뒤로가기=닫기, 새로고침 시 유지).

// 퇴장 애니메이션 길이. globals.css 의 panel-out 지속시간과 맞춘다(짧으면 종이 꼬리가 서류철에
// 다 빨려 들어가기 전에 언마운트돼 툭 끊긴다).
const PANEL_EXIT_MS = 320;
// 종이가 서류철 틈에서 뽑혀 나오는 시간. 꼬리가 마지막까지 물려 있어야 해서 퇴장보다 길다.
const PANEL_ENTER_MS = 520;
// 뽑힘은 감속(ease-out), 빨려듦은 가속(ease-in) — 물리적으로 반대 방향의 힘이다.
const PULL_OUT = "cubic-bezier(0.16, 0.84, 0.32, 1)";
const SUCK_IN = "cubic-bezier(0.55, 0, 0.85, 0.35)";

export function SourceDetailPanel({
  ticker,
  source,
  onClose,
}: {
  ticker: string;
  source: SourceKey;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<SourceDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // 닫기는 퇴장 애니메이션을 먼저 재생하고 언마운트한다 — 서류가 서류철로 되돌아가는 동안
  // 패널이 즉시 사라지면 동작이 끊겨 보인다. 카드의 서류는 is-open 해제로 함께 되돌아온다.
  const requestClose = useCallback(() => {
    setClosing((already) => {
      if (already) return already;
      window.setTimeout(onClose, PANEL_EXIT_MS);
      return true;
    });
  }, [onClose]);

  useEffect(() => {
    let active = true;
    setState("loading");
    setDetail(null);
    getSourceDetail(ticker, source)
      .then((d) => {
        if (!active) return;
        setDetail(d);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (!active) return;
        setState("error");
        setMessage(err instanceof Error ? err.message : "불러오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, [ticker, source]);

  // Esc 로 닫기 + 열려 있는 동안 배경 스크롤 잠금.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") requestClose();
    }
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [requestClose]);

  // 패널이 열리면 포커스를 옮겨 키보드 사용자가 곧바로 Esc/Tab 으로 다룰 수 있게 한다.
  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  const meta = SOURCE_META[source] ?? { label: source, icon: "📄", hint: "" };

  return (
    <div
      className="fixed inset-0 z-[60] grid place-items-center p-4 sm:p-8"
      data-panel="source-detail"
    >
      <button
        type="button"
        aria-label="닫기"
        data-scrim=""
        onClick={requestClose}
        className="absolute inset-0 h-full w-full cursor-default bg-navy/35 backdrop-blur-[3px]"
        style={{
          animation: closing
            ? `scrim-out ${PANEL_EXIT_MS}ms ease-in forwards`
            : "scrim-in 240ms ease-out",
        }}
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${meta.label} 상세`}
        tabIndex={-1}
        className="doc-sheet relative flex h-[86vh] w-full max-w-[840px] flex-col outline-none"
        style={{
          animation: closing
            ? `panel-out ${PANEL_EXIT_MS}ms ${SUCK_IN} forwards`
            : `panel-in ${PANEL_ENTER_MS}ms ${PULL_OUT}`,
        }}
      >
        <header className="flex shrink-0 items-center gap-3 border-b border-line px-6 pb-4 pt-6">
          <span
            className="grid h-11 w-11 place-items-center rounded-2xl bg-surface-2 text-sky-deep"
            aria-hidden="true"
          >
            <SourceIcon source={source} size={22} />
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-[20px] font-extrabold">{meta.label} 분석 보고서</h2>
            <p className="truncate text-[12.5px] text-muted">
              {detail?.stock.stock_name ?? ticker} · {meta.hint}
            </p>
          </div>
          <button
            type="button"
            onClick={requestClose}
            aria-label="상세 닫기"
            className="ml-auto grid h-9 w-9 shrink-0 place-items-center rounded-full border border-line text-navy-soft transition hover:border-navy hover:text-navy"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>

        <div className="doc-body min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {state === "loading" && <p className="py-10 text-center text-muted">불러오는 중…</p>}
          {state === "error" && <p className="py-10 text-center text-red">{message}</p>}
          {state === "ready" && detail && <SourceDetailBody detail={detail} source={source} />}
        </div>
      </div>
    </div>
  );
}
