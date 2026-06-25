"use client";

import { create } from "zustand";
import {
  getMe,
  login as apiLogin,
  logout as apiLogout,
  refreshSession,
  signup as apiSignup,
  socialLogin as apiSocialLogin,
  type AuthResult,
  type Provider,
  type User,
} from "@/lib/apiClient";
import { clearUserTokens, setUserTokens } from "@/lib/session";
import { certify } from "@/lib/portone";

type AuthState = {
  user: User | null;
  status: "idle" | "loading" | "authenticated" | "anonymous";
  error: string | null;
  loginWithIdentity: () => Promise<void>;
  signupWithIdentity: (input: { email: string; nickname: string; agreed_terms?: string[] }) => Promise<void>;
  socialLoginWith: (provider: Provider, code: string) => Promise<void>;
  refreshMe: () => Promise<void>;
  logout: () => Promise<void>;
  hydrate: () => Promise<void>;
};

function apply(set: (partial: Partial<AuthState>) => void, result: AuthResult): void {
  setUserTokens(result.access_token); // refresh 는 HttpOnly 쿠키로 자동 설정됨
  set({ user: result.user, status: "authenticated", error: null });
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: "idle",
  error: null,

  async loginWithIdentity() {
    set({ status: "loading", error: null });
    try {
      const identity_verification_id = await certify();
      apply(set, await apiLogin({ identity_verification_id }));
    } catch (error) {
      set({ status: "anonymous", error: (error as Error).message });
      throw error;
    }
  },

  async signupWithIdentity(input) {
    set({ status: "loading", error: null });
    try {
      const identity_verification_id = await certify();
      apply(
        set,
        await apiSignup({
          identity_verification_id,
          email: input.email,
          nickname: input.nickname,
          agreed_risk: true,
          agreed_terms: input.agreed_terms ?? ["service", "privacy"],
        }),
      );
    } catch (error) {
      set({ status: "anonymous", error: (error as Error).message });
      throw error;
    }
  },

  async socialLoginWith(provider, code) {
    set({ status: "loading", error: null });
    try {
      apply(set, await apiSocialLogin(provider, { code }));
    } catch (error) {
      set({ status: "anonymous", error: (error as Error).message });
      throw error;
    }
  },

  async refreshMe() {
    try {
      set({ user: await getMe() });
    } catch {
      /* noop */
    }
  },

  async logout() {
    try {
      await apiLogout(); // 서버가 refresh 쿠키를 읽어 세션 폐기·쿠키 삭제
    } catch {
      /* 서버 실패와 무관하게 인메모리 토큰 제거 */
    }
    clearUserTokens();
    set({ user: null, status: "anonymous" });
  },

  async hydrate() {
    // access 는 인메모리라 새로고침 시 비어 있음 → refresh 쿠키로 재발급 시도.
    set({ status: "loading" });
    try {
      const ok = await refreshSession();
      if (!ok) {
        set({ user: null, status: "anonymous" });
        return;
      }
      set({ user: await getMe(), status: "authenticated" });
    } catch {
      clearUserTokens();
      set({ user: null, status: "anonymous" });
    }
  },
}));
