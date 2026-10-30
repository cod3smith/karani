import type { ReactNode } from 'react'
import styles from './Verdict.module.css'

/*
 * A qualification result, rendered the way the shortlist reads it.
 *
 * This is karani's loudest output: the LLM tier returns a fit score, a
 * three-way verdict, and claims about the candidate that must each carry a
 * quote from the resume behind them. Rendering it as a plate rather than a
 * JSON dump is the point -- a reader should see at a glance that every
 * strength has evidence attached and every gap has a severity, because that
 * is the contract the prompt enforces (CLAUDE.md 4.13).
 *
 * The data is a real `QualificationResult` from karani's own pydantic model,
 * dumped at generate time by scripts/capture_samples.py from a FABRICATED
 * job and a FABRICATED candidate. Nothing here is from anyone's real resume
 * or any real posting -- scripts/check-synthetic.mjs is the gate that keeps
 * it that way.
 *
 * The verdict colour is the one piece of signal that carries across the
 * whole plate, so a reader scanning a page sees "qualified" before reading.
 */

export type VerdictData = {
  job: { id: number; company: string; title: string }
  fit_score: number
  verdict: 'qualified' | 'maybe' | 'skip'
  strengths: { claim: string; evidence_from_resume: string }[]
  gaps: { requirement: string; mitigation: string; severity: 'low' | 'medium' | 'high' }[]
  red_flags: string[]
  why_apply: string
  why_skip: string
  recommended_positioning: string
  model: string
  prompt_version: string
}

export type VerdictProps = {
  /** The result, from lib/samples.generated.ts. */
  children: VerdictData
  /**
   * What produced it, shown in the plate's top-right -- e.g.
   * `karani qualify`. Say what command ran, because the same shape comes
   * out of the CLI, the MCP tool and the hourly pass.
   */
  note?: ReactNode
  /**
   * Number the plate as a figure, `FIG. NN`, at the left of its bar. The
   * number is counted in CSS (`ka-figure`, reset per page), so nothing is
   * threaded through props and it can never disagree with the figures
   * around it. It sits beside `note` and never replaces it. Defaults to true.
   */
  figure?: boolean
}

export function Verdict({ children, note, figure = true }: VerdictProps) {
  const r = children

  return (
    <div className={styles.panel} data-verdict={r.verdict} role="group" aria-label="Qualification result">
      <div className={styles.bar}>
        {figure ? <span className="ka-figure" aria-hidden="true" /> : null}
        <span className={styles.barLeft}>
          <span className={styles.verdict}>{r.verdict}</span>
          <span className={styles.score} aria-label={`fit score ${r.fit_score} of 100`}>
            {r.fit_score}
            <span className={styles.outOf}>/100</span>
          </span>
          <span className={styles.job}>
            {r.job.title} <span className={styles.at}>at</span> {r.job.company}
          </span>
        </span>
        <span className={styles.barRight}>{note ? <span className={styles.note}>{note}</span> : null}</span>
      </div>

      <div className={styles.body}>
        {r.strengths.length > 0 ? (
          <section className={styles.block}>
            <h4 className={styles.heading}>Strengths</h4>
            <dl className={styles.claims}>
              {r.strengths.map((s, i) => (
                <div key={i} className={styles.claim}>
                  <dt>{s.claim}</dt>
                  <dd>{s.evidence_from_resume}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        {r.gaps.length > 0 ? (
          <section className={styles.block}>
            <h4 className={styles.heading}>Gaps</h4>
            <dl className={styles.claims}>
              {r.gaps.map((g, i) => (
                <div key={i} className={styles.claim} data-severity={g.severity}>
                  <dt>
                    <span className={styles.severity}>{g.severity}</span>
                    {g.requirement}
                  </dt>
                  {g.mitigation ? <dd>{g.mitigation}</dd> : null}
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        {r.red_flags.length > 0 ? (
          <section className={styles.block}>
            <h4 className={styles.heading}>Red flags</h4>
            <ul className={styles.flags}>
              {r.red_flags.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </section>
        ) : null}

        {r.recommended_positioning ? (
          <section className={styles.block}>
            <h4 className={styles.heading}>Recommended positioning</h4>
            <p className={styles.prose}>{r.recommended_positioning}</p>
          </section>
        ) : null}

        <p className={styles.reason}>
          <span aria-hidden="true" className={styles.arrow}>
            &rarr;
          </span>
          <span>{r.verdict === 'skip' ? r.why_skip : r.why_apply}</span>
        </p>
      </div>

      <div className={styles.foot}>
        <span>{r.model}</span>
        <span>{r.prompt_version}</span>
      </div>
    </div>
  )
}

export default Verdict
