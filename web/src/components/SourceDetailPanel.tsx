"use client";

import { X } from "lucide-react";
import { type CSSProperties, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { SourceDetailBody } from "@/components/SourceDetailBody";
import { SourceIcon } from "@/components/SourceIcon";
import { getSourceDetail, type SourceDetail, type SourceKey } from "@/lib/apiClient";
import { SOURCE_META } from "@/lib/format";

// 소스 상세 우측 슬라이드오버. 열림 여부는 리포트 페이지가 URL 쿼리(?source=dart)로 소유하고,
// 이 컴포넌트는 열린 소스의 상세를 받아 그리는 일만 한다(뒤로가기=닫기, 새로고침 시 유지).

// 퇴장 애니메이션 길이. globals.css 의 panel-out 지속시간과 맞춘다(짧으면 종이 꼬리가 서류철에
// 다 빨려 들어가기 전에 언마운트돼 툭 끊긴다).
const PANEL_EXIT_MS = 380;
// 종이가 서류철 틈에서 뽑혀 나오는 시간. 꼬리가 마지막까지 물려 있어야 해서 퇴장보다 길다.
const PANEL_ENTER_MS = 620;
// 이징은 linear 다. 꼬리를 보이게 하려면 clip-path 가 transform 보다 늦게 펴져야 해서 중간
// 키프레임이 필요한데, 이징 곡선을 주면 그게 구간마다 재적용돼 감속→가속이 되풀이된다(끊김).
// 감속(뽑힘)·가속(빨려듦)은 globals.css 의 키프레임 간격이 만든다.
const PAPER_MOTION = "linear";

// 종이가 출발/도착하는 서류철 입구. 클릭한 카드(`[data-source]`)의 윗변 한가운데다.
// 카드를 못 찾으면(딥링크로 바로 열린 경우) 화면 아래에서 올라오는 폴백을 쓴다.
type Slot = { dx: number; dy: number; scale: number };

function slotOf(source: string, sheet: HTMLElement): Slot | null {
  const card = document.querySelector<HTMLElement>(`[data-source="${source}"]`);
  if (!card) return null;
  // 종이의 제자리는 화면 한가운데다(오버레이가 좌우·상하 대칭 패딩으로 중앙 정렬).
  // 크기는 offsetWidth/Height 로 읽는다 — getBoundingClientRect 는 진입 애니메이션의
  // transform 이 이미 걸린 값을 돌려줘 출발점 계산이 스스로 오염된다.
  const sheetW = sheet.offsetWidth;
  const sheetH = sheet.offsetHeight;
  if (!sheetW || !sheetH) return null;

  const from = card.getBoundingClientRect();
  return {
    // 서류가 솟아 나오는 곳은 표지 위쪽이다 — 카드 윗변에서 살짝 안쪽.
    dx: from.left + from.width / 2 - window.innerWidth / 2,
    dy: from.top + 12 - window.innerHeight / 2,
    scale: Math.min(from.width / sheetW, 0.34),
  };
}

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
  // undefined = 아직 안 잼. 재기 전에는 애니메이션을 걸지 않는다 — 걸어 두면 첫 프레임의
  // transform 이 측정을 오염시키고, 종이가 엉뚱한 데서 출발한다. null = 카드 없음(폴백).
  const [slot, setSlot] = useState<Slot | null | undefined>(undefined);
  const panelRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (panelRef.current) setSlot(slotOf(source, panelRef.current));
  }, [source]);

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
        style={
          {
            // 측정 전 한 프레임은 숨긴다(제자리에 뜬 종이가 번쩍이지 않게).
            opacity: slot === undefined ? 0 : undefined,
            animation:
              slot === undefined
                ? "none"
                : closing
                  ? `panel-out ${PANEL_EXIT_MS}ms ${PAPER_MOTION} forwards`
                  : `panel-in ${PANEL_ENTER_MS}ms ${PAPER_MOTION}`,
            ...(slot
              ? {
                  "--slot-dx": `${Math.round(slot.dx)}px`,
                  "--slot-dy": `${Math.round(slot.dy)}px`,
                  "--slot-scale": slot.scale.toFixed(3),
                }
              : null),
          } as CSSProperties
        }
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
