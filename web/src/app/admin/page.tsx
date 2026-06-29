"use client";

import { useCallback, useEffect, useState } from "react";
import {
  adminCancelSubscription,
  adminDeleteUser,
  adminGetStats,
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

const ALL_TARGETS = ["price", "dart"] as const;
const TARGET_LABEL: Record<string, string> = { price: "주식(가격)", dart: "DART 공시" };

function fmtDateTime(value: string | null): string {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
}

function ScheduleCard({ onError }: { onError: (msg: string | null) => void }) {
  const [schedule, setSchedule] = useState<AdminSchedule | null>(null);
  const [runAt, setRunAt] = useState("04:30");
  const [targets, setTargets] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const { items } = await adminListSchedules();
      const first = items[0] ?? null;
      setSchedule(first);
      if (first) {
        setRunAt(first.run_at_local ?? "04:30");
        setTargets(first.targets ?? []);
      }
    } catch (err) {
      onError((err as Error).message);
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!schedule) return null;

  async function save(body: Parameters<typeof adminUpdateSchedule>[1]) {
    if (!schedule) return;
    setBusy(true);
    onError(null);
    try {
      const updated = await adminUpdateSchedule(schedule.id, body);
      setSchedule(updated);
      setRunAt(updated.run_at_local ?? "04:30");
      setTargets(updated.targets ?? []);
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function trigger() {
    if (!schedule) return;
    if (!window.confirm("지금 즉시 수집을 1회 실행할까요? (워커 스케줄러가 다음 폴링에 발화)")) return;
    setBusy(true);
    onError(null);
    try {
      setSchedule(await adminTriggerSchedule(schedule.id));
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function toggleTarget(key: string) {
    setTargets((prev) => (prev.includes(key) ? prev.filter((t) => t !== key) : [...prev, key]));
  }

  return (
    <section className="card mt-8 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">수집 스케줄</h2>
          <p className="mt-0.5 text-[12.5px] text-muted">
            매일 {schedule.timezone ?? "Asia/Seoul"} {schedule.run_at_local ?? "-"} 자동 수집 ·{" "}
            {schedule.enabled ? "활성" : "비활성"}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void save({ enabled: !schedule.enabled })}
          disabled={busy}
          className={`rounded-full px-4 py-2 text-[13.5px] font-semibold disabled:opacity-60 ${
            schedule.enabled
              ? "border border-line text-navy-soft hover:border-navy"
              : "brand-grad text-white"
          }`}
        >
          {schedule.enabled ? "비활성화" : "활성화"}
        </button>
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <div>
          <label className="text-[12.5px] font-semibold text-muted">실행 시각 (KST)</label>
          <div className="mt-2 flex items-center gap-2">
            <input
              type="time"
              value={runAt}
              onChange={(e) => setRunAt(e.target.value)}
              className="card px-3 py-2 text-[14px] outline-none focus:border-sky"
            />
            <button
              type="button"
              onClick={() => void save({ run_at_local: runAt })}
              disabled={busy || runAt === schedule.run_at_local}
              className="text-[13px] font-semibold text-sky-deep disabled:opacity-50"
            >
              시각 저장
            </button>
          </div>

          <label className="mt-4 block text-[12.5px] font-semibold text-muted">수집 대상</label>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            {ALL_TARGETS.map((key) => (
              <label key={key} className="flex items-center gap-1.5 text-[13.5px]">
                <input type="checkbox" checked={targets.includes(key)} onChange={() => toggleTarget(key)} />
                {TARGET_LABEL[key] ?? key}
              </label>
            ))}
            <button
              type="button"
              onClick={() => void save({ targets })}
              disabled={busy}
              className="text-[13px] font-semibold text-sky-deep disabled:opacity-50"
            >
              대상 저장
            </button>
          </div>
        </div>

        <div className="rounded-xl bg-surface-2 p-4">
          <div className="text-[12.5px] font-semibold text-muted">실행 상태</div>
          <dl className="mt-2 space-y-1.5 text-[13px]">
            <div className="flex justify-between gap-4">
              <dt className="text-muted">마지막 실행</dt>
              <dd className="font-medium">{fmtDateTime(schedule.last_run_at)}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">결과</dt>
              <dd className="font-medium">{schedule.last_status ?? "-"}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">다음 예정</dt>
              <dd className="font-medium">{fmtDateTime(schedule.next_run_at)}</dd>
            </div>
          </dl>
          <button
            type="button"
            onClick={() => void trigger()}
            disabled={busy}
            className="brand-grad mt-4 w-full rounded-full py-2.5 text-[14px] font-bold text-white disabled:opacity-60"
          >
            지금 실행
          </button>
        </div>
      </div>
    </section>
  );
}
