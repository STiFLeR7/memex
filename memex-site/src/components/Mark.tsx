/**
 * The memex mark.
 *
 * Two squares offset on a diagonal, meeting at a corner: the same assertion
 * at two points in time. The hollow one is the predecessor — expired, still
 * on record. The solid one is its successor, current. The shared corner is
 * the supersession link.
 *
 * That is the system's central claim (stored ≠ current) reduced to two
 * shapes. Geometric, monochrome, inherits currentColor, legible at 16px.
 */
export default function Mark({
  size = 22,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      role="img"
      aria-label="memex"
    >
      {/* predecessor — expired, retained on the record */}
      <rect
        x="2.9"
        y="12.1"
        width="9.6"
        height="9.6"
        stroke="currentColor"
        strokeWidth="1.6"
        opacity="0.5"
      />
      {/* successor — current. meets the predecessor at a single corner */}
      <rect x="12.5" y="2.5" width="9.6" height="9.6" fill="currentColor" />
    </svg>
  );
}
