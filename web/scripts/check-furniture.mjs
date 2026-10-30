/**
 * Fail when the ledger furniture in globals.css stops meeting its spec.
 *
 * The furniture is the typographic apparatus that makes these pages read as
 * a record rather than a themed Nextra site: the brand-coloured eyebrow,
 * the muted mono label, the numbered sections and figures, the grain and
 * the aurora.
 *
 * Beyond the values, this guards the four ways the furniture can hurt a
 * reader:
 *   - the grain and the aurora must switch off under
 *     prefers-reduced-motion, inside the existing block rather than a
 *     second one;
 *   - a print stylesheet must force ink on paper, because the site defaults
 *     to near-black and these reference tables get printed;
 *   - the section counter must restart on every page and must only ever be
 *     incremented by an h2 inside article main, so a generated page with no
 *     h2 never shows a stray or zero count;
 *   - body prose stays in the serif; the display face is for h1 and display
 *     titles only.
 *
 * It also guards the two components that carry karani's own output
 * (Verdict, RunReport): that they number themselves as figures in a slot of
 * their own, that no call site lost the `note` saying what produced the
 * output, and that print resets every token a plate is drawn with.
 *
 * Everything above reads source. The one thing source cannot tell you is
 * whether the stylesheet attaches to anything -- and in the project this
 * was ported from, for the whole life of the site, it did not: the rules
 * were keyed to `.nextra-*` classes Nextra 4 does not emit, so the serif,
 * the measure, the heading scale and the section numbers never rendered and
 * no check noticed. `--built` closes that. It reads the exported HTML and
 * fails, naming the selector, when globals.css is keyed to something the
 * output does not contain.
 *
 * Usage:
 *   node scripts/check-furniture.mjs            check the source
 *   node scripts/check-furniture.mjs --built    check it against out/
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { contrast, parseTokens } from './check-contrast.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

const failures = []
function assert(cond, message) {
  if (!cond) failures.push(message)
}

let raw = ''
try {
  raw = readFileSync(join(root, 'app/globals.css'), 'utf8')
} catch (err) {
  console.error(`FAIL: ${err.message}`)
  process.exit(1)
}

// Comments are prose, and the prose mentions the very strings asserted on.
const css = raw.replace(/\/\*[\s\S]*?\*\//g, '')

if (process.argv.includes('--built')) {
  const out = join(root, 'out')
  const pages = []
  const walkHtml = (dir) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, e.name)
      if (e.isDirectory()) {
        if (e.name !== '_pagefind' && e.name !== '_next') walkHtml(path)
      } else if (e.name.endsWith('.html')) pages.push(readFileSync(path, 'utf8'))
    }
  }
  try {
    walkHtml(out)
  } catch {
    console.error('FAIL: out/ does not exist; run npm run build first')
    process.exit(1)
  }
  const classes = new Set()
  const attrs = new Set()
  let classText = ''
  for (const html of pages) {
    for (const m of html.matchAll(/\sclass="([^"]*)"/g)) {
      classText += ` ${m[1]}`
      for (const c of m[1].split(/\s+/)) classes.add(c)
    }
    for (const m of html.matchAll(/\s(data-[\w-]+)/g)) attrs.add(m[1])
  }
  const miss = []
  // Preludes of every rule, at any depth; at-rules have none worth checking.
  const preludes = [...css.matchAll(/([^{}]+)\{/g)]
    .map((m) => m[1].trim())
    .filter((p) => !p.startsWith('@'))
  for (const prelude of preludes) {
    for (const c of prelude.matchAll(/\.(nextra-[\w-]+)/g)) {
      if (!classes.has(c[1])) miss.push(`.${c[1]} (in "${prelude}")`)
    }
    for (const a of prelude.matchAll(/\[(data-[\w-]+)/g)) {
      if (!attrs.has(a[1])) miss.push(`[${a[1]}] (in "${prelude}")`)
    }
    for (const f of prelude.matchAll(/\[class\*=['"]([^'"]+)['"]\]/g)) {
      if (!classText.includes(f[1])) miss.push(`[class*='${f[1]}'] (in "${prelude}")`)
    }
  }

  // Links. Nextra paints every link it renders with a primary-colour utility
  // whose value comes from its own lightness ramp, not from our tokens. So a
  // link must be covered by a rule of ours, keyed to that utility, that sets
  // a token; and that token must reach 4.5:1 on every surface prose sits on,
  // in both themes. This is a static model of the cascade, since there is no
  // browser in the gate: it proves a rule exists for every utility the built
  // HTML puts on a link, and that the rule's colour passes.
  // (check-contrast.mjs checks token values and never which token a rendered
  // rule uses; this is the other half.)
  const linkUtilities = new Set()
  let linkCount = 0
  for (const html of pages) {
    if (!/<article[\s>][\s\S]*<main[\s>]/.test(html)) continue
    const main = html.slice(html.indexOf('<main'), html.indexOf('</main>'))
    for (const a of main.matchAll(/<a\s[^>]*class="([^"]*)"[^>]*>/g)) {
      const utilities = a[1].split(/\s+/).filter((c) => /^x:text-primary-\d+$/.test(c))
      if (utilities.length) linkCount += 1
      for (const u of utilities) linkUtilities.add(u)
    }
  }
  const { light: lightTokens, dark: darkTokens } = parseTokens(
    readFileSync(join(root, 'app/tokens.css'), 'utf8'),
  )
  for (const u of linkUtilities) {
    const rule = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].find(
      (m) => m[1].includes(`[class*='${u}']`) && !m[1].includes(':hover'),
    )
    const token = rule?.[2].match(/(?:^|[;\s])color:\s*var\(--(ka-[\w-]+)\)/)?.[1]
    if (!token) {
      miss.push(
        `links carry ${u} but globals.css has no rule keyed to [class*='${u}'] that sets color to a token`,
      )
      continue
    }
    for (const [mode, tokens] of [['light', lightTokens], ['dark', darkTokens]]) {
      for (const surface of ['paper', 'paper-raised', 'paper-sunken']) {
        const ratio = contrast(tokens[token.replace(/^ka-/, '')], tokens[surface])
        if (ratio < 4.5) {
          miss.push(
            `inline links (${u}) use --${token}, which is ${ratio.toFixed(2)}:1 on --ka-${surface} in ${mode}; AA needs 4.5:1`,
          )
        }
      }
    }
  }
  if (linkCount === 0) {
    miss.push('no prose link in the built HTML carries a primary-colour utility; the link check is looking at nothing')
  }

  // Browser chrome. The site opens dark whatever the OS prefers, so the
  // first theme-color a browser can use must be the dark paper,
  // unconditionally. Nextra's <Head> adds its own light/dark pair after
  // ours; the first match in tree order wins, so ours has to come first and
  // have no media query.
  const darkPaper = darkTokens.paper
  for (const html of pages) {
    const first = /<meta\s[^>]*name="theme-color"[^>]*>/.exec(html)?.[0]
    if (!first) continue
    if (/media=/.test(first) || !new RegExp(`content="${darkPaper}"`, 'i').test(first)) {
      miss.push(
        `first theme-color is ${first}; it must be unconditional and ${darkPaper}, the default theme's paper`,
      )
      break
    }
  }

  const withProse = pages.filter((h) => /<article[\s>][\s\S]*<main[\s>]/.test(h)).length
  if (withProse === 0) miss.push('article main (no page has a <main> inside an <article>)')
  if (miss.length) {
    console.error(`check-furniture --built: ${miss.length} selector(s) match nothing in out/`)
    for (const m of new Set(miss)) console.error(`  FAIL: ${m}`)
    process.exit(1)
  }
  console.log(
    `check-furniture --built: every .nextra-*, data-* and class-fragment selector matches, ` +
      `${withProse} pages have article main, and ${linkCount} prose links are covered by an AA-passing rule`,
  )
  process.exit(0)
}

/** The body of the brace block that opens at `openIndex`, or null. */
function blockAt(text, openIndex) {
  let depth = 0
  for (let i = openIndex; i < text.length; i++) {
    if (text[i] === '{') depth++
    else if (text[i] === '}' && --depth === 0) return text.slice(openIndex + 1, i)
  }
  return null
}

/** Every at-rule block whose prelude matches `prelude` (a string). */
function atRules(prelude) {
  const out = []
  let from = 0
  for (;;) {
    const at = css.indexOf(prelude, from)
    if (at === -1) return out
    const open = css.indexOf('{', at)
    const body = blockAt(css, open)
    if (body !== null) out.push(body)
    from = open + 1
  }
}

/** Declarations of every rule (outside or inside `scope`) with exactly this selector list. */
function rules(scope, selector) {
  const wanted = selector.split(',').map((s) => s.trim().replace(/\s+/g, ' '))
  const found = []
  const re = /([^{}]+)\{([^{}]*)\}/g
  for (let m; (m = re.exec(scope)); ) {
    const sel = m[1].split(',').map((s) => s.trim().replace(/\s+/g, ' '))
    if (wanted.every((w) => sel.includes(w))) found.push(m[2])
  }
  return found
}
const has = (bodies, re) => bodies.some((b) => re.test(b))

// --- The labels, exactly -------------------------------------------------

const eyebrow = rules(css, '.ka-eyebrow')
assert(eyebrow.length > 0, '.ka-eyebrow is missing')
assert(has(eyebrow, /letter-spacing:\s*0\.28em/), '.ka-eyebrow must set letter-spacing: 0.28em')
assert(has(eyebrow, /font-size:\s*10px/), '.ka-eyebrow must set font-size: 10px')
assert(has(eyebrow, /text-transform:\s*uppercase/), '.ka-eyebrow must be uppercase')
assert(has(eyebrow, /color:\s*var\(--ka-brand\)/), '.ka-eyebrow must be coloured var(--ka-brand)')
assert(
  has(eyebrow, /font-family:\s*var\(--ka-font-mono\)/),
  '.ka-eyebrow must be set in var(--ka-font-mono)',
)

const mono = rules(css, '.ka-mono')
assert(mono.length > 0, '.ka-mono is missing')
assert(has(mono, /letter-spacing:\s*0\.22em/), '.ka-mono must set letter-spacing: 0.22em')
assert(has(mono, /font-size:\s*11px/), '.ka-mono must set font-size: 11px')
assert(has(mono, /text-transform:\s*uppercase/), '.ka-mono must be uppercase')
assert(has(mono, /color:\s*var\(--ka-ink-muted\)/), '.ka-mono must be coloured var(--ka-ink-muted)')
assert(has(mono, /font-family:\s*var\(--ka-font-mono\)/), '.ka-mono must be set in var(--ka-font-mono)')

const grain = rules(css, '.ka-grain::before')
assert(grain.length > 0, '.ka-grain::before is missing')
assert(has(grain, /z-index:\s*9999/), '.ka-grain::before must sit at z-index: 9999')
assert(has(grain, /background-repeat:\s*repeat/), '.ka-grain::before must repeat its tile')
assert(has(grain, /position:\s*fixed/), '.ka-grain::before must be fixed to the viewport')
assert(has(grain, /pointer-events:\s*none/), '.ka-grain::before must not intercept pointer events')
assert(has(grain, /opacity:\s*0\.035/), '.ka-grain::before must set opacity: 0.035')
assert(has(grain, /feTurbulence/), '.ka-grain::before must draw its noise with feTurbulence')
assert(has(grain, /fractalNoise/), '.ka-grain::before must use fractalNoise')
assert(has(grain, /background-size:\s*256px\s+256px/), '.ka-grain::before must tile at 256px')

const aurora = rules(css, '.ka-aurora')
assert(aurora.length > 0, '.ka-aurora is missing')
assert(has(aurora, /filter:\s*blur\(60px\)/), '.ka-aurora must set filter: blur(60px)')
assert(has(aurora, /pointer-events:\s*none/), '.ka-aurora must not intercept pointer events')
assert(has(aurora, /position:\s*absolute/), '.ka-aurora must be position: absolute')
// The aurora is the brand blue at a few percent, and it is the one place in
// the stylesheet allowed a literal colour: a gradient stop cannot take a
// token's alpha. So the literal is asserted to be the brand's own RGB.
for (const a of ['0.08', '0.05', '0.04']) {
  assert(
    has(aurora, new RegExp(`radial-gradient\\([^;]*rgba\\(125, 211, 252, ${a.replace('.', '\\.')}\\)`)),
    `.ka-aurora must have a brand-blue radial at ${a}`,
  )
}
assert(
  (aurora.join('').match(/radial-gradient\(/g) ?? []).length === 3,
  '.ka-aurora must have exactly three radials',
)

// --- Reduced motion, extended and not duplicated ------------------------

const reduced = atRules('@media (prefers-reduced-motion: reduce)')
assert(reduced.length === 1, `expected exactly one prefers-reduced-motion block, found ${reduced.length}`)
const reducedCss = reduced.join('\n')
assert(
  has(rules(reducedCss, '.ka-grain::before'), /display:\s*none/),
  'reduced motion must set .ka-grain::before { display: none }',
)
assert(
  has(rules(reducedCss, '.ka-aurora'), /display:\s*none/),
  'reduced motion must set .ka-aurora { display: none }',
)
assert(
  /transition-duration:\s*0\.01ms\s*!important/.test(reducedCss) &&
    /animation-duration:\s*0\.01ms\s*!important/.test(reducedCss),
  'the reduced-motion duration rule must survive',
)

// --- Print forces ink on paper ------------------------------------------

const prints = atRules('@media print')
assert(prints.length === 1, `expected exactly one @media print block, found ${prints.length}`)
const printCss = prints.join('\n')
assert(
  has(rules(printCss, 'html, body'), /background:\s*#fff\b/) &&
    has(rules(printCss, 'html, body'), /color:\s*#000\b/),
  'print must force html, body to background: #fff and color: #000',
)
for (const sel of ['.ka-grain::before', '.ka-aurora', '.nextra-sidebar', '.nextra-toc']) {
  assert(has(rules(printCss, sel), /display:\s*none/), `print must hide ${sel}`)
}
// .ka-grain is mounted on body: a bare `.ka-grain` selector that hides it
// hides the whole page, and every printed sheet comes out blank.
assert(
  !/(^|[\s,{}])\.ka-grain\s*[,{]/.test(printCss),
  'print must not hide a bare .ka-grain: it is mounted on body, so that would blank every page',
)
assert(
  has(rules(printCss, 'article main h2::before'), /color:\s*(#000|inherit)\b/),
  'print must drop the section counter colour to ink',
)
// Syntax tokens: Nextra swaps each span to --shiki-dark under .dark, so a
// dark-theme reader would print pale tokens on white unless print pins them
// to the light theme's.
assert(
  has(
    rules(printCss, 'article main pre, article main .nextra-code span'),
    /color:\s*var\(--shiki-light[^;]*!important/,
  ) &&
    has(
      rules(printCss, 'article main pre, article main .nextra-code span'),
      /background-color:\s*#fff\s*!important/,
    ),
  'print must pin code blocks and their tokens to --shiki-light on white, or a dark-theme reader prints pale tokens',
)
// Every token a plate is drawn with must be reset, not only the neutrals:
// under .dark these are light text on dark fills, and a printer drops
// backgrounds.
for (const family of ['refuse', 'agree', 'notice']) {
  for (const suffix of ['', '-soft', '-rule']) {
    assert(
      new RegExp(`--ka-${family}${suffix}:\\s*#`).test(printCss),
      `print must reset --ka-${family}${suffix}`,
    )
  }
}

// --- Counters restart per page and never leak ---------------------------

const resets = [...css.matchAll(/counter-reset:\s*([^;}]+)/g)]
assert(
  has(rules(css, 'article main'), /counter-reset:\s*ka-section\s+ka-figure\s*;?/),
  'article main must reset ka-section and ka-figure together',
)
assert(
  resets.length === 1,
  `ka-section and ka-figure must be reset in one place, found ${resets.length} counter-reset declarations`,
)

const incSection = [...css.matchAll(/([^{}]+)\{[^{}]*counter-increment:\s*ka-section\b[^{}]*\}/g)]
assert(incSection.length === 1, `ka-section must be incremented exactly once, found ${incSection.length}`)
assert(
  incSection.every((m) => m[1].trim() === 'article main h2::before'),
  'ka-section must be incremented only by "article main h2::before"',
)
const sectionBefore = rules(css, 'article main h2::before')
assert(
  has(sectionBefore, /content:\s*'No\. '\s*counter\(ka-section,\s*decimal-leading-zero\)\s*;/),
  'the h2 section marker must read "No. NN", from ka-section',
)
assert(has(sectionBefore, /display:\s*block/), 'the section marker must sit above the heading (display: block)')
assert(has(sectionBefore, /color:\s*var\(--ka-brand\)/), 'the section marker must be brand-coloured')
assert(has(sectionBefore, /letter-spacing:\s*0\.28em/), 'the section marker must be set like .ka-eyebrow')

const figure = rules(css, '.ka-figure::before')
assert(
  has(figure, /counter-increment:\s*ka-figure/) &&
    has(figure, /content:\s*'FIG\. '\s*counter\(ka-figure,\s*decimal-leading-zero\)/),
  '.ka-figure::before must count figures as "FIG. NN"',
)
assert(has(figure, /letter-spacing:\s*0\.22em/), '.ka-figure::before must be set like .ka-mono')

// --- The display face is for h1 and display titles only -----------------

const prose = rules(css, 'article main :is(p, li, dd, blockquote, td, th)')
assert(
  has(prose, /font-family:\s*var\(--ka-font-prose\)/),
  'body prose must stay in var(--ka-font-prose)',
)
assert(has(prose, /font-size:\s*var\(--ka-prose-size\)/), 'body prose size must stay var(--ka-prose-size)')
assert(
  has(rules(css, 'article main p'), /max-width:\s*var\(--ka-measure\)/),
  'body prose measure must stay var(--ka-measure)',
)

const displayUsers = [...css.matchAll(/([^{}]+)\{[^{}]*font-family:\s*var\(--ka-font-display\)[^{}]*\}/g)]
  .map((m) => m[1].trim().replace(/\s+/g, ' '))
  .sort()
assert(
  JSON.stringify(displayUsers) === JSON.stringify(['.ka-display', 'article main h1']),
  `var(--ka-font-display) is for h1 and .ka-display only, found: ${displayUsers.join(' | ') || 'none'}`,
)
const h1 = rules(css, 'article main h1')
assert(has(h1, /font-weight:\s*400/), 'h1 must be weight 400')
assert(has(h1, /letter-spacing:\s*-0\.02em/), 'h1 must set letter-spacing: -0.02em')
const display = rules(css, '.ka-display')
assert(has(display, /font-weight:\s*400/), '.ka-display must be weight 400')
assert(has(display, /letter-spacing:\s*-0\.02em/), '.ka-display must set letter-spacing: -0.02em')

// --- The header comment must describe the ground the file uses ----------

const header = raw.match(/^\s*\/\*[\s\S]*?\*\//)?.[0] ?? ''
assert(header.length > 0, 'globals.css must open with a header comment')
assert(/near-black/i.test(header), 'the header comment must say the ground is near-black')
assert(
  /submission console/i.test(header),
  'the header comment must say where the palette comes from: the submission console',
)

// --- The display face must only be asked for weights layout.tsx loads ---
//
// A rule asking for a weight the layout never loaded falls back silently,
// and no build says so.

const read = (rel) => readFileSync(join(root, rel), 'utf8')
const stripComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '')
const layoutSrc = read('app/layout.tsx')
const displayCall = layoutSrc.match(/Newsreader\(\{([\s\S]*?)\}\)/)?.[1] ?? ''
const loadedWeights = [
  ...(displayCall.match(/weight:\s*\[([^\]]*)\]/)?.[1] ?? '').matchAll(/['"](\d+)['"]/g),
].map((m) => m[1])
assert(loadedWeights.length > 0, 'layout.tsx must list the display face weights it loads')

const cssFiles = [
  ['app/globals.css', css],
  ...readdirSync(join(root, 'components'))
    .filter((f) => f.endsWith('.css'))
    .map((f) => [`components/${f}`, stripComments(read(`components/${f}`))]),
]
for (const [file, text] of cssFiles) {
  for (const m of text.matchAll(/([^{}]+)\{([^{}]*font-family:\s*var\(--ka-font-display\)[^{}]*)\}/g)) {
    const sel = m[1].trim().replace(/\s+/g, ' ')
    const weight = m[2].match(/font-weight:\s*(\d+)/)?.[1]
    assert(weight !== undefined, `${file}: "${sel}" sets the display face without an explicit font-weight`)
    assert(
      weight === undefined || loadedWeights.includes(weight),
      `${file}: "${sel}" asks the display face for weight ${weight}, but layout.tsx loads only ${loadedWeights.join(', ')}`,
    )
  }
}

// --- The plates: karani's own output, numbered as figures ---------------

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) walk(full, out)
    else if (full.endsWith('.mdx')) out.push(full)
  }
  return out
}
const mdxFiles = walk(join(root, 'content')).map((f) => [relative(root, f), readFileSync(f, 'utf8')])
const mdx = mdxFiles.map(([, body]) => body)

const verdictCss = stripComments(read('components/Verdict.module.css'))
const runReportCss = stripComments(read('components/RunReport.module.css'))
const noteCss = stripComments(read('components/Note.module.css'))
const statsCss = stripComments(read('components/Stats.module.css'))
const verdictTsx = read('components/Verdict.tsx')
const runReportTsx = read('components/RunReport.tsx')

// Every plate says what produced it. This is deliberately not a count: an
// earlier version of this gate in the project it was ported from asserted
// "exactly six pages", which was true when written and broke the moment a
// seventh was added -- ordinary work the guard had no business failing. What
// matters is the invariant, and it also catches a note dropped from a page
// added after this was written, which counting never did.
for (const component of ['Verdict', 'RunReport']) {
  const openings = mdxFiles.flatMap(([file, body]) =>
    (body.match(new RegExp(`<${component}(?:\\s[^>]*)?>`, 'g')) ?? []).map((tag) => [file, tag]),
  )
  assert(openings.length > 0, `no <${component}> call sites found in content/`)
  for (const [file, tag] of openings) {
    assert(
      /\snote=/.test(tag),
      `${file}: <${component}> has no note=. The figure number sits beside the note and never replaces it.`,
    )
  }
}

// FIG. gets its own slot in each plate's bar.
assert(/:global\(\.ka-figure\)/.test(verdictCss), 'Verdict.module.css must position .ka-figure')
assert(/:global\(\.ka-figure\)/.test(runReportCss), 'RunReport.module.css must position .ka-figure')
assert(/counter-increment:\s*ka-figure/.test(css), 'globals.css must increment the ka-figure counter')
for (const [name, tsx] of [
  ['Verdict', verdictTsx],
  ['RunReport', runReportTsx],
]) {
  assert(/figure\s*=\s*true/.test(tsx), `${name} must default figure to true`)
  assert(/figure\?:\s*boolean/.test(tsx), `${name} must declare figure?: boolean`)
  assert(/className="ka-figure"/.test(tsx), `${name} must render the global ka-figure class`)
  assert(/note\?:\s*ReactNode/.test(tsx), `${name} must accept note?: ReactNode`)
}
// The figure label's divider only exists to separate it from what follows.
for (const [name, text] of [
  ['Verdict', verdictCss],
  ['RunReport', runReportCss],
]) {
  const figRule = text.match(/:global\(\.ka-figure\)\s*\{([^}]*)\}/)?.[1] ?? ''
  assert(!/border-right/.test(figRule), `${name}: the figure label must not carry an unconditional border-right`)
  assert(
    /:global\(\.ka-figure\):not\(:last-child\)/.test(text),
    `${name}: the figure label's divider must be suppressed when nothing follows it`,
  )
}

// Plates are plates: a raised sheet inside a hairline frame.
for (const [name, text] of [
  ['Verdict', verdictCss],
  ['RunReport', runReportCss],
]) {
  const panel = rules(text, '.panel')
  assert(has(panel, /background:\s*var\(--ka-paper-raised\)/), `${name} .panel must sit on --ka-paper-raised`)
  assert(has(panel, /border:\s*1px solid var\(--ka-rule\)/), `${name} .panel must have a 1px --ka-rule frame`)
  assert(has(panel, /border-radius:\s*var\(--ka-radius-lg\)/), `${name} .panel must use --ka-radius-lg`)
}

// A verdict's colour carries on the left rule and in the word, and nowhere
// else: tinting the bar and the border too would be three statements of one
// thing and would leave no colour spare for the page.
for (const verdict of ['qualified', 'maybe', 'skip']) {
  assert(
    new RegExp(`\\[data-verdict='${verdict}'\\]\\s*\\{[^{}]*box-shadow:\\s*inset 3px`).test(verdictCss),
    `Verdict must carry a left rule for data-verdict='${verdict}'`,
  )
}
// A fit score only means anything within the model and prompt version that
// produced it (ADR 0022), so the plate must always show both.
assert(/styles\.foot/.test(verdictTsx), 'Verdict must render a provenance foot')
assert(
  /r\.model/.test(verdictTsx) && /r\.prompt_version/.test(verdictTsx),
  'Verdict must show the model and the prompt version: a fit score is not comparable without them',
)
// The evidence is the load-bearing half of a strength, since a claim karani
// cannot cite is dropped. A plate that showed only claims would be showing
// the opposite of the rule.
assert(
  /evidence_from_resume/.test(verdictTsx),
  'Verdict must render each strength\'s evidence, not only its claim',
)

// The default callout is not on the brand colour, in any property.
const noteKindRules = [...noteCss.matchAll(/([^{}]*\[data-kind=['"]note['"]\][^{}]*)\{([^{}]*)\}/g)]
assert(noteKindRules.length >= 2, "Note.module.css must style [data-kind='note'] and its label")
for (const [, sel, body] of noteKindRules) {
  assert(!/--ka-brand/.test(body), `Note rule "${sel.trim()}" must not use the brand colour`)
}
for (const kind of ['warning', 'tip', 'caution']) {
  assert(
    new RegExp(`\\[data-kind=['"]${kind}['"]\\][^{}]*\\{[^{}]*--ka-(refuse|agree|notice)\\b`).test(noteCss),
    `Note kind='${kind}' must keep its signal colour`,
  )
}

// Stats: measured figures in the display face, still tabular so they align.
const statValue = rules(statsCss, '.value')
assert(has(statValue, /font-family:\s*var\(--ka-font-display\)/), 'Stats .value must use the display face')
assert(has(statValue, /font-weight:\s*400/), 'Stats .value must be weight 400')
assert(has(statValue, /tabular-nums/), 'Stats .value must keep tabular-nums')
// Every Stat says where its number came from, or it is a marketing claim
// with a serif on it.
for (const [file, body] of mdxFiles) {
  for (const tag of body.match(/<Stat\s[^>]*\/>/g) ?? []) {
    assert(/\snote=/.test(tag), `${file}: a <Stat> has no note= saying where the number came from`)
  }
}

// The Band heading is not a numbered entry of the record.
assert(
  has(rules(stripComments(read('components/Hero.module.css')), '.band .bandTitle::before'), /content:\s*none/),
  "a Band's heading must not pick up the global section marker",
)

// Atmosphere that nothing mounts is dead CSS. Both classes must be used by
// markup, and the aurora's parent must clip it.
const markup = [
  read('app/layout.tsx'),
  ...readdirSync(join(root, 'components'))
    .filter((f) => f.endsWith('.tsx'))
    .map((f) => read(`components/${f}`)),
].join('\n')
assert(/className="[^"]*\bka-grain\b/.test(read('app/layout.tsx')), 'layout.tsx must mount .ka-grain on body')
assert(/className="[^"]*\bka-aurora\b/.test(markup), '.ka-aurora must be mounted by a component')
const heroCss = stripComments(read('components/Hero.module.css'))
assert(
  has(rules(heroCss, '.atmosphere'), /position:\s*absolute/) &&
    has(rules(heroCss, '.atmosphere'), /overflow:\s*hidden/),
  "the aurora's parent (.atmosphere) must be positioned and clip it",
)
assert(
  !has(rules(heroCss, '.hero'), /overflow\s*:/),
  '.hero must not clip: a focusable control at its edge would lose its focus ring',
)

// The masthead is drawn by Hero on request and the landing page requests it;
// it is the muted .ka-mono voice, not a second set of type declarations.
const mastheadTsx = read('components/Masthead.tsx')
assert(/className=\{`ka-mono /.test(mastheadTsx), 'the masthead must carry .ka-mono rather than redeclare its type')
const mastheadCss = stripComments(read('components/Masthead.module.css'))
assert(
  !/font-(family|size)|letter-spacing|text-transform/.test(mastheadCss),
  'Masthead.module.css must not redeclare the .ka-mono type',
)
assert(has(rules(mastheadCss, '.masthead'), /border-bottom:\s*1px solid/), 'the masthead needs a hairline beneath it')
assert(
  /@media[^{]*max-width[^{]*\{[^]*?\.place\s*\{[^}]*display:\s*none/.test(mastheadCss),
  'on a narrow screen the masthead must drop its centre rather than overflow',
)
assert(/masthead\s*\?\s*<Masthead\s*\/>/.test(read('components/Hero.tsx')), 'Hero must draw <Masthead /> only when asked')
assert(/<Hero\s+masthead\b/.test(read('content/index.mdx')), 'the landing page must pass masthead to Hero')
assert(/ka-mono[^>]*>NAIROBI, EAT \(UTC\+3\)/.test(read('app/layout.tsx')), 'the footer must carry the locality line in .ka-mono')

// Every page the site serves must carry frontmatter: generate-llms.mjs
// throws without a title and a description, which fails the build late. A
// page added without them should fail here, where the message says so.
for (const [file, body] of mdxFiles) {
  const front = /^---\r?\n([\s\S]*?)\r?\n---/.exec(body)?.[1] ?? ''
  assert(/^title:/m.test(front), `${file}: no title in the frontmatter`)
  assert(/^description:/m.test(front), `${file}: no description in the frontmatter`)
}

if (failures.length) {
  console.error(`check-furniture: ${failures.length} failure(s)`)
  for (const f of failures) console.error(`  FAIL: ${f}`)
  process.exit(1)
}
console.log(`check-furniture: ok (${mdx.length} pages, ${cssFiles.length} stylesheets)`)
