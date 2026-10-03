'use client';

import { useEffect, useRef } from 'react';
import { currentConfidence } from '@/lib/confidence';

/**
 * The close is one fact, read twelve times, five days apart.
 *
 * Every ring is a read. Its brightness and its radius are `currentConfidence`
 * at that age — the same function the Temporal section prints and
 * `confidence.check.ts` pins. The structure is dense and lit where the
 * evidence is recent, and opens out into almost nothing as it ages.
 *
 * So the shape is not an arrangement of ellipses chosen to look good: change
 * the decay constants and this figure changes with them.
 */
const READS = 12;
const STEP_DAYS = 5;
const BASE = 0.6;

const RINGS = Array.from({ length: READS }, (_, i) => {
  const conf = currentConfidence(BASE, i * STEP_DAYS);
  return {
    conf,
    /** 1 at full confidence, contracting as the fact ages out */
    scale: 0.72 + 0.28 * (conf / BASE),
    /** eased so the falloff is visible rather than a uniform wireframe */
    alpha: 0.035 + 0.5 * Math.pow(conf / BASE, 1.5),
  };
});

export default function DecayRing() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;

    const reduced = matchMedia('(prefers-reduced-motion:reduce)').matches;
    let w = 0;
    let h = 0;
    let phase = 0;
    let raf = 0;
    let last = 0;

    function layout() {
      const r = cv!.getBoundingClientRect();
      const d = Math.min(window.devicePixelRatio || 1, 2);
      w = Math.max(1, r.width);
      h = Math.max(1, r.height);
      cv!.width = w * d;
      cv!.height = h * d;
      ctx!.setTransform(d, 0, 0, d, 0, 0);
    }

    function draw() {
      ctx!.clearRect(0, 0, w, h);
      const cx = w / 2;
      const cy = h / 2;
      const r = Math.min(w, h) * 0.46;

      ctx!.lineWidth = 1;
      RINGS.forEach((ring, i) => {
        // half a turn across the set: an ellipse maps onto itself at π, so
        // this closes the rosette exactly once.
        const rot = phase + (i * Math.PI) / READS;
        ctx!.save();
        ctx!.translate(cx, cy);
        ctx!.rotate(rot);
        ctx!.strokeStyle = `rgba(143,195,255,${ring.alpha.toFixed(3)})`;
        ctx!.beginPath();
        ctx!.ellipse(0, 0, r * ring.scale, r * ring.scale * 0.46, 0, 0, 6.2832);
        ctx!.stroke();
        ctx!.restore();
      });

      // the newest read, marked: one lit point on the outermost trace
      const head = RINGS[0];
      const hx = cx + Math.cos(phase) * r * head.scale;
      const hy = cy + Math.sin(phase) * r * head.scale * 0.46;
      const g = ctx!.createRadialGradient(hx, hy, 0, hx, hy, 26);
      g.addColorStop(0, 'rgba(200,224,255,.9)');
      g.addColorStop(0.3, 'rgba(45,106,255,.34)');
      g.addColorStop(1, 'rgba(45,106,255,0)');
      ctx!.fillStyle = g;
      ctx!.beginPath();
      ctx!.arc(hx, hy, 26, 0, 6.2832);
      ctx!.fill();
    }

    function tick(ts: number) {
      const dt = Math.min(48, ts - last || 16);
      last = ts;
      phase += dt * 0.00006;
      draw();
      raf = requestAnimationFrame(tick);
    }

    const onResize = () => {
      layout();
      draw();
    };

    layout();
    draw();
    window.addEventListener('resize', onResize, { passive: true });

    // only run while the close is actually on screen
    const vis = new IntersectionObserver(
      ([e]) => {
        if (reduced) return;
        if (e.isIntersecting && !raf) raf = requestAnimationFrame(tick);
        else if (!e.isIntersecting && raf) {
          cancelAnimationFrame(raf);
          raf = 0;
          last = 0;
        }
      },
      { threshold: 0.04 },
    );
    vis.observe(cv);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      vis.disconnect();
    };
  }, []);

  return <canvas className="ring-cv" ref={ref} aria-hidden="true" />;
}
