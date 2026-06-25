"use client";

import { useCallback, useEffect, useState } from "react";
import {
  adminCancelSubscription,
  adminGetStats,
  adminListUsers,
  adminLogin,
  adminLogout,
  adminMe,
  adminRefund,
  adminSetSubscription,
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

      <div className="mt-10 flex items-center justify-between">
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
              <th className="px-5 py-3 font-semibold">구독</th>
              <th className="px-5 py-3 font-semibold">가입일</th>
              <th className="px-5 py-3 font-semibold">관리</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const active = user.subscription?.status === "active";
              return (
                <tr key={user.id} className="border-t border-line">
                  <td className="px-5 py-3 font-semibold">{user.member_code ?? user.id}</td>
                  <td className="px-5 py-3">{user.nickname ?? "-"}</td>
                  <td className="px-5 py-3">{active ? "월 구독" : "무료"}</td>
                  <td className="px-5 py-3 text-muted">{user.created_at ? user.created_at.slice(0, 10) : "-"}</td>
                  <td className="px-5 py-3">
                    {active ? (
                      <div className="flex gap-3">
                        <button type="button" onClick={() => void revoke(user.id)} className="text-[13px] font-semibold text-muted hover:text-red">구독 취소</button>
                        <button type="button" onClick={() => void refundUser(user.id)} className="text-[13px] font-semibold text-muted hover:text-red">환불</button>
                      </div>
                    ) : (
                      <button type="button" onClick={() => void grant(user.id)} className="text-[13px] font-semibold text-sky-deep">구독 부여</button>
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
