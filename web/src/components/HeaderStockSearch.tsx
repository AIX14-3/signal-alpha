"use client";

import { usePathname, useRouter } from "next/navigation";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { searchStocks, type Stock } from "@/lib/apiClient";
import { useToastStore } from "@/stores/toastStore";

// 상단 헤더 전역 종목 검색. 검색은 "그 종목을 보러 간다"는 명시적 의도이므로 위치와 무관하게
// 종목 리포트(/report/{code})로 이동한다(검색→리포트 흐름, web-frontend-spec 페이지 인벤토리).
// (홈 리스트를 훑다 클릭하는 browse 는 LiveAnalysisSection 인라인 아코디언이 담당 — 의도 분리.)
export function HeaderStockSearch() {
  const router = useRouter();
  const pathname = usePathname();
  const showToast = useToastStore((s) => s.show);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const composing = useRef(false);

  // 이 컴포넌트는 layout 의 AppShell 안에 있어 라우트가 바뀌어도 언마운트되지 않는다.
  // 비우지 않으면 다른 탭으로 이동한 뒤에도 지난 검색어가 남아 "이 화면이 그 검색 결과"처럼 보인다.
  // (같은 화면에 머무는 실패 검색은 오타 수정을 위해 입력을 그대로 유지한다.)
  useEffect(() => {
    setQuery("");
  }, [pathname]);

  async function run() {
    // 한글은 IME 가 자모를 조합해 NFC 로 확정하지만, 일부 입력기는 분해형(NFD)을 넘긴다.
    // 서버 종목명은 NFC 라 정규화하지 않으면 "삼성전자"가 문자열로 일치하지 않는다.
    const clean = query.trim().normalize("NFC");
    if (!clean || busy) return;
    setBusy(true);
    try {
      const data = await searchStocks(clean);
      if (data.items.length === 0) {
        showToast("검색 결과가 없습니다.", "error");
        return;
      }
      const code = pickBest(data.items, clean).stock_code;
      setQuery("");
      router.push(`/report/${code}`);
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  // 한글 IME: 마지막 글자를 확정하는 Enter 는 keydown 단계에서 조합 중(isComposing)이라
  // form submit 으로 이어지지 않는다 → 사용자는 Enter 를 두 번 눌러야 했다.
  // keydown 에서 기본 제출을 막고, 조합이 끝난 뒤 도착하는 keyup 에서 실행해 Enter 한 번으로 검색한다.
  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") e.preventDefault();
  }

  function onKeyUp(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    if (composing.current || e.nativeEvent.isComposing) return;
    void run();
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void run();
      }}
      className="flex w-full items-center gap-2 rounded-full border border-line bg-surface py-1.5 pl-4 pr-1.5 shadow-[var(--shadow-card)] transition focus-within:border-sky focus-within:ring-4 focus-within:ring-sky/15"
    >
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onCompositionStart={() => (composing.current = true)}
        onCompositionEnd={() => (composing.current = false)}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
        placeholder="종목명 또는 코드 검색"
        aria-label="종목 검색"
        className="min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-muted"
      />
      <button
        type="submit"
        disabled={busy}
        className="shrink-0 rounded-full bg-navy px-4 py-1.5 text-[13px] font-semibold text-white transition hover:-translate-y-px disabled:opacity-60"
      >
        {busy ? "…" : "검색"}
      </button>
    </form>
  );
}

/** 검색어와 가장 잘 맞는 종목(코드 정확일치 > 종목명 정확일치 > 첫 결과). */
function pickBest(items: Stock[], term: string): Stock {
  const clean = term.trim().toLowerCase();
  return (
    items.find((s) => s.stock_code.toLowerCase() === clean) ??
    items.find((s) => s.stock_name.toLowerCase() === clean) ??
    items[0]
  );
}
