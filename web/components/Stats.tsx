import type { ReactNode } from 'react'
import styles from './Stats.module.css'

/*
 * A row of measured numbers.
 *
 * Used only where a figure is one the project has actually produced and can
 * point at -- the test count, the share of postings the pre-filter drops.
 * Deliberately plain: no animation, no trend arrows, nothing that would
 * dress a measurement up as a marketing claim. Every Stat carries a `note`
 * saying where its number came from.
 */

export function Stats({ children }: { children: ReactNode }) {
  return <div className={styles.row}>{children}</div>
}

export function Stat({
  value,
  label,
  note,
  tone = 'neutral',
}: {
  value: ReactNode
  label: ReactNode
  /** Where the number comes from. */
  note?: ReactNode
  tone?: 'neutral' | 'agree' | 'notice'
}) {
  return (
    <div className={styles.stat} data-tone={tone}>
      <div className={styles.value}>{value}</div>
      <div className={styles.label}>{label}</div>
      {note ? <div className={styles.note}>{note}</div> : null}
    </div>
  )
}

export default Stats
