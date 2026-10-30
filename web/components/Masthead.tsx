import styles from './Masthead.module.css'

/*
 * The record's masthead: volume, place and year in one letterspaced row,
 * with a hairline beneath. A clerk's ledger opens with its own reference,
 * and so does this.
 *
 * It is the muted `.ka-mono` voice, not the brand-coloured eyebrow: the
 * masthead is a quiet row and the blue belongs to the hero's status line
 * beneath it. Hero draws it only when asked to, which only the landing page
 * does; every other page is a numbered entry and has no cover.
 *
 * MMXXVI is 2026, the year of the 0.4 line.
 *
 * The text is written in capitals because `.ka-mono` uppercases it only for
 * the eye: check-assets reads the HTML, and a check that matched on a CSS
 * transform would be matching on something the markup does not say.
 */
export function Masthead() {
  return (
    <div className={`ka-mono ${styles.masthead}`} data-masthead>
      <span>VOL. 01 - THE CLERK</span>
      <span className={styles.place}>NAIROBI, EAT (UTC+3)</span>
      <span>MMXXVI</span>
    </div>
  )
}

export default Masthead
