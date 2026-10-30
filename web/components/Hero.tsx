import Link from 'next/link'
import type { ReactNode } from 'react'
import styles from './Hero.module.css'
import { Masthead } from './Masthead'

/*
 * The landing hero.
 *
 * It does not open with a feature list. The headline states the problem in
 * one line, and the first thing below the fold is karani declining to do
 * the last step: the submission console, with the pen handed back.
 * Everything else on the page is evidence for that.
 */

export function Hero({
  masthead,
  eyebrow,
  title,
  children,
  actions,
}: {
  /** Draw the journal masthead above the eyebrow. Only the landing page does. */
  masthead?: boolean
  eyebrow?: ReactNode
  title: ReactNode
  children: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className={styles.hero}>
      <div className={styles.atmosphere} aria-hidden="true">
        <div className="ka-aurora" />
      </div>
      {masthead ? <Masthead /> : null}
      {eyebrow ? <p className={styles.eyebrow}>{eyebrow}</p> : null}
      <h1 className={styles.title}>{title}</h1>
      <div className={styles.lede}>{children}</div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </header>
  )
}

export function Action({
  href,
  children,
  primary,
}: {
  href: string
  children: ReactNode
  primary?: boolean
}) {
  const external = href.startsWith('http')
  const className = primary ? styles.primary : styles.secondary
  if (external) {
    return (
      <a className={className} href={href} rel="noreferrer">
        {children}
      </a>
    )
  }
  return (
    <Link className={className} href={href}>
      {children}
    </Link>
  )
}

/** A labelled band, for sectioning the landing page without an <h2> rule. */
export function Band({
  label,
  title,
  children,
}: {
  label: string
  title: ReactNode
  children: ReactNode
}) {
  return (
    <section className={styles.band}>
      <p className={styles.bandLabel}>{label}</p>
      <h2 className={styles.bandTitle}>{title}</h2>
      {children}
    </section>
  )
}

export default Hero
