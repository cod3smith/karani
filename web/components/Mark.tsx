import styles from './Mark.module.css'

/*
 * The mark and the wordmark.
 *
 * karani is Swahili for clerk: the office worker who reads every paper,
 * files it, keeps the record, and hands it back for a signature. So the
 * mark is a filed sheet. A spine down the left edge, three ruled lines of
 * a record, and one blue stroke across the foot where the signature goes.
 * karani fills the sheet in; the blue line is the part it will not draw
 * for you, which is the whole product in one glyph (ADR 0012).
 *
 * The letterforms take currentColor and the stroke takes the brand token,
 * so the single-colour variant needs no second drawing. Nothing here
 * depends on a webfont: spine, rules and stroke are rectangles and a
 * polygon, so the glyph rasterises the same everywhere. This is the small
 * variant, the geometry of public/favicon.svg, because the glyph is drawn
 * at 24px and the display variant's 6-unit slabs would land at 1.4px
 * there. The rule: the small drawing below 32px, the display drawing
 * (public/icon.svg) at 32px and up.
 *
 * The wordmark is the whole word in ink. There is no italicised syllable,
 * because "karani" has no part that means something on its own — the blue
 * belongs to the glyph's stroke, where it says something.
 */
export function Mark() {
  return (
    <span className={styles.wrap}>
      <svg
        className={styles.glyph}
        width="24"
        height="24"
        viewBox="0 0 100 100"
        fill="none"
        aria-hidden="true"
      >
        <g fill="currentColor">
          <rect x="18" y="10" width="10" height="80" />
          <rect x="34" y="20" width="48" height="10" />
          <rect x="34" y="40" width="48" height="10" />
          <rect x="34" y="60" width="30" height="10" />
        </g>
        <polygon points="18,78 82,70 82,84 18,92" className={styles.stroke} />
      </svg>
      <span className={styles.word}>karani</span>
    </span>
  )
}

export default Mark
