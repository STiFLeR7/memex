'use client';

import { useEffect } from 'react';

/**
 * One observer for the whole page. Sections stay server components and just
 * carry `.rv` / `.stmt` classes; this island wires them up.
 *
 * The init pass is load-bearing: the observer's -12% bottom margin would
 * otherwise strand anything already on screen (the hero CTAs sit at y≈921 in
 * a 1000px viewport and never fired in the static build).
 *
 * This component owns the `.rv-ready` gate on <html> (globals.css block 04).
 * Nothing is hidden until we are standing here with a working observer, so a
 * missing bundle or a thrown hydration renders a plain, readable page.
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

    const markOnScreen = () => {
      targets.forEach((el) => {
        if (el.classList.contains('in')) return;
        if (el.getBoundingClientRect().top < window.innerHeight) {
          el.classList.add('in');
          io.unobserve(el);
        }
      });
    };

    // Gate on, then mark what is already on screen in the same task. Both
    // style changes land in one recalc, so above-the-fold content is never
    // painted hidden: the cost of inverting the gate is that the first screen
    // does not play its entrance, which beats blinking out and back.
    document.documentElement.classList.add('rv-ready');
    markOnScreen();

    // Still load-bearing, see above: at effect time fonts and the canvases
    // have not settled, so positions move. This is the pass that catches it.
    const raf = requestAnimationFrame(markOnScreen);

    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      document.documentElement.classList.remove('rv-ready');
    };
  }, []);

  return null;
}
