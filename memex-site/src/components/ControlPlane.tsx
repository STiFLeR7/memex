'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { STAGES } from '@/lib/content';

type Node = { x: number; y: number; r: number; lit: number };
type Pulse = { i: number; p: number };

export default function ControlPlane() {
  const cvRef = useRef<HTMLCanvasElement>(null);
  const [active, setActive] = useState(0);
  const activeRef = useRef(0);
  const manualRef = useRef(false);
  // Populated by the canvas effect. Under reduced motion no rAF loop runs,
  // so a selection would update the caption and leave the diagram stale.
  const redrawRef = useRef<(() => void) | null>(null);

  const select = useCallback((i: number, manual = false) => {
    if (manual) manualRef.current = true;
    activeRef.current = i;
    setActive(i);
  }, []);

  useEffect(() => {
    const cv = cvRef.current;
    if (!cv) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;

    const reduced = matchMedia('(prefers-reduced-motion:reduce)').matches;
    let w = 0;
    let h = 0;
    let nodes: Node[] = [];
    let pulses: Pulse[] = [];
    let raf = 0;
    let last = 0;

    const dpr = () => Math.min(window.devicePixelRatio || 1, 2);

    function layout() {
      const r = cv!.getBoundingClientRect();
      const d = dpr();
      w = Math.max(1, r.width);
      h = Math.max(1, r.height);
      cv!.width = w * d;
      cv!.height = h * d;
      ctx!.setTransform(d, 0, 0, d, 0, 0);

      nodes = [];
      const n = STAGES.length;
      const padX = Math.min(90, w * 0.08);
      const span = w - padX * 2;
      for (let i = 0; i < n; i++) {
        nodes.push({
          x: padX + (span * i) / (n - 1),
          y: h / 2 + (i % 2 === 0 ? -1 : 1) * h * 0.145 + (i === 3 ? -h * 0.03 : 0),
          r: i === 3 ? 13 : 8.5,
          lit: 0,
        });
      }
    }

    /** Orthogonal routing with a mid break — control-plane, not flowchart. */
    function connector(a: Node, b: Node) {
      const mx = (a.x + b.x) / 2;
      ctx!.beginPath();
      ctx!.moveTo(a.x, a.y);
      ctx!.lineTo(mx, a.y);
      ctx!.lineTo(mx, b.y);
      ctx!.lineTo(b.x, b.y);
      ctx!.stroke();
    }

    function pointAt(a: Node, b: Node, p: number) {
      const mx = (a.x + b.x) / 2;
      const l1 = Math.abs(mx - a.x);
      const l2 = Math.abs(b.y - a.y);
      const l3 = Math.abs(b.x - mx);
      const total = l1 + l2 + l3 || 1;
      let d = p * total;
      if (d <= l1) return { x: a.x + Math.sign(mx - a.x) * d, y: a.y };
      d -= l1;
      if (d <= l2) return { x: mx, y: a.y + Math.sign(b.y - a.y) * d };
      d -= l2;
      return { x: mx + Math.sign(b.x - mx) * d, y: b.y };
    }

    function draw() {
      ctx!.clearRect(0, 0, w, h);

      ctx!.strokeStyle = 'rgba(132,172,235,.10)';
      ctx!.lineWidth = 1;
      ctx!.beginPath();
      for (let x = 0; x < w; x += 34) {
        ctx!.moveTo(x + 0.5, 0);
        ctx!.lineTo(x + 0.5, h);
      }
      for (let y = 0; y < h; y += 34) {
        ctx!.moveTo(0, y + 0.5);
        ctx!.lineTo(w, y + 0.5);
      }
      ctx!.stroke();

      for (let i = 0; i < nodes.length - 1; i++) {
        const lit = Math.min(nodes[i].lit, nodes[i + 1].lit);
        ctx!.strokeStyle = `rgba(143,195,255,${(0.22 + lit * 0.58).toFixed(3)})`;
        ctx!.lineWidth = lit > 0.5 ? 1.4 : 1;
        connector(nodes[i], nodes[i + 1]);
      }

      for (const p of pulses) {
        const a = nodes[p.i];
        const b = nodes[p.i + 1];
        if (!a || !b) continue;
        const pt = pointAt(a, b, p.p);
        const g = ctx!.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, 13);
        g.addColorStop(0, 'rgba(200,224,255,.95)');
        g.addColorStop(0.4, 'rgba(45,106,255,.45)');
        g.addColorStop(1, 'rgba(45,106,255,0)');
        ctx!.fillStyle = g;
        ctx!.beginPath();
        ctx!.arc(pt.x, pt.y, 13, 0, 6.2832);
        ctx!.fill();
      }

      nodes.forEach((n, i) => {
        if (n.lit > 0.01) {
          const g = ctx!.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 5.5);
          g.addColorStop(0, `rgba(45,106,255,${(n.lit * 0.5).toFixed(3)})`);
          g.addColorStop(1, 'rgba(45,106,255,0)');
          ctx!.fillStyle = g;
          ctx!.beginPath();
          ctx!.arc(n.x, n.y, n.r * 5.5, 0, 6.2832);
          ctx!.fill();
        }
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, n.r, 0, 6.2832);
        ctx!.fillStyle = 'rgba(5,8,15,.96)';
        ctx!.fill();
        ctx!.strokeStyle = `rgba(158,203,255,${(0.42 + n.lit * 0.58).toFixed(3)})`;
        ctx!.lineWidth = i === activeRef.current ? 2 : 1.2;
        ctx!.stroke();

        if (n.lit > 0.4) {
          ctx!.beginPath();
          ctx!.arc(n.x, n.y, n.r * 0.42, 0, 6.2832);
          ctx!.fillStyle = `rgba(214,232,255,${n.lit.toFixed(3)})`;
          ctx!.fill();
        }

        ctx!.font = '500 10px var(--f-mono), monospace';
        ctx!.fillStyle = `rgba(${
          i === activeRef.current ? '214,232,255' : '138,160,196'
        },${(0.62 + n.lit * 0.38).toFixed(3)})`;
        ctx!.textAlign = 'center';
        ctx!.fillText(STAGES[i].title.toUpperCase(), n.x, n.y - n.r - 15);
      });
    }

    function tick(ts: number) {
      const dt = Math.min(48, ts - last || 16);
      last = ts;
      nodes.forEach((n, i) => {
        const target = i <= activeRef.current ? 1 : 0.1;
        n.lit += (target - n.lit) * (dt / 420);
      });
      pulses = pulses.filter((p) => {
        p.p += dt / 1550;
        return p.p <= 1;
      });
      draw();
      raf = requestAnimationFrame(tick);
    }

    const onResize = () => {
      layout();
      draw();
    };

    redrawRef.current = () => {
      nodes.forEach((n, i) => {
        n.lit = i <= activeRef.current ? 1 : 0.1;
      });
      draw();
    };

    layout();
    draw();
    window.addEventListener('resize', onResize, { passive: true });

    let emitter: ReturnType<typeof setInterval> | undefined;
    let onScreen = false;
    const vis = new IntersectionObserver(
      ([e]) => {
        onScreen = e.isIntersecting;
        if (reduced) return;
        if (e.isIntersecting && !raf) raf = requestAnimationFrame(tick);
        else if (!e.isIntersecting && raf) {
          cancelAnimationFrame(raf);
          raf = 0;
          pulses = []; // nothing prunes them while the loop is stopped
        }
      },
      { threshold: 0.06 },
    );
    vis.observe(cv);

    const onScroll = () => {
      if (manualRef.current) return;
      const r = cv.getBoundingClientRect();
      const vh = window.innerHeight;
      if (r.bottom < 0 || r.top > vh) return;
      const p = Math.min(1, Math.max(0, (vh * 0.85 - r.top) / (vh * 0.75)));
      const i = Math.min(STAGES.length - 1, Math.floor(p * STAGES.length));
      if (i !== activeRef.current) select(i);
    };

    if (!reduced) {
      emitter = setInterval(() => {
        if (!onScreen || activeRef.current < 1) return;
        pulses.push({ i: Math.floor(Math.random() * activeRef.current), p: 0 });
      }, 1400);
      window.addEventListener('scroll', onScroll, { passive: true });
    }

    return () => {
      redrawRef.current = null;
      if (raf) cancelAnimationFrame(raf);
      if (emitter) clearInterval(emitter);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('scroll', onScroll);
      vis.disconnect();
    };
  }, [select]);

  useEffect(() => {
    redrawRef.current?.();
  }, [active]);

  const stage = STAGES[active];

  return (
    <section className="plane" id="plane">
      <div className="edge wrap">
        <div className="plane-head">
          <div className="h">
            <p className="tech rv" style={{ marginBottom: 26 }}>
              02 <span className="sep">/</span> The control plane
            </p>
            <h2 className="rv d1">
              Repository motion becomes graph state. Agents read and write
              across an explicit boundary.
            </h2>
          </div>
          <p className="note lede rv d2">
            Six stages. One LLM call on the entire write path, and it runs on
            commits, never inside a tool call.
          </p>
        </div>

        <div className="plane-stage rv d2">
          <canvas id="planeCanvas" ref={cvRef} aria-hidden="true" />
        </div>

        <div className="plane-legend" role="tablist" aria-label="Pipeline stages">
          {STAGES.map((s, i) => (
            <button
              key={s.n}
              role="tab"
              aria-selected={i === active}
              aria-current={i === active}
              onClick={() => select(i, true)}
            >
              <span className="n">{s.n}</span>
              <span className="t">{s.title}</span>
              <span className="p">{s.path}</span>
            </button>
          ))}
        </div>

        <div className="plane-caption">
          <p key={stage.n}>{stage.caption}</p>
          <p className="art">{stage.artifact}</p>
        </div>
      </div>
    </section>
  );
}
