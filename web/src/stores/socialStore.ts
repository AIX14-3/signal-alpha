"use client";

import { create } from "zustand";
import { linkSocial, listSocial, unlinkSocial, type Provider, type SocialLink } from "@/lib/apiClient";

type SocialState = {
  links: SocialLink[];
  loading: boolean;
  load: () => Promise<void>;
  link: (provider: Provider, code: string) => Promise<void>;
  unlink: (provider: Provider) => Promise<void>;
};

export const useSocialStore = create<SocialState>((set, get) => ({
  links: [],
  loading: false,

  async load() {
    set({ loading: true });
    try {
      const data = await listSocial();
      set({ links: data.items, loading: false });
    } catch {
      set({ loading: false });
    }
  },

  async link(provider, code) {
    await linkSocial(provider, { code });
    await get().load();
  },

  async unlink(provider) {
    await unlinkSocial(provider);
    await get().load();
  },
}));
