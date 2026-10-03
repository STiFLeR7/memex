import Statement from './Statement';

const META = [
  { k: 'Owns', v: 'Repository facts, decisions, problems, relationships, temporal validity' },
  { k: 'Refuses', v: 'Session state, transcripts, prompts, tool results, user preferences' },
  { k: 'Failure mode', v: 'Fail-open — the agent continues with no context rather than bad context' },
  { k: 'Authority', v: 'Neo4j is the only authoritative state. Everything else is derived or recomputed' },
] as const;

export default function Thesis() {
  return (
    <section className="thesis" id="thesis">
      <div className="glow-top" aria-hidden="true" />
      <div className="edge wrap thesis-grid">
        <div className="thesis-state">
          <p className="tech rv" style={{ marginBottom: 'clamp(28px,4vh,54px)' }}>
            01 <span className="sep">/</span> The thesis
          </p>
          <Statement
            parts={[
              { t: 'Stored does not' },
              { t: 'mean current.', em: true },
              { t: 'Most memory systems cannot tell the difference.', block: true },
            ]}
          />
        </div>

        <div className="thesis-note rv d2">
          <p className="lede">
            A fact retrieved from a vector store arrives with no expiry, no
            supersession, and no account of where it came from. A decision
            reversed six weeks ago reads exactly like one made this morning —
            same fluency, same confidence, no signal.
          </p>
          <p className="lede">
            memex attaches time to every claim. Edges carry <code>created_at</code>{' '}
            and an optional <code>expired_at</code>. Invalidation expires; it
            never deletes. Confidence is recomputed on every read from three
            stored fields, so there is no number sitting in a row quietly
            drifting away from the truth.
          </p>
        </div>

        <div className="thesis-meta rv d3">
          <dl>
            {META.map((m) => (
              <div className="row" key={m.k}>
                <dt>{m.k}</dt>
                <dd>{m.v}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </section>
  );
}
