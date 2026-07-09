"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { SourceDetailBody } from "@/components/SourceDetailBody";
import { SourceIcon } from "@/components/SourceIcon";
import { getSourceDetail, type SourceDetail, type SourceKey } from "@/lib/apiClient";
import { SOURCE_META } from "@/lib/format";

// 소스 상세 전체 페이지. 리포트에서 "상세 보기"는 우측 슬라이드오버로 열리고(?source=…),
// 이 페이지는 직접 접근·새 탭·딥링크용 폴백으로 남는다. 본문은 SourceDetailBody 를 공유한다.

const VALID: SourceKey[] = ["price", "dart", "hiring", "datalab", "patent", "report"];

export default function SourceDetailPage() {
  const params = useParams<{ ticker: string; source: string }>();
  const ticker = params.ticker;
  const source = params.source as SourceKey;

  const [detail, setDetail] = useState<SourceDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!VALID.includes(source)) {
      setState("error");
      setMessage("알 수 없는 소스입니다.");
      return;
    }
    let active = true;
    setState("loading");
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

  const meta = SOURCE_META[source] ?? { label: source, icon: "📄", hint: "" };

  return (
    <div className="relative py-8" data-page="source-detail">
      <div className="report-aura pointer-events-none fixed inset-0 -z-10" aria-hidden="true" />

      <Link
        href={`/report/${encodeURIComponent(ticker)}`}
        className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-navy-soft hover:text-navy"
      >
        <ArrowLeft size={15} aria-hidden="true" /> 리포트로
      </Link>

      <div className="mt-3 flex items-center gap-3">
        <span className="glass grid h-12 w-12 place-items-center rounded-2xl text-sky-deep" aria-hidden="true">
          <SourceIcon source={source} size={24} />
        </span>
        <h1 className="text-[28px] font-extrabold">{meta.label} 상세</h1>
      </div>

      {state === "loading" && <p className="py-10 text-center text-muted">불러오는 중…</p>}
      {state === "error" && <p className="py-10 text-center text-red">{message}</p>}
      {state === "ready" && detail && (
        <div className="mt-4">
          <SourceDetailBody detail={detail} source={source} />
        </div>
      )}
    </div>
  );
}
