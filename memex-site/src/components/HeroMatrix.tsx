'use client';

import { useEffect, useRef } from 'react';

type Cell = { x: number; y: number; a: number; ph: number; up: boolean };

const CELL = 26;
const ARM = 4.4;

/**
 * Directional chevron matrix. Dense at the edges and up top, dissolving into
 * the floor light — infrastructure, not decoration.
 */
export default function HeroMatrix() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;

    const reduced = matchMedia('(prefers-reduced-motion:reduce)').matches;
    let w = 0;
    let h = 0;
    let cells: Cell[] = [];
    let raf = 0;
    let t = 0;

    const dpr = () => Math.min(window.devicePixelRatio || 1, 2);

    function build() {
      const r = cv!.getBoundingClientRect();
      const d = dpr();
      w = Math.max(1, r.width);
      h = Math.max(1, r.height);
      cv!.width = w * d;
      cv!.height = h * d;
      ctx!.setTransform(d, 0, 0, d, 0, 0);

      cells = [];
      const cols = Math.ceil(w / CELL) + 1;
      const rows = Math.ceil(h / CELL) + 1;
      for (let cx = 0; cx < cols; cx++) {
        for (let cy = 0; cy < rows; cy++) {
          const x = cx * CELL;
          const y = cy * CELL;
          const vy = 1 - Math.min(1, y / (h * 0.92));
          const nx = Math.abs(x / w - 0.5) * 2;
          const edge = 0.26 + nx * 0.74;
          const n = (Math.sin(cx * 12.9898 + cy * 78.233) * 43758.5453) % 1;
          const rnd = n - Math.floor(n);
          const a = vy * edge * (0.35 + rnd * 0.65);
          if (a < 0.05) continue;
          cells.push({ x, y, a: Math.min(1, a), ph: rnd * Math.PI * 2, up: rnd > 0.55 });
        }
      }
    }

    function draw() {
      ctx!.clearRect(0, 0, w, h);
      for (const c of cells) {
        const pulse = reduced ? 1 : 0.76 + 0.24 * Math.sin(t * 0.0009 + c.ph);
        const a = c.a * pulse;
        if (a < 0.035) continue;
        const col = c.y / h > 0.55 ? '77,136,255' : '158,203,255';
        ctx!.strokeStyle = `rgba(${col},${(a * 0.92).toFixed(3)})`;
        ctx!.lineWidth = 1.1;
        ctx!.beginPath();
        if (c.up) {
          ctx!.moveTo(c.x - ARM, c.y + ARM);
          ctx!.lineTo(c.x, c.y - ARM);
          ctx!.lineTo(c.x + ARM, c.y + ARM);
        } else {
          ctx!.moveTo(c.x - ARM, c.y - ARM);
          ctx!.lineTo(c.x + ARM, c.y - ARM);
          ctx!.lineTo(c.x + ARM, c.y + ARM);
        }
        ctx!.stroke();
      }
    }

    const loop = (ts: number) => {
      t = ts;
      draw();
      raf = requestAnimationFrame(loop);
    };

    const onResize = () => {
      build();
      draw();
    };

    build();
    draw();
    window.addEventListener('resize', onResize, { passive: true });

    // Only animate while on screen.
    const vis = new IntersectionObserver(
      ([e]) => {
        if (reduced) return;
        if (e.isIntersecting && !raf) raf = requestAnimationFrame(loop);
        else if (!e.isIntersecting && raf) {
          cancelAnimationFrame(raf);
          raf = 0;
        }
      },
      { threshold: 0.01 },
    );
    vis.observe(cv);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      vis.disconnect();
    };
  }, []);

  return <canvas id="heroMatrix" ref={ref} aria-hidden="true" />;
}
