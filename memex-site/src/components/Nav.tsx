'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { NAV_LINKS } from '@/lib/content';
import Mark from './Mark';

export default function Nav() {
  const [stuck, setStuck] = useState(false);
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);

  /**
   * Every close path, so focus goes back to the trigger. Closing the sheet
   * display:nones whatever was focused inside it, which otherwise drops focus
   * to <body> and restarts the tab order at the top of the document.
   */
  const dismiss = useCallback(() => {
    setOpen(false);
    btnRef.current?.focus();
  }, []);

  useEffect(() => {
    const onScroll = () => setStuck(window.scrollY > 40);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') dismiss();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, dismiss]);

  return (
    <header className={`nav${stuck ? ' stuck' : ''}`}>
      <div className="edge wrap nav-row">
        <a className="brand" href="#top">
          <Mark size={22} className="mk" />memex
        </a>
        <nav aria-label="Primary">
          <ul className="navlinks">
            {NAV_LINKS.map((l) => (
              <li key={l.href}>
                <a href={l.href}>{l.label}</a>
              </li>
            ))}
          </ul>
        </nav>
        <button
          ref={btnRef}
          className="menu-btn"
          aria-expanded={open}
          aria-controls="sheet"
          onClick={() => setOpen((v) => !v)}
        >
          Menu
        </button>
      </div>
      {/* display:none while closed (block 05), so these links stay out of the
          tab order without needing `hidden` or conditional rendering. */}
      <nav
        id="sheet"
        className={`sheet${open ? ' open' : ''}`}
        aria-label="Mobile"
      >
        {NAV_LINKS.map((l) => (
          <a key={l.href} href={l.href} onClick={dismiss}>
            {l.label}
          </a>
        ))}
      </nav>
    </header>
  );
}
