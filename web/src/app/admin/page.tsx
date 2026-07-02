"use client";

import { useCallback, useEffect, useState } from "react";
import {
  adminCancelSubscription,
  adminDeleteUser,
  adminGetStats,
  adminListScheduleRuns,
  adminListSchedules,
  adminListUsers,
  adminLogin,
  adminLogout,
  adminMe,
  adminRefund,
  adminSetSubscription,
  adminTriggerSchedule,
  adminUpdateSchedule,
  adminUpdateUser,
  type AdminSchedule,
  type AdminScheduleRun,
  type AdminStats,
  type AdminUser,
} from "@/lib/apiClient";
import { won } from "@/lib/format";

export default function AdminPage() {
  // null = 쿠키 세션 확인 중. admin 쿠키는 HttpOnly 라 /admin/me 로 상태를 판별한다.
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    adminMe()
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false));
  }, []);

  if (authed === null) return <p className="py-16 text-center text-muted">관리자 인증 확인 중…</p>;
  if (!authed) return <AdminLogin onSuccess={() => setAuthed(true)} />;
  return (
    <AdminDashboard
      onLogout={async () => {
        try {
          await adminLogout();
        } catch {
          /* 서버 실패와 무관하게 로그인 화면으로 */
        }
        setAuthed(false);
      }}
    />
  );
}

function AdminLogin({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await adminLogin({ email, password }); // 세션은 sa_admin 쿠키로 설정됨
      onSuccess();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto max-w-[360px] py-16">
      <h1 className="text-[26px] font-extrabold">관리자 로그인</h1>
      <div className="mt-6 space-y-3">
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="관리자 이메일" className="card w-full px-4 py-3 text-[15px] outline-none focus:border-sky" />
        <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="비밀번호" className="card w-full px-4 py-3 text-[15px] outline-none focus:border-sky" />
        {error && <p className="text-[13px] text-red">{error}</p>}
        <button type="submit" className="brand-grad w-full rounded-full py-3 text-[15px] font-bold text-white">로그인</button>
      </div>
    </form>
  );
}

function AdminDashboard({ onLogout }: { onLogout: () => Promise<void> }) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 인라인 수정(행 단위)
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editEmail, setEditEmail] = useState("");
  const [editNickname, setEditNickname] = useState("");

  const reload = useCallback(async (query?: string) => {
    try {
      const [statsData, usersData] = await Promise.all([
        adminGetStats(),
        adminListUsers({ page: 1, size: 20, q: query }),
      ]);
      setStats(statsData);
      setUsers(usersData.items);
      setTotal(usersData.total);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function grant(userId: number) {
    await adminSetSubscription(userId, { plan_type: "monthly_9900" });
    await reload(q);
  }
  async function revoke(userId: number) {
    await adminCancelSubscription(userId);
    await reload(q);
  }
  async function refundUser(userId: number) {
    if (!window.confirm("이 회원의 최근 결제를 전액 환불하고 구독을 즉시 해지합니다. 진행할까요?")) return;
    try {
      await adminRefund(userId);
      await reload(q);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function startEdit(user: AdminUser) {
    setEditingId(user.id);
    setEditEmail(user.email ?? "");
    setEditNickname(user.nickname ?? "");
    setError(null);
  }

  async function saveEdit(userId: number) {
    setBusy(true);
    setError(null);
    try {
      await adminUpdateUser(userId, { email: editEmail.trim(), nickname: editNickname.trim() });
      setEditingId(null);
      await reload(q);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteUser(user: AdminUser) {
    if (!window.confirm(`'${user.nickname ?? user.email ?? user.id}' 회원을 삭제할까요? 되돌릴 수 없습니다.`)) return;
    setBusy(true);
    setError(null);
    try {
      await adminDeleteUser(user.id);
      await reload(q);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="py-12">
      <div className="flex items-center justify-between">
        <h1 className="text-[32px] font-extrabold">관리자 대시보드</h1>
        <button type="button" onClick={() => void onLogout()} className="rounded-full border border-line px-4 py-2 text-[13.5px] font-semibold text-navy-soft hover:border-navy">
          로그아웃
        </button>
      </div>

      {error && <p className="mt-4 text-[14px] text-red">{error}</p>}

      {stats && (
        <div className="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard label="MRR" value={won(stats.mrr)} />
          <StatCard label="총 회원" value={String(stats.total_users)} />
          <StatCard label="활성 구독" value={String(stats.active_subscriptions)} />
          <StatCard label="월 구독자" value={String(stats.by_plan.monthly_9900 ?? 0)} />
        </div>
      )}

      <ScheduleCard onError={setError} />

      <div className="mt-10 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[18px] font-bold">회원 ({total})</h2>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void reload(q)}
          placeholder="검색 후 Enter"
          className="card px-4 py-2 text-[13.5px] outline-none focus:border-sky"
        />
      </div>
      <div className="card mt-3 overflow-hidden">
        <table className="w-full text-left text-[14px]">
          <thead className="bg-surface-2 text-[12.5px] text-muted">
            <tr>
              <th className="px-5 py-3 font-semibold">회원번호</th>
              <th className="px-5 py-3 font-semibold">닉네임</th>
              <th className="px-5 py-3 font-semibold">이메일</th>
              <th className="px-5 py-3 font-semibold">구독</th>
              <th className="px-5 py-3 font-semibold">가입일</th>
              <th className="px-5 py-3 font-semibold">관리</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const active = user.subscription?.status === "active";
              const editing = editingId === user.id;
              return (
                <tr key={user.id} className="border-t border-line">
                  <td className="px-5 py-3 font-semibold">{user.member_code ?? user.id}</td>
                  <td className="px-5 py-3">
                    {editing ? (
                      <input value={editNickname} onChange={(e) => setEditNickname(e.target.value)} placeholder="닉네임" className="card w-[120px] px-2 py-1 text-[13px] outline-none focus:border-sky" />
                    ) : (
                      user.nickname ?? "-"
                    )}
                  </td>
                  <td className="px-5 py-3 text-muted">
                    {editing ? (
                      <input type="email" value={editEmail} onChange={(e) => setEditEmail(e.target.value)} placeholder="이메일" className="card w-[200px] px-2 py-1 text-[13px] outline-none focus:border-sky" />
                    ) : (
                      user.email ?? "-"
                    )}
                  </td>
                  <td className="px-5 py-3">{active ? "월 구독" : "무료"}</td>
                  <td className="px-5 py-3 text-muted">{user.created_at ? user.created_at.slice(0, 10) : "-"}</td>
                  <td className="px-5 py-3">
                    {editing ? (
                      <div className="flex gap-3">
                        <button type="button" onClick={() => void saveEdit(user.id)} disabled={busy} className="text-[13px] font-semibold text-sky-deep disabled:opacity-60">저장</button>
                        <button type="button" onClick={() => setEditingId(null)} className="text-[13px] font-semibold text-muted">취소</button>
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-3">
                        {active ? (
                          <>
                            <button type="button" onClick={() => void revoke(user.id)} className="text-[13px] font-semibold text-muted hover:text-red">구독 취소</button>
                            <button type="button" onClick={() => void refundUser(user.id)} className="text-[13px] font-semibold text-muted hover:text-red">환불</button>
                          </>
                        ) : (
                          <button type="button" onClick={() => void grant(user.id)} className="text-[13px] font-semibold text-sky-deep">구독 부여</button>
                        )}
                        <button type="button" onClick={() => startEdit(user)} className="text-[13px] font-semibold text-navy-soft hover:text-navy">수정</button>
                        <button type="button" onClick={() => void deleteUser(user)} className="text-[13px] font-semibold text-muted hover:text-red">삭제</button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-5">
      <div className="text-[12.5px] font-semibold text-muted">{label}</div>
      <div className="mt-1.5 text-[24px] font-extrabold">{value}</div>
    </div>
  );
}

const ALL_TARGETS = ["price", "dart", "report", "alternative"] as const;
const TARGET_LABEL: Record<string, string> = {
  price: '\uac00\uaca9',
  dart: 'DART',
  report: '\ub9ac\ud3ec\ud2b8',
  alternative: '\ub300\uccb4 \ub370\uc774\ud130',
};

type ScheduleDraft = {
  runAt: string;
  targets: string[];
  frequencyMinutes: string;
  activeFrom: string;
  activeUntil: string;
};

const DEFAULT_SCHEDULE_DRAFT: ScheduleDraft = {
  runAt: '04:30',
  targets: [],
  frequencyMinutes: '1440',
  activeFrom: '04:30',
  activeUntil: '04:30',
};

function draftFromSchedule(schedule: AdminSchedule): ScheduleDraft {
  const activeFrom = schedule.active_from_local ?? schedule.run_at_local ?? '04:30';
  return {
    runAt: schedule.run_at_local ?? '04:30',
    targets: schedule.targets ?? [],
    frequencyMinutes: String(schedule.frequency_minutes ?? 1440),
    activeFrom,
    activeUntil: schedule.active_until_local ?? activeFrom,
  };
}

function fmtDateTime(value: string | null): string {
  if (!value) return '-';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringifyRunValue(value: unknown): string {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (Array.isArray(value)) return `${value.length} items`;
  const record = asRecord(value);
  if (!record) return '-';
  const entries = Object.entries(record);
  if (entries.length === 0) return 'empty';
  return entries
    .slice(0, 3)
    .map(([key, item]) => `${key}=${stringifyRunValue(item)}`)
    .join(', ');
}

const SCHEDULE_DECISION_ACTION_LABEL: Record<string, string> = {
  fire: '실행',
  skip: '보류',
};

const SCHEDULE_DECISION_REASON_LABEL: Record<string, string> = {
  manual: '수동 실행',
  scheduled: '정기 실행',
  'queue-backlog': '큐 적체',
  'recent-failures': '실패 누적',
  'outside-window': '운영 시간 외',
  'not-due': '대기',
  disabled: '비활성',
};

function formatScheduleRunDecision(run: AdminScheduleRun): string {
  const detail = asRecord(run.detail);
  const decision = asRecord(detail?.decision);
  const action = typeof decision?.action === 'string' ? decision.action : null;
  const reason = typeof decision?.reason === 'string' ? decision.reason : null;
  const reasonValue = reason ?? run.trigger_reason;
  const reasonLabel = reasonValue
    ? (SCHEDULE_DECISION_REASON_LABEL[reasonValue] ?? reasonValue)
    : '-';
  if (action) return `${SCHEDULE_DECISION_ACTION_LABEL[action] ?? action}: ${reasonLabel}`;
  return reasonLabel;
}

function formatScheduleRunTargetResult(run: AdminScheduleRun): string {
  const detail = asRecord(run.detail);
  const targetSummary = asRecord(detail?.targets);
  if (targetSummary) {
    const entries = Object.entries(targetSummary);
    if (entries.length === 0) return 'empty';
    return entries
      .map(([target, value]) => `${target}: ${stringifyRunValue(value)}`)
      .join(' | ');
  }
  return run.targets.join(', ') || '-';
}

const SCHEDULE_HEALTH_TONE: Record<string, string> = {
  ok: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  disabled: 'border-line bg-surface-2 text-muted',
  delayed: 'border-red/30 bg-red/10 text-red',
  failed_waiting: 'border-amber-200 bg-amber-50 text-amber-700',
  unknown: 'border-sky/30 bg-sky/10 text-sky-deep',
};

function formatScheduleHealth(schedule: AdminSchedule): {
  label: string;
  detail: string;
  className: string;
} {
  const status = schedule.health_status ?? (schedule.enabled ? 'unknown' : 'disabled');
  return {
    label: schedule.health_label ?? status,
    detail: schedule.health_detail ?? '스케줄러 상태 확인이 필요합니다.',
    className: SCHEDULE_HEALTH_TONE[status] ?? SCHEDULE_HEALTH_TONE.unknown,
  };
}

function scheduleRunDecisionReason(run: AdminScheduleRun): string | null {
  const detail = asRecord(run.detail);
  const decision = asRecord(detail?.decision);
  if (typeof decision?.reason === 'string') return decision.reason;
  return run.trigger_reason;
}

function getScheduleRunWarning(runs: AdminScheduleRun[]): string | null {
  let consecutive = 0;
  for (const run of runs) {
    const status = (run.status ?? '').toLowerCase();
    const reason = scheduleRunDecisionReason(run);
    const policySkip = status === 'skipped' && (
      reason === 'queue-backlog' || reason === 'recent-failures'
    );
    const failedRun = status === 'failed' || status === 'partial';
    if (!policySkip && !failedRun) break;
    consecutive += 1;
  }
  if (consecutive < 2) return null;
  return `반복 보류/실패 ${consecutive}회가 이어졌습니다. 큐 상태와 최근 실행 결과를 추가 확인하세요.`;
}

function validateScheduleDraft(draft: ScheduleDraft): string | null {
  if (draft.targets.length === 0) {
    return '수집 대상을 최소 1개 선택하세요.';
  }
  const frequencyMinutes = Number(draft.frequencyMinutes);
  if (!Number.isInteger(frequencyMinutes) || frequencyMinutes < 1 || frequencyMinutes > 1440) {
    return '반복 주기는 1~1440분 사이의 정수로 입력하세요.';
  }
  if (frequencyMinutes < 1440 && draft.activeFrom === draft.activeUntil) {
    return '반복 스케줄의 활성 시작/종료 시각은 같을 수 없습니다.';
  }
  return null;
}

function ScheduleCard({ onError }: { onError: (msg: string | null) => void }) {
  const [schedules, setSchedules] = useState<AdminSchedule[]>([]);
  const [drafts, setDrafts] = useState<Record<number, ScheduleDraft>>({});
  const [runsBySchedule, setRunsBySchedule] = useState<Record<number, AdminScheduleRun[]>>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const { items } = await adminListSchedules();
      setSchedules(items);
      setDrafts(
        Object.fromEntries(
          items.map((schedule) => [
            schedule.id,
            draftFromSchedule(schedule),
          ]),
        ),
      );
      const runEntries = await Promise.all(
        items.map(async (schedule) => {
          const { items: runs } = await adminListScheduleRuns(schedule.id, 5);
          return [schedule.id, runs] as const;
        }),
      );
      setRunsBySchedule(Object.fromEntries(runEntries));
    } catch (err) {
      onError((err as Error).message);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  if (schedules.length === 0) return null;

  async function refreshRuns(scheduleId: number) {
    const { items } = await adminListScheduleRuns(scheduleId, 5);
    setRunsBySchedule((prev) => ({ ...prev, [scheduleId]: items }));
  }

  async function save(schedule: AdminSchedule, body: Parameters<typeof adminUpdateSchedule>[1]) {
    setBusy(true);
    onError(null);
    try {
      const updated = await adminUpdateSchedule(schedule.id, body);
      setSchedules((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setDrafts((prev) => ({
        ...prev,
        [updated.id]: draftFromSchedule(updated),
      }));
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function saveTargets(schedule: AdminSchedule, draft: ScheduleDraft) {
    const validation = validateScheduleDraft(draft);
    if (validation) {
      onError(validation);
      return;
    }
    await save(schedule, { targets: draft.targets });
  }

  async function saveCadence(schedule: AdminSchedule, draft: ScheduleDraft) {
    const validation = validateScheduleDraft(draft);
    if (validation) {
      onError(validation);
      return;
    }
    await save(schedule, {
      frequency_minutes: Number(draft.frequencyMinutes),
      active_from_local: draft.activeFrom,
      active_until_local: draft.activeUntil,
    });
  }

  async function trigger(schedule: AdminSchedule) {
    if (!window.confirm(`${schedule.name ?? schedule.id} \uc2a4\ucf00\uc904\uc744 \ub2e4\uc74c \ud3f4\ub9c1\uc5d0\uc11c \uc2e4\ud589\ud558\ub3c4\ub85d \uc694\uccad\ud560\uae4c\uc694?`)) return;
    setBusy(true);
    onError(null);
    try {
      const updated = await adminTriggerSchedule(schedule.id);
      setSchedules((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      await refreshRuns(schedule.id);
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function setRunAt(scheduleId: number, runAt: string) {
    setDrafts((prev) => {
      const schedule = schedules.find((item) => item.id === scheduleId);
      const current = prev[scheduleId] ?? (schedule ? draftFromSchedule(schedule) : DEFAULT_SCHEDULE_DRAFT);
      return { ...prev, [scheduleId]: { ...current, runAt } };
    });
  }

  function setDraftField(schedule: AdminSchedule, key: keyof ScheduleDraft, value: string) {
    setDrafts((prev) => ({
      ...prev,
      [schedule.id]: {
        ...(prev[schedule.id] ?? draftFromSchedule(schedule)),
        [key]: value,
      },
    }));
  }

  function toggleTarget(scheduleId: number, key: string) {
    setDrafts((prev) => {
      const schedule = schedules.find((item) => item.id === scheduleId);
      const current = prev[scheduleId] ?? (schedule ? draftFromSchedule(schedule) : DEFAULT_SCHEDULE_DRAFT);
      const nextTargets = current.targets.includes(key)
        ? current.targets.filter((target) => target !== key)
        : [...current.targets, key];
      return { ...prev, [scheduleId]: { ...current, targets: nextTargets } };
    });
  }

  return (
    <section className="card mt-8 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">{'\uc218\uc9d1 \uc2a4\ucf00\uc904'}</h2>
          <p className="mt-0.5 text-[12.5px] text-muted">{'\uc18c\uc2a4\ubcc4 \uc218\uc9d1 \uc8fc\uae30\uc640 \ucd5c\uadfc \uc2e4\ud589 \uc774\ub825\uc744 \uad00\ub9ac\ud569\ub2c8\ub2e4.'}</p>
        </div>
      </div>

      <div className="mt-5 space-y-5">
        {schedules.map((schedule) => {
          const draft = drafts[schedule.id] ?? {
            runAt: schedule.run_at_local ?? '04:30',
            targets: schedule.targets ?? [],
            frequencyMinutes: String(schedule.frequency_minutes ?? 1440),
            activeFrom: schedule.active_from_local ?? schedule.run_at_local ?? '04:30',
            activeUntil: schedule.active_until_local ?? schedule.active_from_local ?? schedule.run_at_local ?? '04:30',
          };
          const frequencyMinutes = Number(draft.frequencyMinutes);
          const runs = runsBySchedule[schedule.id] ?? [];
          const health = formatScheduleHealth(schedule);
          const runWarning = getScheduleRunWarning(runs);
          const draftValidation = validateScheduleDraft(draft);
          return (
            <div key={schedule.id} className="rounded-xl border border-line p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-[15px] font-bold">{schedule.name ?? `schedule-${schedule.id}`}</h3>
                    <span className={`rounded-full border px-2.5 py-1 text-[12px] font-semibold ${health.className}`}>
                      {health.label}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[12.5px] text-muted">
                    {schedule.timezone ?? 'Asia/Seoul'} {schedule.run_at_local ?? '-'} {' - '}
                    {schedule.frequency_minutes ?? 1440}{'\ubd84 \uc8fc\uae30'} {' - '}
                    {schedule.enabled ? '\ud65c\uc131' : '\ube44\ud65c\uc131'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void save(schedule, { enabled: !schedule.enabled })}
                  disabled={busy}
                  className={`rounded-full px-4 py-2 text-[13.5px] font-semibold disabled:opacity-60 ${
                    schedule.enabled
                      ? 'border border-line text-navy-soft hover:border-navy'
                      : 'brand-grad text-white'
                  }`}
                >
                  {schedule.enabled ? '\ube44\ud65c\uc131\ud654' : '\ud65c\uc131\ud654'}
                </button>
              </div>

              <div className="mt-4 grid gap-5 lg:grid-cols-2">
                <div>
                  <label className="text-[12.5px] font-semibold text-muted">{'\uc2e4\ud589 \uc2dc\uac01 (KST)'}</label>
                  <div className="mt-2 flex items-center gap-2">
                    <input
                      type="time"
                      value={draft.runAt}
                      onChange={(e) => setRunAt(schedule.id, e.target.value)}
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <button
                      type="button"
                      onClick={() => void save(schedule, { run_at_local: draft.runAt })}
                      disabled={busy || draft.runAt === schedule.run_at_local}
                      className="text-[13px] font-semibold text-sky-deep disabled:opacity-50"
                    >
                      {'\uc2dc\uac01 \uc800\uc7a5'}
                    </button>
                  </div>

                  <label className="mt-4 block text-[12.5px] font-semibold text-muted">{'\uc218\uc9d1 \ub300\uc0c1'}</label>
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    {ALL_TARGETS.map((key) => (
                      <label key={key} className="flex items-center gap-1.5 text-[13.5px]">
                        <input
                          type="checkbox"
                          checked={draft.targets.includes(key)}
                          onChange={() => toggleTarget(schedule.id, key)}
                        />
                        {TARGET_LABEL[key] ?? key}
                      </label>
                    ))}
                    <button
                      type="button"
                      onClick={() => void saveTargets(schedule, draft)}
                      disabled={busy}
                      className="text-[13px] font-semibold text-sky-deep disabled:opacity-50"
                    >
                      {'\ub300\uc0c1 \uc800\uc7a5'}
                    </button>
                  </div>

                  <label className="mt-4 block text-[12.5px] font-semibold text-muted">{'\ubc18\ubcf5 \uc8fc\uae30 / \ud65c\uc131 \uc2dc\uac04'}</label>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    <input
                      type="number"
                      min={1}
                      max={1440}
                      value={draft.frequencyMinutes}
                      onChange={(e) => setDraftField(schedule, 'frequencyMinutes', e.target.value)}
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="time"
                      value={draft.activeFrom}
                      onChange={(e) => setDraftField(schedule, 'activeFrom', e.target.value)}
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                    <input
                      type="time"
                      value={draft.activeUntil}
                      onChange={(e) => setDraftField(schedule, 'activeUntil', e.target.value)}
                      className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
                    />
                  </div>
                  {draftValidation && (
                    <p className="mt-2 text-[12.5px] font-semibold text-red">{draftValidation}</p>
                  )}
                  <button
                    type="button"
                    onClick={() => void saveCadence(schedule, draft)}
                    disabled={busy || !Number.isInteger(frequencyMinutes) || frequencyMinutes < 1 || frequencyMinutes > 1440}
                    className="mt-2 text-[13px] font-semibold text-sky-deep disabled:opacity-50"
                  >
                    {'\uc8fc\uae30 \uc800\uc7a5'}
                  </button>
                </div>

                <div className="rounded-xl bg-surface-2 p-4">
                  <div className="text-[12.5px] font-semibold text-muted">{'\uc2e4\ud589 \uc0c1\ud0dc'}</div>
                  <dl className="mt-2 space-y-1.5 text-[13px]">
                    <div className="flex justify-between gap-4">
                      <dt className="text-muted">{'\uc0c1\ud0dc \uc9c4\ub2e8'}</dt>
                      <dd className="text-right font-medium">{health.detail}</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-muted">{'\ub9c8\uc9c0\ub9c9 \uc2e4\ud589'}</dt>
                      <dd className="font-medium">{fmtDateTime(schedule.last_run_at)}</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-muted">{'\uacb0\uacfc'}</dt>
                      <dd className="font-medium">{schedule.last_status ?? '-'}</dd>
                    </div>
                    <div className="flex justify-between gap-4">
                      <dt className="text-muted">{'\ub2e4\uc74c \uc608\uc815'}</dt>
                      <dd className="font-medium">{fmtDateTime(schedule.next_run_at)}</dd>
                    </div>
                  </dl>
                  <button
                    type="button"
                    onClick={() => void trigger(schedule)}
                    disabled={busy}
                    className="brand-grad mt-4 w-full rounded-full py-2.5 text-[14px] font-bold text-white disabled:opacity-60"
                  >
                    {'\uc9c0\uae08 \uc2e4\ud589'}
                  </button>
                </div>
              </div>

              <div className="mt-4">
                <div className="text-[12.5px] font-semibold text-muted">{'\ucd5c\uadfc \uc2e4\ud589 \uc774\ub825'}</div>
                {runWarning && (
                  <p className="mt-1.5 rounded-md border border-red/30 bg-red/10 px-3 py-2 text-[12.5px] font-semibold text-red">
                    {runWarning}
                  </p>
                )}
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-left text-[12.5px]">
                    <thead className="text-muted">
                      <tr>
                        <th className="py-1.5 pr-3 font-semibold">{'\uc2dc\uc791'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\ud2b8\ub9ac\uac70'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\ub300\uc0c1'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\uc0c1\ud0dc'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\ud310\ub2e8'}</th>
                        <th className="py-1.5 pr-3 font-semibold">{'\uacb0\uacfc \uc694\uc57d'}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.length === 0 ? (
                        <tr>
                          <td className="py-2 text-muted" colSpan={6}>
                            {'\uc2e4\ud589 \uc774\ub825\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.'}
                          </td>
                        </tr>
                      ) : (
                        runs.map((run) => (
                          <tr key={run.id} className="border-t border-line">
                            <td className="py-1.5 pr-3">{fmtDateTime(run.started_at)}</td>
                            <td className="py-1.5 pr-3">{run.trigger_reason ?? '-'}</td>
                            <td className="py-1.5 pr-3">{run.targets.join(', ') || '-'}</td>
                            <td className="py-1.5 pr-3 font-medium">{run.status ?? '-'}</td>
                            <td className="py-1.5 pr-3">{formatScheduleRunDecision(run)}</td>
                            <td className="max-w-[260px] py-1.5 pr-3 text-muted">{formatScheduleRunTargetResult(run)}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
