import type { ReactNode } from 'react'
import styles from './RunReport.module.css'

/*
 * A transcript of a karani command.
 *
 * The value of a hunt pass is in one word per line -- fetched, dropped,
 * qualified, drafted, skipped -- so that word gets its own column and its
 * own colour, and the counts sit right-aligned where they can be compared
 * down the column instead of read as prose.
 *
 * The text is passed through verbatim; this component only adds typography.
 * Anything the parser does not recognise falls through to a plain monospace
 * line, so an unfamiliar transcript degrades to a well-set code block rather
 * than to mangled output.
 *
 * Every transcript on this site is from a run over FABRICATED postings and a
 * FABRICATED candidate. scripts/check-synthetic.mjs is the gate.
 */

const STATUSES = [
  'fetched',
  'dropped',
  'qualified',
  'drafted',
  'skipped',
  'delivered',
  'flagged',
  'failed',
] as const
type Status = (typeof STATUSES)[number]

const ROW = new RegExp(`^(${STATUSES.join('|')})\\s+(.*?)(?:\\s{2,}(\\S+))?\\s*$`)
const SUMMARY = /^(?:pass|run) \w+:/i
const COMMAND = /^\$\s+(.*)$/
const HEADING = /^(hunting|qualifying|drafting|reading|watching|checking)\b/

type Line =
  | { kind: 'command'; text: string }
  | { kind: 'heading'; text: string }
  | { kind: 'row'; status: Status; subject: string; count?: string }
  | { kind: 'summary'; text: string }
  | { kind: 'plain'; text: string }

function parse(raw: string): Line[] {
  return raw.split('\n').map((line): Line => {
    const command = COMMAND.exec(line)
    if (command) return { kind: 'command', text: command[1] }
    if (SUMMARY.test(line.trim())) return { kind: 'summary', text: line.trim() }
    if (HEADING.test(line.trim())) return { kind: 'heading', text: line.trim() }
    const row = ROW.exec(line)
    if (row) {
      return {
        kind: 'row',
        status: row[1] as Status,
        subject: row[2],
        ...(row[3] ? { count: row[3] } : {}),
      }
    }
    return { kind: 'plain', text: line }
  })
}

/** Render backticked spans inside a row's subject as code. */
function subject(text: string) {
  return text.split(/(`[^`]+`)/).map((part, i) =>
    part.startsWith('`') && part.endsWith('`') ? (
      <code key={i} className={styles.name}>
        {part.slice(1, -1)}
      </code>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}

export type RunReportProps = {
  /** The command's output, verbatim. */
  children: string
  /** What produced it, shown at the right of the plate's bar. */
  note?: ReactNode
  /**
   * Number the plate as a figure, `FIG. NN`, counted in CSS and shared with
   * `Verdict` so the two never number against each other. Defaults to true.
   */
  figure?: boolean
}

export function RunReport({ children, note, figure = true }: RunReportProps) {
  const raw = typeof children === 'string' ? children.replace(/\n+$/, '') : ''
  const lines = parse(raw)

  return (
    <div className={styles.panel} role="group" aria-label="Command transcript">
      {figure || note ? (
        <div className={styles.bar}>
          {figure ? <span className="ka-figure" aria-hidden="true" /> : null}
          {note ? <span className={styles.note}>{note}</span> : null}
        </div>
      ) : null}
      <div className={styles.body}>
        {lines.map((line, i) => {
          switch (line.kind) {
            case 'command':
              return (
                <div key={i} className={styles.command}>
                  <span aria-hidden="true" className={styles.prompt}>
                    $
                  </span>
                  <span>{line.text}</span>
                </div>
              )
            case 'heading':
              return (
                <div key={i} className={styles.heading}>
                  {line.text}
                </div>
              )
            case 'row':
              return (
                <div key={i} className={styles.row} data-status={line.status}>
                  <span className={styles.status}>{line.status}</span>
                  <span className={styles.subject}>{subject(line.subject)}</span>
                  {line.count ? <span className={styles.count}>{line.count}</span> : null}
                </div>
              )
            case 'summary':
              return (
                <div key={i} className={styles.summary}>
                  {line.text}
                </div>
              )
            default:
              return (
                <div key={i} className={line.text.trim() ? styles.plain : styles.blank}>
                  {line.text || ' '}
                </div>
              )
          }
        })}
      </div>
    </div>
  )
}

export default RunReport
