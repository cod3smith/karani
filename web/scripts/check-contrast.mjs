/**
 * Fail the build when the design tokens stop meeting WCAG AA or when the signal
 * colours drift too close together on the colour wheel.
 *
 * The palette lives in app/tokens.css as `--ka-*` custom properties, once for
 * the light theme (`:root`) and once for the dark theme (`.dark`). Nothing
 * else in the site is allowed to pick a colour, so checking the tokens checks
 * the site. This reads the CSS itself rather than a copy of the values, so the
 * gate cannot go stale.
 *
 * Three things are asserted for each theme:
 *
 *   contrast    every colour used as normal-size text reaches 4.5:1 (WCAG 2.1
 *               AA) against every surface it can sit on, and each signal colour
 *               reaches 4.5:1 against its own -soft tint (the callout fill).
 *   separation  `brand` stays at least 25 degrees of hue away from `notice` and
 *               from `refuse`. A sky-blue brand has an easy time of this; the
 *               gate exists for the recolour that does not.
 *   brand       a few tokens are pinned to the submission console's own values
 *               (karani/submission/console.py), so the checker asserts the
 *               product's palette is actually present and not merely that the
 *               site is self-consistent. See REQUIRED.
 *
 * `rule` and `rule-strong` are exempt. They are hairlines, not text, and their
 * whole job is to be quiet. They are still parsed, and `contrast()` still
 * handles their alpha channel, because a translucent rule is a legitimate way
 * to write one.
 *
 * Usage:
 *   node scripts/check-contrast.mjs               check app/tokens.css
 *   node scripts/check-contrast.mjs path/to.css   check another file
 *   node scripts/check-contrast.mjs --self-test   verify the maths, then stop
 *   node scripts/check-contrast.mjs --orphans     check both themes declare the same colour (and theme-dependent) tokens
 *   node scripts/check-contrast.mjs --shiki       measure every syntax colour in the built HTML (run after a build)
 *
 * The self-test also runs before every real check: a checker whose arithmetic
 * is wrong would pass or fail the palette for the wrong reasons.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const TOKENS_CSS = fileURLToPath(new URL('../app/tokens.css', import.meta.url))
const BUILT_DIR = fileURLToPath(new URL('../out', import.meta.url))

/** Used as normal-size text, so all need 4.5:1. */
const TEXT_TOKENS = ['ink', 'ink-muted', 'ink-faint', 'brand', 'refuse', 'agree', 'notice']
const SURFACES = ['paper', 'paper-raised', 'paper-sunken']
const SIGNALS = ['brand', 'refuse', 'agree', 'notice']
const AA_TEXT = 4.5
/**
 * Non-colour tokens whose value embeds a theme-specific colour, so both blocks
 * must declare them. Add a token here when it is a shadow, gradient or border
 * shorthand built from a colour that differs between themes.
 */
const THEME_DEPENDENT = ['shadow-raised']
const MIN_SEPARATION = 25
const SEPARATED_FROM_BRAND = ['notice', 'refuse']

/**
 * Values the product fixes, asserted exactly (case-insensitive). These are the
 * submission console's own tokens: the dark paper, its card, its text and its
 * accent. The light-mode brand is deliberately not here — #7dd3fc is 1.6:1 on
 * white, so the light theme takes the same hue far darker, and the contrast
 * assertions police it instead.
 */
const REQUIRED = {
  dark: { paper: '#0b0d10', 'paper-raised': '#14171b', ink: '#e6e8ea', brand: '#7dd3fc' },
  light: { paper: '#f6f7f9', ink: '#0b0d10' },
}

// --- parsing ---------------------------------------------------------------

/** Declarations of `--ka-*` custom properties inside the block opened by `selector`. */
function declarationsOf(css, selector) {
  const opener = new RegExp(`(?:^|[}\\s,])${selector.replace('.', '\\.')}\\s*\\{`, 'm')
  const match = opener.exec(css)
  if (!match) return {}
  const start = match.index + match[0].length
  const end = css.indexOf('}', start)
  const body = css.slice(start, end === -1 ? undefined : end)
  const found = {}
  for (const decl of body.matchAll(/--ka-([a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    found[decl[1]] = decl[2].trim()
  }
  return found
}

/**
 * Read the light (`:root`) and dark (`.dark`) tokens out of the stylesheet.
 * Keys have the `--ka-` prefix removed. The dark theme is returned as written,
 * not merged over the light one, so a token it forgets to redefine is reported
 * rather than silently inherited.
 */
export function parseTokens(css) {
  const bare = css.replace(/\/\*[\s\S]*?\*\//g, '')
  return { light: declarationsOf(bare, ':root'), dark: declarationsOf(bare, '.dark') }
}

// --- colour maths ----------------------------------------------------------

/** `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa` or `rgb()/rgba()` -> channels 0-255 and alpha 0-1. */
export function parseColor(value) {
  const v = String(value).trim()
  const hex = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(v)
  if (hex) {
    let digits = hex[1]
    if (digits.length <= 4) digits = [...digits].map((d) => d + d).join('')
    const [r, g, b, a] = [0, 2, 4, 6].map((i) =>
      i < digits.length ? parseInt(digits.slice(i, i + 2), 16) : 255,
    )
    return { r, g, b, a: a / 255 }
  }
  const fn = /^rgba?\(\s*([^)]+)\)$/i.exec(v)
  if (fn) {
    const parts = fn[1].split(/[\s,/]+/).filter(Boolean)
    if (parts.length === 3 || parts.length === 4) {
      const channel = (p) => (p.endsWith('%') ? (parseFloat(p) / 100) * 255 : parseFloat(p))
      const [r, g, b] = parts.slice(0, 3).map(channel)
      const a = parts.length === 4
        ? parts[3].endsWith('%') ? parseFloat(parts[3]) / 100 : parseFloat(parts[3])
        : 1
      if ([r, g, b, a].every(Number.isFinite)) return { r, g, b, a }
    }
  }
  throw new Error(`not a colour: ${value}`)
}

/** sRGB channel, 0-1 -> linear light (WCAG 2.1 relative luminance). */
export function srgbToLinear(c) {
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function luminanceOf({ r, g, b }) {
  return (
    0.2126 * srgbToLinear(r / 255) +
    0.7152 * srgbToLinear(g / 255) +
    0.0722 * srgbToLinear(b / 255)
  )
}

/** Relative luminance of an opaque colour. Any alpha is ignored; see `contrast`. */
export function relativeLuminance(hex) {
  return luminanceOf(parseColor(hex))
}

/** `fg` laid over an opaque `bg`, blended per channel in sRGB as a browser does. */
function composite(fg, bg) {
  const mix = (f, b) => f * fg.a + b * (1 - fg.a)
  return { r: mix(fg.r, bg.r), g: mix(fg.g, bg.g), b: mix(fg.b, bg.b), a: 1 }
}

/**
 * WCAG 2.1 contrast ratio of `fg` on `bg`, from 1 to 21.
 *
 * A translucent foreground (8-digit hex, rgba()) is composited over `bg`
 * first: it has no luminance of its own, only the one it produces on a
 * surface. The background must be opaque; there is nothing underneath it to
 * composite against.
 */
export function contrast(fg, bg) {
  const back = parseColor(bg)
  if (back.a !== 1) throw new Error(`background must be opaque: ${bg}`)
  const front = composite(parseColor(fg), back)
  const a = luminanceOf(front)
  const b = luminanceOf(back)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/** HSL hue of a colour in degrees, [0, 360). Greys have no hue and give 0. */
export function hueOf(hex) {
  const { r, g, b } = parseColor(hex)
  const [R, G, B] = [r / 255, g / 255, b / 255]
  const max = Math.max(R, G, B)
  const delta = max - Math.min(R, G, B)
  if (delta === 0) return 0
  let h
  if (max === R) h = ((G - B) / delta) % 6
  else if (max === G) h = (B - R) / delta + 2
  else h = (R - G) / delta + 4
  return (h * 60 + 360) % 360
}

/** Smallest angle between two hues, 0-180: 350 and 10 are 20 apart, not 340. */
export function separation(a, b) {
  const d = Math.abs(hueOf(a) - hueOf(b)) % 360
  return d > 180 ? 360 - d : d
}

// --- the assertions --------------------------------------------------------

/**
 * Check one theme's tokens. `mode` is 'light' or 'dark' and is only a label on
 * the violations. Returns every violation; an empty array means the theme passes.
 */
export function check(tokens, mode) {
  const violations = []
  const missing = new Set()

  const need = (name) => {
    if (tokens[name] === undefined) {
      if (!missing.has(name)) {
        missing.add(name)
        violations.push({ mode, pair: `--ka-${name}`, kind: 'missing', detail: 'token is not defined' })
      }
      return undefined
    }
    return tokens[name]
  }

  const text = (fgName, bgName) => {
    const fg = need(fgName)
    const bg = need(bgName)
    if (fg === undefined || bg === undefined) return
    try {
      const ratio = contrast(fg, bg)
      if (ratio < AA_TEXT) {
        violations.push({ mode, pair: `${fgName} on ${bgName}`, kind: 'contrast', ratio, required: AA_TEXT, fg, bg })
      }
    } catch (error) {
      violations.push({ mode, pair: `${fgName} on ${bgName}`, kind: 'unparseable', detail: error.message })
    }
  }

  for (const name of TEXT_TOKENS) for (const surface of SURFACES) text(name, surface)
  for (const name of SIGNALS) text(name, `${name}-soft`)

  for (const [name, want] of Object.entries(REQUIRED[mode] ?? {})) {
    const got = need(name)
    if (got === undefined) continue
    if (got.toLowerCase() !== want.toLowerCase()) {
      violations.push({ mode, pair: `--ka-${name}`, kind: 'required', detail: `is ${got}, the console's palette requires ${want}` })
    }
  }

  for (const other of SEPARATED_FROM_BRAND) {
    const brand = need('brand')
    const rival = need(other)
    if (brand === undefined || rival === undefined) continue
    try {
      const angle = separation(brand, rival)
      if (angle < MIN_SEPARATION) {
        violations.push({ mode, pair: `hue of brand vs ${other}`, kind: 'separation', ratio: angle, required: MIN_SEPARATION, fg: brand, bg: rival })
      }
    } catch (error) {
      violations.push({ mode, pair: `hue of brand vs ${other}`, kind: 'unparseable', detail: error.message })
    }
  }

  return violations
}

// --- orphans ---------------------------------------------------------------

/**
 * Tokens one theme declares and the other does not. A half-converted theme
 * shows up as exactly this: a colour added to `:root` and forgotten in `.dark`.
 *
 * Every token in `.dark` must exist in `:root`. Every colour token in `:root`
 * must exist in `.dark`. Tokens in `:root` whose value is not a colour (type
 * stacks, sizes, spacing) are theme-invariant and legitimately declared once,
 * except the ones listed in THEME_DEPENDENT: a shadow embeds an ink tint, so
 * it is not a colour by this function's test but still differs per theme, and
 * a half-converted theme drops exactly that.
 */
export function orphans(light, dark) {
  const isColour = (value) => {
    try {
      parseColor(value)
      return true
    } catch {
      return false
    }
  }
  const out = []
  for (const name of Object.keys(dark)) {
    if (!(name in light)) out.push({ pair: `--ka-${name}`, detail: 'declared in .dark, missing from :root' })
  }
  for (const [name, value] of Object.entries(light)) {
    if (!(name in dark) && (isColour(value) || THEME_DEPENDENT.includes(name))) {
      out.push({ pair: `--ka-${name}`, detail: 'declared in :root, missing from .dark' })
    }
  }
  return out
}

// --- syntax colours --------------------------------------------------------

/**
 * Every colour Shiki wrote into the built HTML, per theme. Code is drawn with
 * `style="--shiki-light:#A0111F;--shiki-dark:#FF9492"` on each token span, so
 * reading the output measures the colours that are actually painted, not the
 * ones a theme file declares (a theme also lists colours for diff markers and
 * ignored text that no code block here ever uses).
 */
export function shikiColours(html) {
  const found = { light: new Set(), dark: new Set() }
  for (const m of html.matchAll(/--shiki-(light|dark):(#[0-9a-fA-F]{3,8})\b/g)) {
    found[m[1]].add(m[2].toLowerCase())
  }
  return found
}

/**
 * Check syntax colours against the surface a code block is drawn on. The
 * stylesheet forces `pre` to `--ka-paper-raised` in both themes, so that is the
 * only surface code tokens sit on; `paper-sunken` is not one of them.
 * Returns violations in the same shape as `check`.
 */
export function checkShiki(colours, light, dark) {
  const violations = []
  for (const [mode, tokens] of [['light', light], ['dark', dark]]) {
    const surface = tokens['paper-raised']
    if (surface === undefined) {
      violations.push({ mode, pair: '--ka-paper-raised', kind: 'missing', detail: 'token is not defined' })
      continue
    }
    for (const colour of [...colours[mode]].sort()) {
      const ratio = contrast(colour, surface)
      if (ratio < AA_TEXT) {
        violations.push({ mode, pair: `code token ${colour}`, kind: 'contrast', ratio, required: AA_TEXT, fg: colour, bg: surface })
      }
    }
  }
  return violations
}

function builtHtml(dir) {
  const files = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`
    if (entry.isDirectory()) files.push(...builtHtml(path))
    else if (entry.name.endsWith('.html')) files.push(path)
  }
  return files
}

// --- self-test -------------------------------------------------------------

/*
 * Expected ratios, each confirmed against an independent implementation of the
 * WCAG 2.1 formula. The alpha case composites in sRGB, as a browser paints it:
 * 0x1c/255 of white over #0b0d10 is rgb(38, 40, 42), giving 1.31.
 */
const CASES = [
  { fg: '#7dd3fc', bg: '#ffffff', expect: 1.667, label: 'the console accent on white fails AA badly' },
  { fg: '#7dd3fc', bg: '#0b0d10', expect: 11.67, label: 'the console accent on the console ground passes AA' },
  { fg: '#7dd3fc', bg: '#14171b', expect: 10.783, label: 'the console accent on a console card passes AA' },
  { fg: '#e6e8ea', bg: '#0b0d10', expect: 15.84, label: 'console text on console ground' },
  { fg: '#ffffff1c', bg: '#0b0d10', expect: 1.31, label: '8-digit hex composites over its surface' },
  { fg: '#0a5c82', bg: '#ffffff', expect: 7.327, label: 'the light-mode brand on white passes AA' },
]

function selfTest() {
  const failures = []
  const assert = (ok, label, detail) => {
    if (!ok) failures.push(`${label}: ${detail}`)
  }

  for (const c of CASES) {
    let got
    try {
      got = contrast(c.fg, c.bg)
    } catch (error) {
      failures.push(`${c.label}: threw ${error.message}`)
      continue
    }
    assert(Math.abs(got - c.expect) < 0.05, c.label, `${c.fg} on ${c.bg} gave ${got.toFixed(3)}, expected ${c.expect}`)
  }

  assert(Math.abs(hueOf('#7dd3fc') - 199) <= 1.5, 'hueOf the console accent', `gave ${hueOf('#7dd3fc').toFixed(2)}, expected 199 +/- 1.5`)
  assert(separation('#7dd3fc', '#fbbf24') >= 25, 'separation brand/notice', `gave ${separation('#7dd3fc', '#fbbf24').toFixed(2)}, expected >= 25`)
  assert(Math.abs(separation('#ff002b', '#ff2b00') - separation('#ff2b00', '#ff002b')) < 1e-9, 'separation is symmetric', 'a,b and b,a differ')
  assert(Math.abs(separation('#ff002b', '#ff2b00') - 20) < 1, 'separation wraps the wheel', `350 and 10 should be 20 apart, gave ${separation('#ff002b', '#ff2b00').toFixed(2)}`)
  assert(Math.abs(contrast('#000', '#fff') - 21) < 1e-9, 'black on white', `gave ${contrast('#000', '#fff')}, expected 21`)
  assert(Math.abs(contrast('#fff', '#fff') - 1) < 1e-9, 'identical colours', 'expected exactly 1')
  assert(Math.abs(contrast('rgba(255,255,255,0.11)', '#0b0d10') - contrast('#ffffff1c', '#0b0d10')) < 0.02, 'rgba() agrees with 8-digit hex', 'differ by more than rounding of the alpha byte')

  const css = ':root{--ka-a:#fff;/* --ka-b:#000; */}.dark{--ka-a:#000;--ka-c:red;}'
  const parsed = parseTokens(css)
  assert(parsed.light.a === '#fff' && !('b' in parsed.light), 'parseTokens light', JSON.stringify(parsed.light))
  assert(parsed.dark.a === '#000' && parsed.dark.c === 'red', 'parseTokens dark', JSON.stringify(parsed.dark))

  const html = '<span style="--shiki-light:#A0111F;--shiki-dark:#FF9492">x</span><span style="--shiki-light:#66707B;--shiki-dark:#BDC4CC">y</span>'
  const seen = shikiColours(html)
  assert(seen.light.size === 2 && seen.light.has('#66707b') && seen.dark.has('#ff9492'), 'shikiColours', JSON.stringify([seen.light, seen.dark].map((x) => [...x])))
  const surfaces = { 'paper-raised': '#ffffff' }
  assert(checkShiki(seen, surfaces, { 'paper-raised': '#14171b' }).length === 0, 'checkShiki passes AA colours', 'reported a violation')
  const pale = checkShiki({ light: new Set(['#aaaaaa']), dark: new Set() }, surfaces, { 'paper-raised': '#14171b' })
  assert(pale.length === 1 && pale[0].pair === 'code token #aaaaaa', 'checkShiki flags a pale token', JSON.stringify(pale))

  const lopsided = orphans({ a: '#fff', b: '#000', size: '1rem' }, { a: '#000', c: '#111' })
  assert(
    lopsided.length === 2 &&
      lopsided.some((o) => o.pair === '--ka-b') &&
      lopsided.some((o) => o.pair === '--ka-c'),
    'orphans',
    `expected b (root only) and c (dark only), not size; got ${JSON.stringify(lopsided)}`,
  )
  assert(orphans({ a: '#fff', size: '1rem' }, { a: '#000' }).length === 0, 'orphans ignore non-colour tokens', 'a theme-invariant token was reported')
  const shadowless = orphans({ a: '#fff', 'shadow-raised': '0 0 0 1px red' }, { a: '#000' })
  assert(
    shadowless.length === 1 && shadowless[0].pair === '--ka-shadow-raised',
    'orphans require theme-dependent non-colour tokens in both themes',
    `expected shadow-raised to be reported, got ${JSON.stringify(shadowless)}`,
  )

  return failures
}

// --- main ------------------------------------------------------------------

function describe(v) {
  if (v.kind === 'contrast') {
    return `${v.pair.padEnd(28)} ${v.ratio.toFixed(2)}:1, needs ${v.required}:1   (${v.fg} on ${v.bg})`
  }
  if (v.kind === 'separation') {
    return `${v.pair.padEnd(28)} ${v.ratio.toFixed(1)} deg, needs ${v.required}   (${v.fg} vs ${v.bg})`
  }
  return `${v.pair.padEnd(28)} ${v.detail}`
}

function main(args) {
  const failures = selfTest()
  if (failures.length) {
    console.error(`\ncontrast self-test failed (${failures.length}):\n`)
    for (const failure of failures) console.error(`  ${failure}`)
    console.error('')
    process.exit(1)
  }
  if (args.includes('--self-test')) {
    console.log(`contrast self-test ok: ${CASES.length} ratios, hue, separation, parsing`)
    return
  }

  const file = args.find((a) => !a.startsWith('--')) ?? TOKENS_CSS
  const { light, dark } = parseTokens(readFileSync(file, 'utf8'))

  if (args.includes('--shiki')) {
    let pages
    try {
      pages = builtHtml(BUILT_DIR)
    } catch {
      console.error(`\nno build to read: ${BUILT_DIR} does not exist. Run npm run build first.\n`)
      process.exit(1)
    }
    const colours = { light: new Set(), dark: new Set() }
    for (const page of pages) {
      const seen = shikiColours(readFileSync(page, 'utf8'))
      for (const mode of ['light', 'dark']) for (const c of seen[mode]) colours[mode].add(c)
    }
    if (!colours.light.size || !colours.dark.size) {
      console.error('\nno Shiki colours found in the built HTML; the check is looking at nothing\n')
      process.exit(1)
    }
    const bad = checkShiki(colours, light, dark)
    if (bad.length) {
      console.error(`\n${bad.length} code-token contrast problem(s) in ${BUILT_DIR}:\n`)
      for (const v of bad) console.error(`    ${v.mode.padEnd(6)} ${describe(v)}`)
      console.error('')
      process.exit(1)
    }
    const worst = (mode) =>
      Math.min(...[...colours[mode]].map((c) => contrast(c, (mode === 'light' ? light : dark)['paper-raised'])))
    console.log(
      `shiki ok: ${colours.light.size} light and ${colours.dark.size} dark code colours in ${pages.length} pages all reach ${AA_TEXT}:1 on paper-raised ` +
        `(worst: light ${worst('light').toFixed(2)}, dark ${worst('dark').toFixed(2)})`,
    )
    return
  }

  if (args.includes('--orphans')) {
    const lost = orphans(light, dark)
    if (lost.length) {
      console.error(`\n${lost.length} token(s) declared in only one theme in ${file}:\n`)
      for (const o of lost) console.error(`    ${o.pair.padEnd(28)} ${o.detail}`)
      console.error('')
      process.exit(1)
    }
    console.log(`orphans ok: light and dark declare the same ${Object.keys(dark).length} colour and theme-dependent tokens`)
    return
  }

  const violations = [...check(light, 'light'), ...check(dark, 'dark')]

  if (violations.length) {
    console.error(`\n${violations.length} contrast problem(s) in ${file}:\n`)
    for (const mode of ['light', 'dark']) {
      const mine = violations.filter((v) => v.mode === mode)
      if (!mine.length) continue
      console.error(`  ${mode}`)
      for (const v of mine) console.error(`    ${describe(v)}`)
    }
    console.error('')
    process.exit(1)
  }

  const pairs =
    (TEXT_TOKENS.length * SURFACES.length + SIGNALS.length + SEPARATED_FROM_BRAND.length) * 2 +
    Object.values(REQUIRED).reduce((n, r) => n + Object.keys(r).length, 0)
  console.log(`contrast ok: ${pairs} checks across light and dark`)
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main(process.argv.slice(2))
}
