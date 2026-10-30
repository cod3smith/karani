import styles from './Mark.module.css'

/*
 * The mark and the wordmark.
 *
 * karani is Swahili for clerk: the office worker who reads every paper,
 * files it, keeps the record, and hands it back for a signature. So the
 * mark is a filled-in list. A spine, then three bars descending in length,
 * and the shortest one accented — the line karani stopped at, which is
 * yours to sign. That last bar is the whole product in one glyph: karani
 * drafts everything and submits nothing (ADR 0012).
 *
 * The geometry is the brand kit's, unchanged
 * (assets/brand/karani-mark.svg). The colour is not: the kit draws it in
 * dark green with an orange accent, which is close to invisible on the
 * console's near-black ground and contradicts the palette the rest of this
 * site is built from. The bars take currentColor and the accent takes the
 * brand token, so the single-colour variant needs no second drawing.
 *
 * Nothing here depends on a webfont: the mark is four rounded rectangles,
 * so it rasterises the same everywhere. This is the small variant, the
 * geometry of public/favicon.svg, because the glyph is drawn at 24px and
 * the display variant's 9-unit bars would land at about three pixels
 * there. The rule: the small drawing below 32px, the display drawing
 * (public/icon.svg) at 32px and up.
 *
 * The wordmark is the whole word in ink. There is no accented syllable,
 * because "karani" has no part that means something on its own — the blue
 * belongs to the last bar, where it says something.
 */
export function Mark() {
  return (
    <span className={styles.wrap}>
      <svg
        className={styles.glyph}
        width="24"
        height="24"
        viewBox="0 0 64 64"
        fill="none"
        aria-hidden="true"
      >
        <g fill="currentColor">
          <rect x="8" y="6" width="12" height="52" rx="6" />
          <rect x="26" y="6" width="30" height="12" rx="6" />
          <rect x="26" y="26" width="22" height="12" rx="6" />
        </g>
        <rect x="26" y="46" width="12" height="12" rx="6" className={styles.accent} />
      </svg>
      <span className={styles.word}>karani</span>
    </span>
  )
}

export default Mark
