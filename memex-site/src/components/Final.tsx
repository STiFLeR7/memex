import Statement from './Statement';

const REPO = 'https://github.com/STiFLeR7/memex';

export default function Final() {
  return (
    <section className="final">
      <div className="glow glow-floor" aria-hidden="true" />
      <div className="edge wrap final-in">
        <p className="tech rv" style={{ marginBottom: 38 }}>
          Repository source of truth
        </p>
        <Statement
          parts={[
            { t: 'Inspect the graph' },
            { t: 'behind the claims.', em: true },
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

        <p className="contact rv d4">
          Want to know more, or need something?{' '}
          <a href="mailto:contact@stifler.in">contact@stifler.in</a>
        </p>
      </div>
    </section>
  );
}
