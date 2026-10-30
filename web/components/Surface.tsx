import Link from 'next/link'
import type { ReactNode } from 'react'
import styles from './Surface.module.css'

/*
 * A link to one of karani's surfaces.
 *
 * karani has five faces onto the same pipeline -- a CLI, an MCP server, a
 * Slack bridge, a static submission console and a Gmail watcher -- and they
 * are thin adapters over the same storage and runners, never separate
 * implementations. Each card names the surface, shows the one thing you type
 * at it, and says what it is for. People arrive wanting a surface, not a
 * feature.
 */

export function SurfaceGrid({ children }: { children: ReactNode }) {
  return <div className={styles.grid}>{children}</div>
}

export function Surface({
  href,
  name,
  invocation,
  children,
}: {
  href: string
  /** What the surface is called. */
  name: string
  /** The one thing you type at it. */
  invocation: string
  children: ReactNode
}) {
  return (
    <Link href={href} className={styles.card}>
      <code className={styles.invocation}>{invocation}</code>
      <span className={styles.name}>{name}</span>
      <span className={styles.body}>{children}</span>
    </Link>
  )
}

export default Surface
