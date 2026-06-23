"use client";

import { useEffect, useRef } from "react";

// v2_converge 시안의 "신호 컨버전스" 배경: 가장자리에서 코어(50%,34%)로
// 빨려드는 파티클 + 트레일. 전체 뷰포트 고정 레이어.
export function BackgroundFX() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const COLORS = ["14,165,233", "16,185,129", "99,102,241"]; // sky, green, indigo

    let w = 0;
    let h = 0;
    let cx = 0;
    let cy = 0;
    let maxR = 0;
    let raf = 0;

    type Part = { a: number; d: number; px: number; py: number; sp: number; c: string; sz: number };
    let parts: Part[] = [];

    function spawn(seed: boolean): Part {
      const a = Math.random() * Math.PI * 2;
      const d = seed ? Math.random() * maxR : maxR * (0.75 + Math.random() * 0.25);
      return {
        a,
        d,
        px: cx + Math.cos(a) * d,
        py: cy + Math.sin(a) * d,
        sp: 0.6 + Math.random() * 1.1,
        c: COLORS[(Math.random() * COLORS.length) | 0],
        sz: 1 + Math.random() * 1.6,
      };
    }

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas!.width = w * dpr;
      canvas!.height = h * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      cx = w * 0.5;
      cy = h * 0.34;
      maxR = Math.hypot(Math.max(cx, w - cx), Math.max(cy, h - cy)) + 60;
    }

    function init() {
      parts = [];
      const n = Math.min(120, Math.round((w * h) / 14000));
      for (let i = 0; i < n; i++) parts.push(spawn(true));
    }

    function frame() {
      ctx!.clearRect(0, 0, w, h);
      for (const p of parts) {
        const x = cx + Math.cos(p.a) * p.d;
        const y = cy + Math.sin(p.a) * p.d;
        p.d -= p.sp * (0.5 + (1 - p.d / maxR) * 1.8);
        const t = 1 - p.d / maxR;
        const near = Math.min(1, p.d / 70);
        const alpha = Math.max(0, Math.min(0.9, t * 1.2)) * near;
        ctx!.strokeStyle = `rgba(${p.c},${(alpha * 0.5).toFixed(3)})`;
        ctx!.lineWidth = p.sz;
        ctx!.beginPath();
        ctx!.moveTo(p.px, p.py);
        ctx!.lineTo(x, y);
        ctx!.stroke();
        ctx!.fillStyle = `rgba(${p.c},${alpha.toFixed(3)})`;
        ctx!.beginPath();
        ctx!.arc(x, y, p.sz, 0, Math.PI * 2);
        ctx!.fill();
        p.px = x;
        p.py = y;
        if (p.d < 4) Object.assign(p, spawn(false));
      }
      raf = requestAnimationFrame(frame);
    }

    function staticFrame() {
      ctx!.clearRect(0, 0, w, h);
      for (let i = 0; i < 90; i++) {
        const a = Math.random() * Math.PI * 2;
        const d = Math.random() * maxR * 0.9;
        const x = cx + Math.cos(a) * d;
        const y = cy + Math.sin(a) * d;
        ctx!.fillStyle = `rgba(${COLORS[i % 3]},${(0.25 * (1 - d / maxR)).toFixed(3)})`;
        ctx!.beginPath();
        ctx!.arc(x, y, 1.4, 0, Math.PI * 2);
        ctx!.fill();
      }
    }

    function onResize() {
      resize();
      init();
      if (reduce) staticFrame();
    }

    resize();
    init();
    if (reduce) staticFrame();
    else frame();
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <div className="bg-fx" aria-hidden="true">
      <div className="grid-lines" />
      <div className="core" />
      <canvas ref={canvasRef} />
    </div>
  );
}
