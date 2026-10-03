import { CAPABILITIES } from '@/lib/content';

export default function Capabilities() {
  return (
    <section className="caps" id="caps">
      <div className="edge wrap">
        <div className="caps-head">
          <p className="tech rv" style={{ marginBottom: 26 }}>
            03 <span className="sep">/</span> What the system does
          </p>
          <h2 className="rv d1" style={{ fontSize: 'var(--t-h2)', maxWidth: '20ch' }}>
            Four operations. Everything else is an adapter.
          </h2>
        </div>

        {CAPABILITIES.map((c) => (
          <article className="cap rv" key={c.num}>
            <div className="num">
              <b>{c.num}</b>
              <span>{c.key}</span>
            </div>
            <h3>
              {c.headline} <em className="ser">{c.emphasis}</em>
            </h3>
            <div className="d">
              {c.body}
              <code>{c.source}</code>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
