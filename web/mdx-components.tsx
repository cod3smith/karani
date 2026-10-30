import { useMDXComponents as getThemeComponents } from 'nextra-theme-docs'
import { Note } from './components/Note'
import { RunReport } from './components/RunReport'
import { Stat, Stats } from './components/Stats'
import { Surface, SurfaceGrid } from './components/Surface'
import { Verdict } from './components/Verdict'
import { Lede } from './components/Lede'
import { Action, Band, Hero } from './components/Hero'

const themeComponents = getThemeComponents()

export function useMDXComponents(components?: Record<string, unknown>) {
  return {
    ...themeComponents,
    Verdict,
    RunReport,
    Note,
    Stat,
    Stats,
    Surface,
    SurfaceGrid,
    Lede,
    Hero,
    Action,
    Band,
    ...components,
  }
}
