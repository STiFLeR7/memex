import { Fragment, type ElementType, type ReactNode } from 'react';

export type Part = {
  readonly t: string;
  /** render in the italic serif accent face */
  readonly em?: boolean;
  /** force a line break before this part */
  readonly block?: boolean;
};

type Props = {
  as?: ElementType;
  parts: readonly Part[];
  className?: string;
  id?: string;
};

/**
 * Word-split headline for the staggered reveal.
 *
 * Splitting happens at render, not at runtime — the markup ships correct, so
 * the heading is readable with JS disabled and there is no layout thrash.
 *
 * The whitespace between words MUST be a sibling text node, never a child of
 * `.wr`: that span is `overflow:hidden` + `inline-block`, so a space placed
 * inside it is clipped and the words run together.
 */
export default function Statement({
  as: Tag = 'h2',
  parts,
  className = '',
  id,
}: Props) {
  let seq = 0;

  const renderWords = (text: string): ReactNode[] => {
    const words = text.split(/\s+/).filter(Boolean);
    return words.map((w, i) => (
      <Fragment key={`${w}-${i}`}>
        <span className="wr">
          <i style={{ transitionDelay: `${seq++ * 44}ms` }}>{w}</i>
        </span>
        {i < words.length - 1 ? ' ' : null}
      </Fragment>
    ));
  };

  return (
    <Tag id={id} className={`stmt ${className}`.trim()}>
      {parts.map((part, pi) => {
        const words = renderWords(part.t);
        const needsLeadingSpace = pi > 0 && !part.block;

        if (part.block) {
          return (
            <span className="l2" key={pi}>
              {words}
            </span>
          );
        }
        return (
          <Fragment key={pi}>
            {needsLeadingSpace ? ' ' : null}
            {part.em ? <em className="ser">{words}</em> : words}
          </Fragment>
        );
      })}
    </Tag>
  );
}
