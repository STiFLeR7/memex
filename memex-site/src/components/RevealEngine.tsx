'use client';

import { useEffect } from 'react';

/**
 * One observer for the whole page. Sections stay server components and just
 * carry `.rv` / `.stmt` classes; this island wires them up.
 *
 * The init pass is load-bearing: the observer's -12% bottom margin would
 * otherwise strand anything already on screen (the hero CTAs sit at y≈921 in
 * a 1000px viewport and never fired in the static build).
 */
export default function RevealEngine() {
  useEffect(() => {
    const targets = document.querySelectorAll<HTMLElement>('.rv, .stmt');

    if (matchMedia('(prefers-reduced-motion:reduce)').matches) {
      targets.forEach((el) => el.classList.add('in'));
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          e.target.classList.add('in');
          io.unobserve(e.target);
        }
      },
      { rootMargin: '0px 0px -12% 0px', threshold: 0.08 },
    );

    targets.forEach((el) => io.observe(el));

    const raf = requestAnimationFrame(() => {
      targets.forEach((el) => {
        if (el.classList.contains('in')) return;
        if (el.getBoundingClientRect().top < window.innerHeight) {
          el.classList.add('in');
          io.unobserve(el);
        }
      });
    });

    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
    };
  }, []);

  return null;
}
