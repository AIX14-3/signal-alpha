"use client";

import { useEffect, useState } from "react";
import { adminLogout, adminMe } from "@/lib/apiClient";
import { AdminDashboard } from "./_components/AdminDashboard";
import { AdminLogin } from "./_components/AdminLogin";

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
