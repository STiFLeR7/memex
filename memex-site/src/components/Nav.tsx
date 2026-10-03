'use client';

import { useEffect, useState } from 'react';
import { NAV_LINKS } from '@/lib/content';

export default function Nav() {
  const [stuck, setStuck] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setStuck(window.scrollY > 40);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <header className={`nav${stuck ? ' stuck' : ''}`}>
      <div className="edge wrap nav-row">
        <a className="brand" href="#top">
          <span className="mk">M</span>memex
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
          className="menu-btn"
          aria-expanded={open}
          aria-controls="sheet"
          onClick={() => setOpen((v) => !v)}
        >
          Menu
        </button>
      </div>
      <nav
        id="sheet"
        className={`sheet${open ? ' open' : ''}`}
        aria-label="Mobile"
      >
        {NAV_LINKS.map((l) => (
          <a key={l.href} href={l.href} onClick={() => setOpen(false)}>
            {l.label}
          </a>
        ))}
      </nav>
    </header>
  );
}
