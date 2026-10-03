import DecayRing from './DecayRing';
import Statement from './Statement';

const REPO = 'https://github.com/STiFLeR7/memex';

export default function Final() {
  return (
    <section className="final" id="close">
      <DecayRing />
      <div className="glow glow-core" aria-hidden="true" />

      <div className="edge wrap final-in">
        <p className="tech rv">Repository source of truth</p>

        <Statement
          parts={[
            { t: 'Inspect the graph' },
            { t: 'behind the claims.', sel: true },
          ]}
        />

        <p className="lede sub rv d2">
          Every number on this page traces to a file. The implementation, the
          evaluation record, and the list of things it deliberately does not do
          are all in the repository.
        </p>

        <div className="acts rv d3">
          <a className="btn primary" href={REPO}>
            Open the repository <span className="ar">→</span>
          </a>
          <a className="btn" href={`${REPO}/blob/master/BENCHMARK.md`}>
            Read the evaluation
          </a>
        </div>

        <p className="fine rv d4">
          MIT licensed · Python 3.11+ · runs on your own machine
        </p>
      </div>

      <p className="ring-note rv d4">
        Twelve reads of one unvalidated fact, five days apart. Each ring is
        drawn at its computed confidence, so the figure is the decay function,
        not a pattern chosen to sit behind the text.
      </p>
    </section>
  );
}
