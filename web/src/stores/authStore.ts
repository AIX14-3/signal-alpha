"use client";

import { create } from "zustand";
import {
  getMe,
  login as apiLogin,
  logout as apiLogout,
  signup as apiSignup,
  type User,
} from "@/lib/apiClient";
import {
  clearUserTokens,
  getAccessToken,
  getRefreshToken,
  setUserTokens,
} from "@/lib/session";

type AuthState = {
  user: User | null;
  status: "idle" | "loading" | "authenticated" | "anonymous";
  error: string | null;
  login: (email: string, password: string) => Promise<void>;
  signup: (input: { email: string; password: string; nickname?: string }) => Promise<void>;
  logout: () => Promise<void>;
  hydrate: () => Promise<void>;
};

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: "idle",
  error: null,

  async login(email, password) {
    set({ status: "loading", error: null });
    try {
      const result = await apiLogin({ email, password });
      setUserTokens(result.access_token, result.refresh_token);
      set({ user: result.user, status: "authenticated" });
    } catch (error) {
      set({ status: "anonymous", error: (error as Error).message });
      throw error;
    }
  },

  async signup(input) {
    set({ status: "loading", error: null });
    try {
      const result = await apiSignup({ ...input, agreed_risk: true });
      setUserTokens(result.access_token, result.refresh_token);
      set({ user: result.user, status: "authenticated" });
    } catch (error) {
      set({ status: "anonymous", error: (error as Error).message });
      throw error;
    }
  },

  async logout() {
    const refreshToken = getRefreshToken();
    if (refreshToken) {
      try {
        await apiLogout(refreshToken);
      } catch {
        /* 서버 실패와 무관하게 로컬 토큰은 제거 */
      }
    }
    clearUserTokens();
    set({ user: null, status: "anonymous" });
  },

  async hydrate() {
    if (!getAccessToken()) {
      set({ status: "anonymous" });
      return;
    }
    set({ status: "loading" });
    try {
      const user = await getMe();
      set({ user, status: "authenticated" });
    } catch {
      clearUserTokens();
      set({ user: null, status: "anonymous" });
    }
  },
}));
