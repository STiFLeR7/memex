'use client';

import { useEffect, useRef, type ReactNode } from 'react';
import { currentConfidence, formatConfidence } from '@/lib/confidence';

/**
 * Every quantity here derives from the real decay function. The dot-field
 * density, the bar fill and the printed readout all read the same number, so
 * the visual cannot drift from the text (it did: the field encoded 0.78 while
 * the readout said 0.60).
 *
 * State 04 has no confidence to show — the record has left live traversal —
 * so it gets a deliberately minimal field and says so in words.
 */
const CONF = {
  created: currentConfidence(0.6, 2),
  corroborated: currentConfidence(0.6, 0),
  superseded: currentConfidence(0.6, 26),
} as const;

const EXCLUDED_FIELD = 0.04;
const STATES: ReadonlyArray<{
  n: string;
  title: string;
  body: ReactNode;
  /** drives halftone density */
  field: number;
  bar: number;
  readout: string;
  off?: boolean;
}> = [
  {
    n: 'State 01',
    title: 'Created',
    body: (
      <>
        Written by the watcher with <code>created_at</code>, a base confidence
        of 0.6 and a reinforcement anchor. Unvalidated.
      </>
    ),
    field: CONF.created,
    bar: CONF.created,
    readout: formatConfidence(CONF.created),
  },
  {
    n: 'State 02',
    title: 'Corroborated',
    body: (
      <>
        New evidence resets <code>last_reinforced_at</code>. It does{' '}
        <em>not</em> validate. Only a human review crosses that line.
      </>
    ),
    field: CONF.corroborated,
    bar: CONF.corroborated,
    readout: `${formatConfidence(CONF.corroborated)} · clock reset`,
  },
  {
    n: 'State 03',
    title: 'Superseded',
    body: (
      <>
        A replacement Decision is written and the predecessor&rsquo;s outgoing
        edges expire. Returned only on an explicit historical request,
        labelled.
      </>
    ),
    field: CONF.superseded,
    bar: CONF.superseded,
    readout: `${formatConfidence(CONF.superseded)} · historical`,
  },
  {
    n: 'State 04',
    title: 'Invalidated',
    body: (
      <>
        <code>invalidate_edge</code> sets <code>expired_at</code>. The record is
        not deleted; it leaves live traversal and stays auditable.
      </>
    ),
    field: EXCLUDED_FIELD,
    bar: 0,
    readout: 'excluded from live traversal',
    off: true,
  },
];

function ConfidenceField({ conf }: { conf: number }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;

    const draw = () => {
      const r = cv.getBoundingClientRect();
      const w = Math.max(1, r.width);
      const h = Math.max(1, r.height);
      if (w < 2 || h < 2) return;
      const d = Math.min(window.devicePixelRatio || 1, 2);
      cv.width = w * d;
      cv.height = h * d;
      ctx.setTransform(d, 0, 0, d, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const cell = 10;
      const dens = 0.26 + conf * 0.62;
      const slope = 1.3 - conf * 0.5;
      for (let x = 0; x < w; x += cell) {
        const tx = x / w;
        const keep = dens * Math.max(0, 1 - tx * slope);
        if (keep <= 0) break;
        for (let y = 0; y < h; y += cell) {
          const n = Math.abs((Math.sin(x * 0.17 + y * 0.31) * 43758.5453) % 1);
          if (n > keep) continue;
          const rad = Math.max(0.55, 2.3 * (1 - tx * 0.7) * (0.42 + conf));
          ctx.fillStyle =
            conf > 0.5
              ? `rgba(143,195,255,${(0.16 + conf * 0.2).toFixed(3)})`
              : `rgba(118,142,184,${(0.34 + conf * 0.2).toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(x, y, rad, 0, 6.2832);
          ctx.fill();
        }
      }
    };

    // measure after layout settles, then follow resizes
    const raf = requestAnimationFrame(() => requestAnimationFrame(draw));
    const ro = new ResizeObserver(draw);
    ro.observe(cv);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [conf]);

  return <canvas ref={ref} aria-hidden="true" />;
}

export default function Lifecycle() {
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const bars = el.querySelectorAll<HTMLElement>('[data-bar]');
    const io = new IntersectionObserver(
      ([e]) => {
        if (!e.isIntersecting) return;
        bars.forEach((b, i) => {
          setTimeout(() => {
            b.style.right = `${100 - Number(b.dataset.bar) * 100}%`;
          }, 180 + i * 130);
        });
        io.disconnect();
      },
      { threshold: 0.3 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <section className="motion" id="motion">
      <div className="edge wrap">
        <div className="motion-head">
          <div className="h">
            <p className="tech rv" style={{ marginBottom: 26 }}>
              04 <span className="sep">/</span> The system in motion
            </p>
            <h2 className="rv d1">
              A fact has a life, and the graph records all of it.
            </h2>
          </div>
          <div className="formula rv d2">
            validated<span className="dim">:</span> base × e<sup>−0.005·d</sup>{' '}
            <b>floor 0.7</b>
            <br />
            unvalidated ≤30d<span className="dim">:</span> base ×
            e<sup>−(ln2/30)·d</sup>
            <br />
            unvalidated &gt;30d<span className="dim">:</span> base ×
            e<sup>−(ln2/20)·d</sup> <b>cap 0.5</b>
          </div>
        </div>

        <div className="states rv d2" ref={wrap}>
          {STATES.map((s) => (
            <article className="state" key={s.n}>
              <ConfidenceField conf={s.field} />
              <span className="n">{s.n}</span>
              <h3>{s.title}</h3>
              <p>{s.body}</p>
              <span className={`val${s.off ? ' off' : ''}`}>{s.readout}</span>
              <span className="bar">
                <i data-bar={s.bar} />
              </span>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
