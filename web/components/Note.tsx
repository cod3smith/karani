import type { ReactNode } from 'react'
import styles from './Note.module.css'

/*
 * The callout, in four kinds. Warning, tip and caution map onto the refuse,
 * agree and notice signal colours; the default `note` is deliberately
 * neutral, so the most common callout on the site does not compete with
 * links and `drafted` lines for the brand colour.
 *
 * Titled by default: a callout worth drawing is a callout worth naming, and
 * the title is set as the first thing you read.
 */

export type NoteKind = 'note' | 'warning' | 'tip' | 'caution'

const LABEL: Record<NoteKind, string> = {
  note: 'Note',
  warning: 'Warning',
  tip: 'Tip',
  caution: 'Careful',
}

export function Note({
  kind = 'note',
  title,
  children,
}: {
  kind?: NoteKind
  title?: ReactNode
  children: ReactNode
}) {
  return (
    <aside className={styles.note} data-kind={kind}>
      <p className={styles.head}>
        <span className={styles.label}>{LABEL[kind]}</span>
        {title ? <span className={styles.title}>{title}</span> : null}
      </p>
      <div className={styles.body}>{children}</div>
    </aside>
  )
}

export default Note
