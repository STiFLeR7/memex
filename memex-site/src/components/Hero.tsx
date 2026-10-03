import { Fragment } from 'react';
import HeroMatrix from './HeroMatrix';
import Statement from './Statement';

const FACTS = [
  { v: '14', k: 'registered MCP tools in the current server surface' },
  { v: 'Neo4j\n+ Graphiti', k: 'graph storage and search boundary' },
  { v: '3', k: 'decay regimes computed at read time, never stored' },
] as const;

export default function Hero() {
  return (
    <section className="hero" id="top">
      <HeroMatrix />
      <div className="hero-vig" aria-hidden="true" />
      <div className="glow glow-floor breathe" aria-hidden="true" />

      <div className="edge wrap hero-inner">
        <p className="tech hero-eyebrow rv">
          Engineering context infrastructure <span className="sep">/</span> v0.9.0
        </p>

        <Statement
          as="h1"
          parts={[
            { t: 'A knowledge graph for the parts of engineering work' },
            { t: 'files do not explain.', em: true },
          ]}
        />

        <div className="hero-foot">
          <div>
            <p className="lede rv d2">
              memex watches a repository, extracts its structure, and stores
              engineering decisions in a bitemporal graph where facts expire
              rather than disappear. Agents receive bounded,
              provenance-carrying context — or nothing at all.
            </p>
            <div className="hero-cta rv d3">
              <a className="btn primary" href="#plane">
                Trace the system <span className="ar">→</span>
              </a>
              <a className="btn" href="#artifacts">
                Inspect the surface
              </a>
            </div>
          </div>

          <div className="hero-facts rv d4">
            {FACTS.map((f) => (
              <div key={f.k}>
                <b>
                  {f.v.split('\n').map((line, i) => (
                    <Fragment key={i}>
                      {i > 0 && <br />}
                      {line}
                    </Fragment>
                  ))}
                </b>
                <span>{f.k}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="scroll-cue" aria-hidden="true">
        <i />
        Scroll
      </div>
    </section>
  );
}
