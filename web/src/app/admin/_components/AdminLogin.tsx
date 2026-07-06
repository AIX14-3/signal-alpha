"use client";

import { useState } from "react";
import { adminLogin } from "@/lib/apiClient";

export function AdminLogin({ onSuccess }: { onSuccess: () => void }) {
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
