(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const dpr = () => Math.min(window.devicePixelRatio || 1, 2);
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const root = document.documentElement, isDark = () => root.dataset.theme === 'dark';

  /* Theme: the visitor's system setting until they choose; the choice is kept.
     The switch spreads from the toggle as a circle where the browser allows it. */
  (() => {
    const tb = $('#theme'), meta = $('meta[name="theme-color"]');
    const set = (t, keep) => {
      root.dataset.theme = t; meta.content = t === 'dark' ? '#0A0D0F' : '#F7F9F8';
      tb.setAttribute('aria-pressed', t === 'dark'); tb.setAttribute('aria-label', `Switch to ${t === 'dark' ? 'light' : 'dark'} theme`);
      if (keep) try { localStorage.setItem('memex-theme', t); } catch {}
      dispatchEvent(new Event('themechange'));
    };
    set(root.dataset.theme === 'dark' ? 'dark' : 'light', false);
    tb.addEventListener('click', () => {
      const next = isDark() ? 'light' : 'dark';
      if (reduced || !document.startViewTransition) return set(next, true);
      const r = tb.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
      const R = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
      document.startViewTransition(() => set(next, true)).ready.then(() => root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${R}px at ${x}px ${y}px)`] },
        { duration: 700, easing: 'cubic-bezier(.16,1,.3,1)', pseudoElement: '::view-transition-new(root)' }));
    });
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
      let kept = null; try { kept = localStorage.getItem('memex-theme'); } catch {}
      if (!kept) set(e.matches ? 'dark' : 'light', false);
    });
  })();

  /* Film: the poster stands in until the reader asks for the video. */
  (() => {
    const frame = $('#frame'), v = $('#filmV'), play = $('#play');
    play.addEventListener('click', () => { frame.classList.add('playing'); v.controls = true; v.play().catch(() => {}); v.focus(); });
  })();
  const whenVisible = (el, start, stop) => new IntersectionObserver(([e]) => e.isIntersecting ? start() : stop(), { threshold: 0.01 }).observe(el);

  /* Headlines split into words for the staggered rise. The words ship in the
     markup, so the page reads the same with no script. */
  $$('[data-words]').forEach(h => {
    let seq = 0;
    const split = node => [...node.childNodes].forEach(n => {
      if (n.nodeType !== 3) return split(n);
      const frag = document.createDocumentFragment();
      n.textContent.split(/(\s+)/).forEach(part => {
        if (!part) return;
        if (/^\s+$/.test(part)) return frag.append(' ');
        const wr = document.createElement('span'), i = document.createElement('i');
        wr.className = 'wr'; i.textContent = part; i.style.transitionDelay = (seq++ * 44) + 'ms';
        wr.append(i); frag.append(wr);
      });
      n.replaceWith(frag);
    });
    split(h);
  });

  /* One observer for every reveal. Nothing is hidden until it is running. */
  const targets = $$('.rv, .stmt');
  if (reduced) targets.forEach(t => t.classList.add('in'));
  else {
    const io = new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
    }), { rootMargin: '0px 0px -12% 0px', threshold: 0.08 });
    targets.forEach(t => io.observe(t));
    document.documentElement.classList.add('rv-ready');
    const onScreen = () => targets.forEach(t => { if (t.getBoundingClientRect().top < innerHeight * 0.92) { t.classList.add('in'); io.unobserve(t); } });
    onScreen(); requestAnimationFrame(onScreen);
  }

  /* Nav glass, reading progress, active section, glass parallax. */
  const nav = $('nav.top'), bar = $('.progress'), links = $$('nav.top ul a'), pars = $$('[data-par]');
  pars.forEach(g => g.style.transform = `rotate(${g.dataset.rot}deg)`);
  let ticking = false;
  const onScroll = () => {
    if (ticking) return; ticking = true;
    requestAnimationFrame(() => {
      const y = scrollY, max = document.documentElement.scrollHeight - innerHeight;
      nav.classList.toggle('scrolled', y > 12);
      bar.style.transform = `scaleX(${max > 0 ? y / max : 0})`;
      let current = null;
      links.forEach(a => { const s = $(a.getAttribute('href')); if (s && s.getBoundingClientRect().top < innerHeight * 0.4) current = a; });
      links.forEach(a => a.classList.toggle('on', a === current));
      if (!reduced) pars.forEach(g => {
        const top = g.parentElement.getBoundingClientRect().top;
        g.style.transform = `translateY(${(top * parseFloat(g.dataset.par)).toFixed(1)}px) rotate(${g.dataset.rot}deg)`;
      });
      ticking = false;
    });
  };
  addEventListener('scroll', onScroll, { passive: true }); onScroll();

  /* Copy to clipboard from the pills and the terminal. */
  const copy = async (text, el) => {
    try { await navigator.clipboard.writeText(text); } catch { return false; }
    el.classList.add('copied'); setTimeout(() => el.classList.remove('copied'), 1400); return true;
  };
  $$('[data-copy]').forEach(b => b.addEventListener('click', () => copy(b.dataset.copy, b)));
  const copyAll = $('#copyAll'), term = $('#term'), termText = term.innerText;
  copyAll.addEventListener('click', async () => {
    const text = termText.split('\n').map(l => l.replace(/\s+#.*$/, '').trimEnd()).filter(l => l && !l.startsWith('#')).join('\n');
    if (await copy(text, copyAll)) { copyAll.textContent = 'Copied'; setTimeout(() => copyAll.textContent = 'Copy', 1400); }
  });

  /* Tiles: the glow follows the cursor. */
  $$('.tile').forEach(t => t.addEventListener('pointermove', e => {
    const r = t.getBoundingClientRect();
    t.style.setProperty('--mx', (e.clientX - r.left) + 'px'); t.style.setProperty('--my', (e.clientY - r.top) + 'px');
  }));

  /* Evidence numbers count to their value when they come into view. */
  const cio = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return; cio.unobserve(e.target);
    const el = e.target, to = +el.dataset.count, from = +(el.dataset.from ?? 0);
    if (reduced) return;
    const t0 = performance.now(), dur = 1400;
    const step = now => { const p = Math.min(1, (now - t0) / dur), k = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(from + (to - from) * k); if (p < 1) requestAnimationFrame(step); };
    el.textContent = from; requestAnimationFrame(step);
  }), { threshold: 0.6 });
  $$('[data-count]').forEach(c => cio.observe(c));

  /* Terminal types its commands the first time it is seen. */
  if (!reduced) {
    const html = term.innerHTML;
    const tio = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return; tio.disconnect();
      const tmp = document.createElement('div'); tmp.innerHTML = html;
      const parts = [...tmp.childNodes].map(n => ({ cls: n.nodeType === 1 ? n.className : '', text: n.textContent }));
      let pi = 0, ci = 0, built = '';
      const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
      const tick = () => {
        if (pi >= parts.length) { term.innerHTML = html; return; }
        const p = parts[pi]; ci++;
        const piece = esc(p.text.slice(0, ci)), wrap = s => p.cls ? `<span class="${p.cls}">${s}</span>` : s;
        term.innerHTML = built + wrap(piece) + '<span class="caret"></span>';
        if (ci >= p.text.length) { built += wrap(esc(p.text)); pi++; ci = 0; }
        setTimeout(tick, p.text.endsWith('\n') && ci === 0 ? 140 : 14);
      };
      term.innerHTML = '<span class="caret"></span>'; setTimeout(tick, 350);
    }, { threshold: 0.5 });
    tio.observe(term);
  }

  /* Hero lattice: rounded cells shift from told (teal) to now (lime) as a
     change front sweeps across; the cursor brings nearby cells forward. */
  (() => {
    const cv = $('#lattice'), ctx = cv.getContext('2d'), CELL = 30;
    let w = 0, h = 0, cells = [], raf = 0, mx = -1e4, my = -1e4;
    const build = () => {
      const r = cv.getBoundingClientRect(), d = dpr(); w = r.width; h = r.height;
      cv.width = w * d; cv.height = h * d; ctx.setTransform(d, 0, 0, d, 0, 0); cells = [];
      for (let x = CELL / 2; x < w; x += CELL) for (let y = CELL / 2; y < h; y += CELL) {
        const nx = x / w, ny = y / h;
        const edge = Math.max(Math.pow(Math.abs(nx - 0.5) * 2, 1.6), 1 - ny * 1.5);
        const n = Math.sin(x * 12.9898 + y * 78.233) * 43758.5453, rnd = n - Math.floor(n);
        const a = edge * (0.35 + rnd * 0.65);
        if (a > 0.08) cells.push({ x, y, a: Math.min(1, a), ph: rnd * 6.283, d: Math.hypot(nx - 0.1, ny - 0.05) });
      }
    };
    const draw = t => {
      ctx.clearRect(0, 0, w, h);
      for (const c of cells) {
        const wave = reduced ? 0.3 : 0.5 + 0.5 * Math.sin(t * 0.0006 - c.d * 7);
        const near = Math.max(0, 1 - Math.hypot(c.x - mx, c.y - my) / 160);
        const a = c.a * (reduced ? 0.6 : 0.42 + 0.3 * Math.sin(t * 0.0009 + c.ph)) + near * 0.55;
        if (a < 0.04) continue;
        const s = 6 + near * 5;
        const r = Math.round(142 * wave), g = Math.round(179 + 41 * wave), b = Math.round(181 - 91 * wave);
        ctx.fillStyle = `rgba(${r},${g},${b},${(a * 0.5).toFixed(3)})`;
        ctx.beginPath(); ctx.roundRect(c.x - s / 2, c.y - s / 2, s, s, s * 0.3); ctx.fill();
        if (near > 0.25) { ctx.strokeStyle = `rgba(255,255,255,${near.toFixed(2)})`; ctx.lineWidth = 1; ctx.stroke(); }
      }
    };
    const loop = t => { draw(t); raf = requestAnimationFrame(loop); };
    build(); draw(0);
    addEventListener('resize', () => { build(); draw(performance.now()); }, { passive: true });
    const host = cv.parentElement;
    host.addEventListener('pointermove', e => { const r = cv.getBoundingClientRect(); mx = e.clientX - r.left; my = e.clientY - r.top; if (reduced) draw(0); });
    host.addEventListener('pointerleave', () => { mx = my = -1e4; if (reduced) draw(0); });
    if (!reduced) whenVisible(cv, () => { if (!raf) raf = requestAnimationFrame(loop); }, () => { cancelAnimationFrame(raf); raf = 0; });
  })();

  /* Hero demo: the line flips, the session card wakes, the correction types
     in. It loops while visible and rests on the finished state otherwise. */
  (() => {
    const demo = $('#demo'), out = $('#typed'), chip = $('#heldChip'), finalHTML = out.innerHTML;
    if (reduced) { demo.classList.add('s1', 's2', 's3'); return; }
    // Hold the finished height so the cards never move while the text types in.
    const hold = () => { out.style.minHeight = ''; out.innerHTML = finalHTML; out.style.minHeight = out.offsetHeight + 'px'; };
    hold(); addEventListener('resize', hold, { passive: true });
    const tmp = document.createElement('div'); tmp.innerHTML = finalHTML;
    const parts = [...tmp.childNodes].map(n => ({ cls: n.nodeType === 1 ? n.className : '', text: n.textContent }));
    let timers = [], running = false;
    const at = (ms, fn) => timers.push(setTimeout(fn, ms));
    const clear = () => { timers.forEach(clearTimeout); timers = []; };
    const run = () => {
      clear(); demo.classList.add('armed'); demo.classList.remove('s1', 's2', 's3');
      out.innerHTML = '<span class="caret"></span>';
      at(900, () => demo.classList.add('s1'));
      at(1700, () => demo.classList.add('s2'));
      at(2500, () => { demo.classList.add('s3'); chip.classList.remove('pulse'); void chip.offsetWidth; chip.classList.add('pulse'); });
      let ms = 2900, built = '';
      parts.forEach(p => {
        const wrap = s => p.cls ? `<span class="${p.cls}">${s}</span>` : s;
        for (let k = 1; k <= p.text.length; k++) { const s = p.text.slice(0, k); at(ms += 15, () => { out.innerHTML = built + wrap(s) + '<span class="caret"></span>'; }); }
        at(ms += 160, () => { built += wrap(p.text); });
      });
      at(ms + 200, () => { out.innerHTML = finalHTML; });
      at(ms + 6000, () => running && run());
    };
    whenVisible(demo, () => { if (!running) { running = true; run(); } },
      () => { running = false; clear(); demo.classList.add('s1', 's2', 's3'); out.innerHTML = finalHTML; });
  })();


  /* The mark is the model: told (teal) and now (lime) drift apart when the
     evidence moves, and memex brings them back so the white overlap holds. */
  (() => {
    const steps = $$('#steps button'), tag = $('#vtag'), CYCLE = 4200;
    const el = id => document.getElementById(id);
    const told = el('vtold'), now = el('vnow'), hold = el('vhold'), clip = el('vclipr'), tl = el('vtl'), nl = el('vnl');
    const S = [
      { t: [110, 210], n: [250, 110], tag: 'c-contract@r1 · supported', warn: false },
      { t: [110, 210], n: [356, 40], tag: 'evidence moved · needs_revalidation', warn: true },
      { t: [216, 140], n: [356, 40], tag: 'agent re-read directory.py · holds again', warn: false },
    ];
    let cur = { t: [...S[0].t], n: [...S[0].n] }, idx = 0, manual = false, timer = 0, raf = 0, visible = false;
    $('#steps').style.setProperty('--cycle', CYCLE + 'ms');
    const paint = () => {
      const [tx, ty] = cur.t, [nx, ny] = cur.n;
      told.setAttribute('x', tx); told.setAttribute('y', ty); hold.setAttribute('x', tx); hold.setAttribute('y', ty);
      now.setAttribute('x', nx); now.setAttribute('y', ny); clip.setAttribute('x', nx); clip.setAttribute('y', ny);
      tl.setAttribute('x', tx + 6); tl.setAttribute('y', ty + 292); nl.setAttribute('x', nx + 254); nl.setAttribute('y', ny - 16);
    };
    const tween = (to) => {
      cancelAnimationFrame(raf);
      if (reduced) { cur = { t: [...to.t], n: [...to.n] }; return paint(); }
      const from = { t: [...cur.t], n: [...cur.n] }, t0 = performance.now(), dur = 1100;
      const step = now => { const p = Math.min(1, (now - t0) / dur), k = 1 - Math.pow(2, -10 * p);
        cur.t = from.t.map((v, i) => v + (to.t[i] - v) * k); cur.n = from.n.map((v, i) => v + (to.n[i] - v) * k);
        paint(); if (p < 1) raf = requestAnimationFrame(step); };
      raf = requestAnimationFrame(step);
    };
    const schedule = () => { clearTimeout(timer); if (!manual && !reduced && visible) timer = setTimeout(() => go((idx + 1) % S.length), CYCLE); };
    const go = (i, byHand = false) => {
      if (byHand) manual = true; idx = i;
      steps.forEach((b, k) => { b.classList.toggle('on', k === i); b.classList.toggle('manual', manual && k === i); b.setAttribute('aria-pressed', k === i);
        if (k === i) { b.classList.remove('on'); void b.offsetWidth; b.classList.add('on'); } });
      tag.textContent = S[i].tag; tag.classList.toggle('warn', S[i].warn);
      tween(S[i]); schedule();
    };
    steps.forEach((b, i) => b.addEventListener('click', () => go(i, true)));
    paint(); go(0);
    whenVisible($('.model'), () => { visible = true; schedule(); }, () => { visible = false; clearTimeout(timer); });
  })();

  /* Pipeline: a control plane with pulses travelling between stages. It
     cycles on its own until the reader picks a stage. */
  (() => {
    const STAGES = [
      { n: '01', t: 'Capture', p: 'memex/runtime/views.py', c: 'Two matching listings and reads of every permitted source, with HEAD, or no view at all. Drift during capture is rejected, not averaged.', a: 'EMITS → stable source capture' },
      { n: '02', t: 'Index', p: 'memex/runtime/coordinator.py', c: 'Each capture becomes an immutable repository view. The graph commit precedes the local acknowledgement, and an already published view is never rewritten.', a: 'EMITS → RepositoryView, structure' },
      { n: '03', t: 'Verify', p: 'memex/runtime/verification.py', c: 'Every claim is checked against the evidence it cites, at this view: hashes, symbols, tests, approvals and supersession. Deterministic, with no model in the loop.', a: 'DERIVES → supported · needs_revalidation · unsupported' },
      { n: '04', t: 'Deliver', p: 'memex/integrations/host_adapter.py', c: "A bounded packet goes into the session, marked so it can be found again. It counts as delivered only when the client's own transcript shows it arrived whole.", a: 'HOLDS → what each session was told' },
      { n: '05', t: 'Check', p: 'memex/runtime/actions.py', c: 'Before a declared edit, its targets and the evidence behind delivered claims are rechecked against the current view, through a long-lived hook service.', a: 'DECIDES → proceed · replan · unknown' },
      { n: '06', t: 'Correct', p: 'memex/integrations/host_adapter.py', c: 'An affected edit is held, and the correction names what changed and what to re-read. If memex cannot check, the edit proceeds and the agent is told so.', a: 'EMITS → correction, change notice' },
    ];
    const legend = $('#legend'), cap = $('#caption'), cv = $('#planeCv'), ctx = cv.getContext('2d'), CYCLE = 5000;
    legend.style.setProperty('--cycle', CYCLE + 'ms');
    let active = 0, manual = false, w = 0, h = 0, nodes = [], pulses = [], raf = 0, last = 0, timer = 0, visible = false;
    STAGES.forEach((s, i) => {
      const li = document.createElement('li'), b = document.createElement('button');
      b.type = 'button'; b.innerHTML = `<span class="n">${s.n}</span><span class="t">${s.t}</span>`;
      b.addEventListener('click', () => select(i, true)); li.append(b); legend.append(li);
    });
    const buttons = $$('button', legend);
    const schedule = () => { clearTimeout(timer); if (!manual && !reduced && visible) timer = setTimeout(() => select((active + 1) % STAGES.length), CYCLE); };
    const select = (i, byHand = false) => {
      if (byHand) manual = true;
      active = i;
      buttons.forEach((b, k) => {
        b.classList.toggle('on', k === i); b.classList.toggle('manual', manual && k === i);
        b.setAttribute('aria-pressed', k === i);
        if (k === i) { b.classList.remove('on'); void b.offsetWidth; b.classList.add('on'); }
      });
      const s = STAGES[i];
      const fill = () => { $('.path', cap).textContent = s.p; $('p', cap).textContent = s.c; $('.art', cap).textContent = s.a; cap.classList.remove('swap'); };
      if (reduced) fill(); else { cap.classList.add('swap'); setTimeout(fill, 220); }
      if (i > 0) pulses.push({ i: i - 1, p: 0 });
      schedule(); if (reduced) draw();
    };
    const layout = () => {
      const r = cv.getBoundingClientRect(), d = dpr(); w = r.width; h = r.height;
      cv.width = w * d; cv.height = h * d; ctx.setTransform(d, 0, 0, d, 0, 0);
      const pad = Math.min(70, w * 0.08), span = w - pad * 2;
      nodes = STAGES.map((_, i) => ({ x: pad + span * i / (STAGES.length - 1), y: h / 2 + (i % 2 ? 1 : -1) * h * 0.16, lit: i === 0 ? 1 : 0 }));
    };
    const route = (a, b) => { const m = (a.x + b.x) / 2; return [[a.x, a.y], [m, a.y], [m, b.y], [b.x, b.y]]; };
    const along = (pts, p) => {
      const seg = []; let total = 0;
      for (let k = 0; k < pts.length - 1; k++) { const l = Math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]); seg.push(l); total += l; }
      let d = p * total;
      for (let k = 0; k < seg.length; k++) { if (d <= seg[k]) { const q = d / (seg[k] || 1); return [pts[k][0] + (pts[k + 1][0] - pts[k][0]) * q, pts[k][1] + (pts[k + 1][1] - pts[k][1]) * q]; } d -= seg[k]; }
      return pts[pts.length - 1];
    };
    const draw = () => {
      ctx.clearRect(0, 0, w, h); const dk = isDark();
      ctx.strokeStyle = dk ? 'rgba(255,255,255,0.06)' : 'rgba(17,20,23,0.05)'; ctx.lineWidth = 1; ctx.beginPath();
      for (let x = 0; x < w; x += 34) { ctx.moveTo(x + .5, 0); ctx.lineTo(x + .5, h); }
      for (let y = 0; y < h; y += 34) { ctx.moveTo(0, y + .5); ctx.lineTo(w, y + .5); }
      ctx.stroke();
      nodes.forEach((n, i) => n.lit += ((i <= active ? 1 : 0) - n.lit) * (reduced ? 1 : 0.08));
      for (let i = 0; i < nodes.length - 1; i++) {
        const lit = Math.min(nodes[i].lit, nodes[i + 1].lit), pts = route(nodes[i], nodes[i + 1]);
        ctx.strokeStyle = `rgba(${dk ? '0,179,181' : '0,140,142'},${(0.16 + lit * 0.5).toFixed(3)})`; ctx.lineWidth = lit > .5 ? 1.6 : 1;
        ctx.beginPath(); pts.forEach(([x, y], k) => k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke();
      }
      pulses = pulses.filter(p => p.p <= 1);
      for (const p of pulses) {
        const a = nodes[p.i], b = nodes[p.i + 1]; if (!a || !b) continue;
        const [x, y] = along(route(a, b), p.p);
        const g = ctx.createRadialGradient(x, y, 0, x, y, 16);
        g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(.35, 'rgba(142,220,90,.75)'); g.addColorStop(1, 'rgba(0,179,181,0)');
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 16, 0, 6.283); ctx.fill();
      }
      nodes.forEach((n, i) => {
        const on = i === active, s = on ? 28 : 20, lit = n.lit > .5;
        ctx.save(); ctx.translate(n.x, n.y);
        if (on) { ctx.fillStyle = 'rgba(0,179,181,0.10)'; ctx.beginPath(); ctx.roundRect(-s - 4, -s - 4, s * 2 + 8, s * 2 + 8, s * 0.7); ctx.fill(); }
        const gt = ctx.createLinearGradient(-s, -s, s, s);
        gt.addColorStop(0, lit ? '#7FDDE0' : (dk ? '#2B353B' : '#E3E8EA')); gt.addColorStop(1, lit ? '#00B3B5' : (dk ? '#1B2328' : '#C9D0D3'));
        ctx.fillStyle = gt; ctx.beginPath(); ctx.roundRect(-s / 2 - 4, -s / 2 + 4, s, s, s * 0.3); ctx.fill();
        if (lit) {
          const gl = ctx.createLinearGradient(-s, -s, s, s); gl.addColorStop(0, '#E6F8C8'); gl.addColorStop(1, '#8EDC5A');
          ctx.fillStyle = gl; ctx.beginPath(); ctx.roundRect(-s / 2 + 4, -s / 2 - 4, s, s, s * 0.3); ctx.fill();
          ctx.save(); ctx.beginPath(); ctx.roundRect(-s / 2 + 4, -s / 2 - 4, s, s, s * 0.3); ctx.clip();
          ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.roundRect(-s / 2 - 4, -s / 2 + 4, s, s, s * 0.3); ctx.fill(); ctx.restore();
        }
        ctx.restore();
        ctx.font = '500 11px ' + getComputedStyle(document.body).getPropertyValue('--f-mono'); ctx.fillStyle = on ? (dk ? '#7FDDE0' : '#057375') : (dk ? '#6C767C' : '#8A9399'); ctx.textAlign = 'center';
        ctx.fillText(STAGES[i].t.toUpperCase(), n.x, n.y + (i % 2 ? 42 : -32));
      });
    };
    const loop = t => {
      const dt = Math.min(64, t - (last || t)); last = t;
      pulses.forEach(p => p.p += dt / 1100);
      if (Math.random() < 0.012) pulses.push({ i: Math.floor(Math.random() * (nodes.length - 1)), p: 0 });
      draw(); raf = requestAnimationFrame(loop);
    };
    layout(); select(0); draw();
    addEventListener('resize', () => { layout(); draw(); }, { passive: true });
    addEventListener('themechange', () => draw());
    document.fonts?.ready.then(draw);
    whenVisible(cv, () => { visible = true; schedule(); if (!reduced && !raf) { last = 0; raf = requestAnimationFrame(loop); } },
      () => { visible = false; clearTimeout(timer); cancelAnimationFrame(raf); raf = 0; });
  })();
})();
