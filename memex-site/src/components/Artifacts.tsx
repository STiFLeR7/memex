'use client';

import { useState } from 'react';
import { TOOLS, TOOL_GROUPS } from '@/lib/content';

const EVIDENCE = [
  { b: '8/8', k: 'Paired runs', p: 'Valid baseline/treatment pairs in the v0.9 objective evaluation, with zero treatment regressions.' },
  { b: '0', k: 'Causal claims', p: 'The baseline also completed all eight cases. Non-regression is established; improvement is not.' },
  { b: '10', k: 'Node types', p: 'Repository, Module, Symbol, Decision, Problem, Dependency, Cluster, ClusterSummary, AgentSession, Principal.' },
  { b: 'MIT', k: 'Licence', p: 'Published on PyPI, npm and the MCP Registry. Python 3.11+. Local-first by default.' },
] as const;

const BOUNDARY = [
  { h: 'Not ordinary RAG', p: 'Results are constrained by repository scope and active validity. The graph carries typed entities, relation history, confidence, and write semantics — not embeddings alone.' },
  { h: 'Not generic chat memory', p: 'An MCP client must request context or invoke a write. memex makes no claim of automatic universal recall before every response.' },
  { h: 'Not a coding agent', p: 'It exposes information and governed graph writes. It does not own an editor and does not generate a patch.' },
  { h: 'Not a confidence oracle', p: 'Confidence is a ranking and disclosure signal. A high-confidence record can still be wrong. The site will not pretend otherwise.' },
] as const;

/* Grouped once at module scope — TOOLS is static, so this is free. */
const GROUPED = (['read', 'write', 'analytic'] as const).map((cls) => ({
  cls,
  label: TOOL_GROUPS[cls],
  tools: TOOLS.map((t, i) => ({ ...t, i })).filter((t) => t.cls === cls),
}));

export default function Artifacts() {
  const [sel, setSel] = useState(0);
  const tool = TOOLS[sel];

  return (
    <section className="art" id="artifacts">
      <div className="edge wrap">
        <div className="art-head">
          <div className="h">
            <p className="tech rv" style={{ marginBottom: 26 }}>
              05 <span className="sep">/</span> The surface
            </p>
            <h2 className="rv d1">
              Fourteen tools. Eight read, four write, two analytic.
            </h2>
          </div>
          <p className="note lede rv d2">
            Schemas below are the implemented interface. Illustrative
            projections — not live output from a running graph.
          </p>
        </div>

        <div className="surface rv d2">
          <div className="tool-list" role="tablist" aria-label="MCP tools">
            {GROUPED.map((g) => (
              <div key={g.cls}>
                <div className="grp">{g.label}</div>
                {g.tools.map((t) => (
                  <button
                    key={t.name}
                    role="tab"
                    aria-selected={t.i === sel}
                    onClick={() => setSel(t.i)}
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            ))}
          </div>

          <dl className="tool-detail">
            <div className="tool-row">
              <dt>Tool</dt>
              <dd><code>{tool.name}</code></dd>
            </div>
            <div className="tool-row">
              <dt>Class</dt>
              <dd>{tool.cls[0].toUpperCase() + tool.cls.slice(1)}</dd>
            </div>
            <div className="tool-row">
              <dt>Agent moment</dt>
              <dd>{tool.moment}</dd>
            </div>
            <div className="tool-row">
              <dt>Input</dt>
              <dd><code>{tool.input}</code></dd>
            </div>
            <div className="tool-row">
              <dt>Returns</dt>
              <dd>{tool.returns}</dd>
            </div>
            <div className="tool-row">
              <dt>Implemented</dt>
              <dd><code>memex/mcp_server/server.py</code></dd>
            </div>
          </dl>
        </div>

        <div className="evidence rv">
          {EVIDENCE.map((e) => (
            <div className="ev" key={e.k}>
              <b>{e.b}</b>
              <span className="k">{e.k}</span>
              <p>{e.p}</p>
            </div>
          ))}
        </div>

        <div className="boundary rv">
          {BOUNDARY.map((b) => (
            <div className="b-row" key={b.h}>
              <h3>
                <span className="x">—</span>
                {b.h}
              </h3>
              <p>{b.p}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
