"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  adminDecideGuardRecommendation,
  adminGetGuardStatus,
  adminListGuardRecommendations,
  adminUpdateGuardStatus,
  type GuardAdminStatus,
  type GuardAudit,
  type GuardMode,
  type GuardRecommendation,
  type GuardScope,
} from "@/lib/apiClient";
import { fmtDateTime } from "../_lib/datetime";

const GUARD_SCOPE_LABEL: Record<GuardScope, string> = {
  report_generation: "신규 분석 노출 중단 (약)",
  report_view: "리포트 조회 차단 (중)",
  whole_site: "전체 페이지 차단 (강)",
};

const GUARD_MODE_LABEL: Record<GuardMode, string> = {
  manual: "manual (관리자만)",
  advisory: "advisory (제안→승인)",
  auto: "auto (자동)",
};

// 지정학 리스크 Kill-Switch 통제 카드 — guard_site_status 토글 + 에이전트 차단 제안 승인/거절.
export function GuardControlCard({ onError }: { onError: (msg: string | null) => void }) {
  const [status, setStatus] = useState<GuardAdminStatus | null>(null);
  const [audit, setAudit] = useState<GuardAudit[]>([]);
  const [pending, setPending] = useState<GuardRecommendation[]>([]);
  const [busy, setBusy] = useState(false);
  // 차단 폼 draft
  const [scope, setScope] = useState<GuardScope>("report_generation");
  const [mode, setMode] = useState<GuardMode>("advisory");
  const [reason, setReason] = useState("");
  const [resumeAt, setResumeAt] = useState("");
  // draft 는 최초 1회만 서버 상태로 시드한다. apply()/decide() 후 reload 가 draft 를
  // 덮어쓰면, 관리자가 입력 중이던 scope/reason 이 사라져(예: stale 제안 거절 →
  // 이어지는 "차단 적용" 이 잘못된 값으로 발동) 안전 스위치가 오작동한다.
  const draftSeeded = useRef(false);

  const load = useCallback(async () => {
    try {
      const [statusData, recoData] = await Promise.all([
        adminGetGuardStatus(),
        adminListGuardRecommendations("pending"),
      ]);
      setStatus(statusData.status);
      setAudit(statusData.audit);
      setPending(recoData.items);
      if (!draftSeeded.current) {
        setScope(statusData.status.scope);
        setMode(statusData.status.mode);
        setReason(statusData.status.reason ?? "");
        draftSeeded.current = true;
      }
    } catch (err) {
      onError((err as Error).message);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function apply(nextStatus: "ok" | "blocked") {
    const label = nextStatus === "blocked" ? `차단(${GUARD_SCOPE_LABEL[scope]})을 적용` : "차단을 해제";
    if (!window.confirm(`${label}합니다. 진행할까요?`)) return;
    setBusy(true);
    onError(null);
    try {
      await adminUpdateGuardStatus({
        status: nextStatus,
        scope,
        mode,
        reason: reason.trim() || null,
        resume_at: resumeAt ? new Date(resumeAt).toISOString() : null,
      });
      await load();
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function decide(recommendation: GuardRecommendation, decision: "approve" | "reject") {
    if (
      decision === "approve" &&
      !window.confirm(
        `제안을 승인하면 즉시 '${GUARD_SCOPE_LABEL[recommendation.suggested_scope]}' 차단이 적용됩니다. 진행할까요?`,
      )
    )
      return;
    setBusy(true);
    onError(null);
    try {
      await adminDecideGuardRecommendation(recommendation.id, decision);
      await load();
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const blocked = status?.status === "blocked";

  return (
    <div className="card mt-10 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[18px] font-bold">지정학 리스크 Kill-Switch</h2>
        {status && (
          <span
            className={`rounded-full px-3 py-1 text-[12.5px] font-bold ${
              blocked ? "bg-red/10 text-red" : "bg-surface-2 text-navy-soft"
            }`}
          >
            {blocked ? `차단 중 · ${GUARD_SCOPE_LABEL[status.scope]}` : "정상 노출"}
          </span>
        )}
      </div>
      {status && blocked && (
        <p className="mt-2 text-[13.5px] text-muted">
          사유: {status.reason ?? "-"} · 실행: {status.triggered_by ?? "-"} · 갱신:{" "}
          {fmtDateTime(status.updated_at)}
        </p>
      )}

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <label className="text-[13px] font-semibold text-muted">
          차단 범위
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as GuardScope)}
            className="card mt-1 w-full px-3 py-2 text-[13.5px] outline-none focus:border-sky"
          >
            {(Object.keys(GUARD_SCOPE_LABEL) as GuardScope[]).map((key) => (
              <option key={key} value={key}>
                {GUARD_SCOPE_LABEL[key]}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[13px] font-semibold text-muted">
          발동 방식
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as GuardMode)}
            className="card mt-1 w-full px-3 py-2 text-[13.5px] outline-none focus:border-sky"
          >
            {(Object.keys(GUARD_MODE_LABEL) as GuardMode[]).map((key) => (
              <option key={key} value={key}>
                {GUARD_MODE_LABEL[key]}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[13px] font-semibold text-muted">
          사유(사용자에게 노출)
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="예: 이란-미국 분쟁 속보로 리포트 신뢰도 확보 시까지 일시중단"
            className="card mt-1 w-full px-3 py-2 text-[13.5px] outline-none focus:border-sky"
          />
        </label>
        <label className="text-[13px] font-semibold text-muted">
          재개 예정(선택)
          <input
            type="datetime-local"
            value={resumeAt}
            onChange={(e) => setResumeAt(e.target.value)}
            className="card mt-1 w-full px-3 py-2 text-[13.5px] outline-none focus:border-sky"
          />
        </label>
      </div>
      <div className="mt-4 flex gap-3">
        <button
          type="button"
          onClick={() => void apply("blocked")}
          disabled={busy}
          className="rounded-full bg-red px-5 py-[9px] text-[13.5px] font-bold text-white hover:opacity-90 disabled:opacity-60"
        >
          차단 적용
        </button>
        <button
          type="button"
          onClick={() => void apply("ok")}
          disabled={busy || !blocked}
          className="rounded-full border border-line px-5 py-[9px] text-[13.5px] font-semibold text-navy-soft hover:border-navy hover:text-navy disabled:opacity-60"
        >
          차단 해제
        </button>
      </div>

      {pending.length > 0 && (
        <div className="mt-6">
          <h3 className="text-[15px] font-bold">차단 제안 대기 ({pending.length})</h3>
          <div className="mt-2 space-y-2">
            {pending.map((item) => (
              <div key={item.id} className="card flex flex-wrap items-center justify-between gap-3 p-4">
                <div className="text-[13.5px]">
                  <div className="font-semibold">
                    {GUARD_SCOPE_LABEL[item.suggested_scope]} · 위험도 {item.severity ?? "-"}
                  </div>
                  <div className="mt-0.5 text-muted">{item.reason ?? "-"}</div>
                  {item.news_url && (
                    <a
                      href={item.news_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[12.5px] text-sky-deep hover:underline"
                    >
                      근거 기사 {item.news_title ? `· ${item.news_title}` : ""}
                    </a>
                  )}
                </div>
                <div className="flex gap-3">
                  <button
                    type="button"
                    onClick={() => void decide(item, "approve")}
                    disabled={busy}
                    className="text-[13px] font-bold text-red disabled:opacity-60"
                  >
                    승인(차단)
                  </button>
                  <button
                    type="button"
                    onClick={() => void decide(item, "reject")}
                    disabled={busy}
                    className="text-[13px] font-semibold text-muted hover:text-navy disabled:opacity-60"
                  >
                    거절
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {audit.length > 0 && (
        <div className="mt-6">
          <h3 className="text-[15px] font-bold">최근 변경 이력</h3>
          <table className="mt-2 w-full text-left text-[13px]">
            <thead className="text-[12px] text-muted">
              <tr>
                <th className="py-1.5 pr-4 font-semibold">시각</th>
                <th className="py-1.5 pr-4 font-semibold">동작</th>
                <th className="py-1.5 pr-4 font-semibold">범위</th>
                <th className="py-1.5 pr-4 font-semibold">실행</th>
                <th className="py-1.5 font-semibold">사유</th>
              </tr>
            </thead>
            <tbody>
              {audit.slice(0, 8).map((item, index) => (
                <tr key={index} className="border-t border-line">
                  <td className="py-1.5 pr-4 text-muted">{fmtDateTime(item.created_at)}</td>
                  <td className="py-1.5 pr-4 font-semibold">{item.action}</td>
                  <td className="py-1.5 pr-4">{item.scope ?? "-"}</td>
                  <td className="py-1.5 pr-4">{item.actor ?? "-"}</td>
                  <td className="py-1.5 text-muted">{item.reason ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
