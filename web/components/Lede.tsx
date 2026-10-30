import type { ReactNode } from 'react'
import styles from './Lede.module.css'

/** The opening paragraph of a page, set a size up. */
export function Lede({ children }: { children: ReactNode }) {
  return <div className={styles.lede}>{children}</div>
}

export default Lede
