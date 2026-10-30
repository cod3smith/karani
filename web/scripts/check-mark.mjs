/**
 * Fail when karani's mark stops meeting its spec.
 *
 * The mark is a filled-in list: a spine, then three bars descending in
 * length, the shortest one accented. That last bar is the line karani
 * stopped at, which is yours to sign — the product in one glyph (ADR
 * 0012). The descending rhythm is the idea, so it is asserted directly
 * rather than inferred from the coordinates happening to be right.
 *
 * The geometry is the brand kit's (assets/brand/karani-mark.svg and
 * karani-favicon.svg), unchanged. The COLOUR is deliberately not: the kit
 * draws the mark in dark green with an orange accent, which measures close
 * to invisible on the console's near-black ground and contradicts the
 * palette the rest of the site is built from. So the fills asserted below
 * are the site's own, and a future sync from the kit must not quietly
 * bring the green back.
 *
 * It is drawn five times, and that is why this check exists:
 * public/icon.svg (display, 32px and up), public/favicon.svg (small, below
 * 32px, thicker bars), components/Mark.tsx (the navbar, at 24px, so the
 * small geometry), app/apple-icon.png/route.tsx (the home-screen icon,
 * also small) and app/not-found.tsx (the refusal variant). Five copies of
 * one drawing drift.
 *
 * The defect this exists to catch first is a favicon that vanishes on a
 * light tab strip, so the favicon's own colour-scheme handling is asserted
 * before anything else.
 *
 * Usage:
 *   node scripts/check-mark.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p) => readFileSync(join(root, p), 'utf8')

const failures = []
function assert(cond, message) {
  if (!cond) failures.push(message)
}

// The console's own palette. The mark is the product's, so these are the
// product's values and not the site's idea of them.
const BLUE = '#7DD3FC'
const DARK = '#0B0D10'
const PAPER = '#F6F7F9'

// The brand kit's colours, which must NOT appear here. The kit is the
// source of the geometry and not of the palette; a future sync that
// brought these across would put dark green on a near-black ground.
const KIT_COLOURS = ['#0E3B33', '#D9822B', '#F6F5F0']

/*
 * The two drawings, transcribed from the kit. Order matters: spine, then
 * the three bars longest to shortest. The accent is always the last.
 */
const DISPLAY = {
  spine: '<rect x="10" y="8" width="9" height="48" rx="4.5"',
  bars: [
    '<rect x="27" y="8" width="28" height="9" rx="4.5"',
    '<rect x="27" y="27.5" width="20" height="9" rx="4.5"',
  ],
  accent: '<rect x="27" y="47" width="9" height="9" rx="4.5"',
  // Bar widths, longest first, with the accent last.
  widths: [28, 20, 9],
  thickness: 9,
}
const SMALL = {
  spine: '<rect x="8" y="6" width="12" height="52" rx="6"',
  bars: [
    '<rect x="26" y="6" width="30" height="12" rx="6"',
    '<rect x="26" y="26" width="22" height="12" rx="6"',
  ],
  accent: '<rect x="26" y="46" width="12" height="12" rx="6"',
  widths: [30, 22, 12],
  thickness: 12,
}

const fragments = (variant) => [variant.spine, ...variant.bars, variant.accent]

let favicon = ''
let icon = ''
try {
  favicon = read('public/favicon.svg')
  icon = read('public/icon.svg')
} catch (err) {
  console.error(`FAIL: ${err.message}`)
  process.exit(1)
}
const mark = read('components/Mark.tsx')
const markCss = read('components/Mark.module.css')
const apple = read('app/apple-icon.png/route.tsx')
const opengraph = read('app/opengraph-image.png/route.tsx')
const layout = read('app/layout.tsx')
const notFound = read('app/not-found.tsx')

const EVERY_DRAWING = [
  ['public/icon.svg', icon],
  ['public/favicon.svg', favicon],
  ['components/Mark.tsx', mark],
  ['apple-icon route', apple],
  ['opengraph route', opengraph],
  ['app/not-found.tsx', notFound],
]

// ---- the favicon must carry BOTH tab-strip schemes -----------------------

assert(/prefers-color-scheme:\s*dark/.test(favicon), 'favicon has no dark-scheme override')
assert(new RegExp(DARK, 'i').test(favicon), 'light tab strip needs dark bars')
assert(new RegExp(PAPER, 'i').test(favicon), 'dark tab strip needs pale bars')
// The default, outside the media query, must be the dark one: a reader on a
// light tab strip is the case that fails invisibly.
const outsideStyle = favicon.replace(/<style>[\s\S]*?<\/style>/, '')
assert(
  !new RegExp(PAPER, 'i').test(outsideStyle),
  'favicon bars must default to dark, with the pale fill only inside the media query',
)

// ---- geometry, exactly ---------------------------------------------------

for (const fragment of fragments(DISPLAY)) {
  assert(icon.includes(fragment), `public/icon.svg (display variant) is missing ${fragment}`)
}
for (const fragment of fragments(SMALL)) {
  assert(favicon.includes(fragment), `public/favicon.svg (small variant) is missing ${fragment}`)
}
assert(
  /viewBox="0 0 64 64"/.test(favicon) && /viewBox="0 0 64 64"/.test(icon),
  'both SVGs must use the kit grid, viewBox 0 0 64 64',
)

// The small variant is the thicker one. That is its whole reason to exist:
// the display variant's 9-unit bars land at about two pixels at 16px.
assert(
  SMALL.thickness > DISPLAY.thickness,
  'the small variant must be the thicker drawing, or it has no reason to exist',
)

// ---- the bars descend, and the shortest one is the accent ---------------
//
// This is the mark's meaning rather than its coordinates: a list filled in
// down to a last, short line that is somebody else's to sign. Equal-length
// bars would still draw, and would say nothing.

for (const [name, variant] of [['display', DISPLAY], ['small', SMALL]]) {
  const [first, second, accent] = variant.widths
  assert(
    first > second && second > accent,
    `the ${name} variant's bars must descend in length (got ${variant.widths.join(', ')})`,
  )
  assert(
    accent === Math.min(...variant.widths),
    `the ${name} variant's accent must be the shortest bar`,
  )
}

// ---- the accent is the brand, everywhere it is drawn --------------------

for (const [name, svg] of [['icon', icon], ['favicon', favicon]]) {
  const accentLine = svg.split('\n').find((l) => l.includes(DISPLAY.accent) || l.includes(SMALL.accent))
  assert(accentLine !== undefined, `${name}: the accent bar is missing`)
  assert(
    accentLine === undefined || new RegExp(BLUE, 'i').test(accentLine),
    `${name}: the accent bar must be ${BLUE}`,
  )
}

// ---- the kit's palette must not come back -------------------------------

for (const [name, source] of EVERY_DRAWING) {
  for (const colour of KIT_COLOURS) {
    assert(
      !new RegExp(colour, 'i').test(source),
      `${name} uses the brand kit's ${colour}. The kit is the source of the ` +
        `geometry, not the palette: its dark green is close to invisible on ` +
        `the console's near-black ground.`,
    )
  }
}

// ---- neither file depends on a webfont ----------------------------------

assert(!/font-family|<text/.test(favicon + icon), 'the SVGs must not use text or fonts')

// ---- the React mark draws the SMALL variant -----------------------------
//
// It is rendered at 24px, below the 32px at which the display variant holds
// up, so it must take the small geometry and theme-aware colour.

for (const fragment of fragments(SMALL)) {
  assert(mark.includes(fragment), `Mark.tsx (24px, small variant) is missing ${fragment}`)
}
assert(!mark.includes(DISPLAY.spine), "Mark.tsx must not draw the display variant's spine")
const glyphSize = Number(mark.match(/<svg[^>]*\swidth="(\d+)"/)?.[1])
assert(
  glyphSize > 0 && glyphSize < 32,
  `Mark.tsx draws the glyph at ${glyphSize}px; below 32px it must use the small variant, and from 32px up the display one`,
)
assert(/currentColor/.test(mark), 'Mark.tsx bars must use currentColor')
assert(/--ka-brand/.test(mark + markCss), 'the Mark accent must use var(--ka-brand)')

// ---- the Apple icon is the small variant too ---------------------------

for (const fragment of fragments(SMALL)) {
  assert(apple.includes(fragment), `apple-icon route (small variant) is missing ${fragment}`)
}
assert(new RegExp(BLUE, 'i').test(apple), `the apple icon's accent must be ${BLUE}`)
assert(new RegExp(DARK, 'i').test(apple), "the apple icon must sit on the console's own ground")

// ---- the social card is the display variant ----------------------------

for (const fragment of fragments(DISPLAY)) {
  assert(opengraph.includes(fragment), `opengraph route (display variant) is missing ${fragment}`)
}

// ---- the display face is wired to the variable tokens.css reads ---------

assert(/Newsreader/.test(layout), 'layout.tsx must load Newsreader')
assert(/variable:\s*'--font-newsreader'/.test(layout), "the display variable must be '--font-newsreader'")
assert(/newsreader\.variable/.test(layout), "the display face's .variable must be on <html>")

// ---- metadata.icons must declare the SMALL variant only ----------------
//
// icon.svg collapses at 16px, and listing both lets the browser pick it for
// the tab strip. 'favicon.svg' contains 'icon.svg' as a substring, so these
// match on the quoted path.

const iconsBlock = layout.match(/icons:\s*\{[\s\S]*?\n  \},/)?.[0] ?? ''
assert(iconsBlock !== '', 'layout.tsx must declare metadata.icons')
assert(/'\/favicon\.svg'/.test(iconsBlock), 'metadata.icons must reference /favicon.svg')
assert(
  !/'\/icon\.svg'/.test(iconsBlock),
  'metadata.icons must not declare /icon.svg (the display variant)',
)
// Next links a file-convention icon only when metadata.icons is unset, so
// the Apple icon has to be named here, and it must be the generated PNG.
const appleBlock = iconsBlock.match(/apple:\s*\[[\s\S]*?\]/)?.[0] ?? ''
assert(/'\/apple-icon\.png'/.test(appleBlock), "metadata.icons.apple must reference '/apple-icon.png'")
assert(!/icon\.svg/.test(appleBlock), 'metadata.icons.apple must not reference an SVG')

// ---- the 404 is the mark going wrong ------------------------------------
//
// Same list, but the last bar -- the one karani normally stops at for a
// signature -- is drawn in the refusal colour. If it stops being the mark
// it stops saying anything, so the spine and both long bars must survive.

assert(notFound.includes(DISPLAY.spine), "the 404 must draw the mark's spine")
for (const bar of DISPLAY.bars) {
  assert(notFound.includes(bar), `the 404 must keep the bar ${bar}`)
}
assert(notFound.includes(DISPLAY.accent), 'the 404 must draw the last bar, in the refusal colour')
assert(/var\(--ka-refuse\)/.test(notFound), "the 404's last bar must be var(--ka-refuse)")
assert(
  !new RegExp(BLUE, 'i').test(notFound) && !/--ka-brand/.test(notFound),
  'the 404 must not draw the last bar in the brand colour; the point is that it is not there to sign',
)

if (failures.length) {
  for (const f of failures) console.error(`FAIL: ${f}`)
  process.exit(1)
}
console.log(`check-mark: ok (${EVERY_DRAWING.length} drawings)`)
