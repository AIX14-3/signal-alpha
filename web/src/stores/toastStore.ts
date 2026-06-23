"use client";

import { create } from "zustand";

export type ToastTone = "info" | "error" | "success";
export type Toast = { id: number; message: string; tone: ToastTone };

type ToastState = {
  toasts: Toast[];
  show: (message: string, tone?: ToastTone) => void;
  dismiss: (id: number) => void;
};

let seq = 0;
const DURATION_MS = 3200;

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  show(message, tone = "info") {
    const id = ++seq;
    set((state) => ({ toasts: [...state.toasts, { id, message, tone }] }));
    setTimeout(() => {
      set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
    }, DURATION_MS);
  },
  dismiss(id) {
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
  },
}));
