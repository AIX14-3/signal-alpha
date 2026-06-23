"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuthStore } from "@/stores/authStore";

// 소셜 로그인(구글/네이버/카카오)은 별도 트랙 — 현재는 자리표시만 둔다.
const SOCIALS = [
  { key: "google", label: "Google로 계속하기" },
  { key: "naver", label: "네이버로 계속하기" },
  { key: "kakao", label: "카카오로 계속하기" },
];

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const login = useAuthStore((state) => state.login);
  const signup = useAuthStore((state) => state.signup);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isSignup = mode === "signup";

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (isSignup && !agreed) {
      setError("서비스 고지에 동의해야 가입할 수 있습니다.");
      return;
    }
    setBusy(true);
    try {
      if (isSignup) {
        await signup({ email, password, nickname: nickname || undefined });
      } else {
        await login(email, password);
      }
      router.push("/mypage");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-[400px] py-16">
      <h1 className="text-[28px] font-extrabold">{isSignup ? "회원가입" : "로그인"}</h1>
      <p className="mt-1 text-[14px] text-muted">
        {isSignup ? "이메일로 Signal α를 시작하세요." : "다시 오신 것을 환영합니다."}
      </p>

      <form onSubmit={onSubmit} className="mt-8 space-y-3">
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="이메일"
          className="card w-full px-4 py-3 text-[15px] outline-none focus:border-sky"
        />
        <input
          type="password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="비밀번호 (8자 이상)"
          className="card w-full px-4 py-3 text-[15px] outline-none focus:border-sky"
        />
        {isSignup && (
          <>
            <input
              value={nickname}
              onChange={(e) => setNickname(e.target.value)}
              placeholder="닉네임 (선택)"
              className="card w-full px-4 py-3 text-[15px] outline-none focus:border-sky"
            />
            <label className="flex items-center gap-2 px-1 text-[13px] text-navy-soft">
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
              Signal α는 투자 권유가 아닌 데이터 방향성·근거 제공 서비스임에 동의합니다.
            </label>
          </>
        )}

        {error && <p className="text-[13px] text-red">{error}</p>}

        <button
          type="submit"
          disabled={busy}
          className="brand-grad w-full rounded-full py-3 text-[15px] font-bold text-white disabled:opacity-60"
        >
          {busy ? "처리 중…" : isSignup ? "가입하기" : "로그인"}
        </button>
      </form>

      <div className="my-6 flex items-center gap-3 text-[12px] text-muted">
        <span className="h-px flex-1 bg-line" /> 또는 <span className="h-px flex-1 bg-line" />
      </div>

      <div className="space-y-2">
        {SOCIALS.map((social) => (
          <button
            key={social.key}
            type="button"
            disabled
            title="소셜 로그인은 준비 중입니다"
            className="w-full rounded-full border border-line py-3 text-[14px] font-semibold text-muted"
          >
            {social.label} (준비 중)
          </button>
        ))}
      </div>

      <p className="mt-6 text-center text-[14px] text-muted">
        {isSignup ? (
          <>
            이미 계정이 있나요? <Link href="/login" className="font-semibold text-sky-deep">로그인</Link>
          </>
        ) : (
          <>
            계정이 없나요? <Link href="/signup" className="font-semibold text-sky-deep">회원가입</Link>
          </>
        )}
      </p>
    </div>
  );
}
