/**
 * Fail when the site stops being discoverable, or starts claiming too much.
 *
 * Runs against `out/` after a build. It checks six things:
 *
 *   1. The sitemap and the exported pages agree, in both directions. Every
 *      URL it lists must exist as a page (a listed 404 is worse than an
 *      unlisted page), and every exported page must be listed -- so a page
 *      that falls out of the page map is noticed rather than silently
 *      dropped, which matters here because three of the five sections are
 *      generated on every build.
 *   2. robots.txt names each AI crawler with its own stanza and an explicit
 *      Allow, and points at the sitemap. karani is a tool people reach for
 *      by asking an assistant, so this is not decoration.
 *   3. Every page carries a canonical link, and it points at that page.
 *   4. The structured data is a SoftwareApplication that implies no
 *      adoption. karani has one user, so there are no ratings, reviews or
 *      counts, and its version is pyproject.toml's rather than a copy that
 *      goes stale.
 *   5. The prose is published for models: llms.txt follows the convention
 *      and lists every page, llms-full.txt carries the real text, and every
 *      page has a markdown variant with no JSX, no MDX import and no
 *      component tag.
 *   6. None of that prose claims adoption karani does not have.
 *
 * The count of pages is deliberately not asserted. The page map is the
 * source of truth, and a hardcoded number rots the day somebody adds a
 * page -- or, here, the day somebody writes an ADR.
 *
 * Usage:
 *   node scripts/check-seo.mjs [out-dir]
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { toPlainMarkdown, packageVersion } from './generate-llms.mjs'
import { ORIGIN, REPOSITORY } from '../site.config.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const OUT = resolve(process.argv[2] ?? join(root, 'out'))
const failures = []
function assert(cond, message) {
  if (!cond) failures.push(message)
}

// Every path computation below maps a URL's pathname straight onto a file in
// out/, which only holds at a zone root. An origin with a path in it would
// make this check silently look in the wrong place, so it is refused here
// rather than diagnosed later (site.config.mjs says why a subpath does not
// work for this site at all).
if (new URL(ORIGIN).pathname !== '/') {
  console.error(`FAIL: ORIGIN (${ORIGIN}) has a path. This site must be served from a zone root; see web/site.config.mjs.`)
  process.exit(1)
}

const read = (p) => readFileSync(join(OUT, p), 'utf8')

for (const required of ['sitemap.xml', 'robots.txt', 'index.html']) {
  if (!existsSync(join(OUT, required))) {
    console.error(`FAIL: out/${required} does not exist (run "npm run build" first)`)
    process.exit(1)
  }
}

/** The file an exported URL is served from, under `trailingSlash: true`. */
function outPathFor(url) {
  const { pathname } = new URL(url)
  return join(OUT, pathname, pathname.endsWith('/') ? 'index.html' : '')
}

/** Every `index.html` under out/, as the URL it is served at. */
function exportedPages() {
  const pages = []
  const walk = (dir) => {
    for (const entry of readdirSync(dir)) {
      // Build machinery, not pages: the search index and Next's own assets.
      if (entry === '_next' || entry === '_pagefind') continue
      const full = join(dir, entry)
      if (statSync(full).isDirectory()) walk(full)
      else if (entry === 'index.html') {
        const rel = relative(OUT, dir)
        pages.push(rel === '' ? `${ORIGIN}/` : `${ORIGIN}/${rel.split('\\').join('/')}/`)
      }
    }
  }
  walk(OUT)
  return pages
}

/**
 * The 404 is exported as a page and Next marks it noindex, so it has no
 * place in a sitemap. It is the only page allowed to be noindex: a content
 * page that turned noindex by accident must still fail. It is not exempt
 * from anything else -- it declares a canonical, its own, like every other
 * page.
 */
const isNoindex = (html) => /<meta[^>]+name="robots"[^>]+content="[^"]*noindex/i.test(html)
const NOINDEX_ALLOWED = new Set([`${ORIGIN}/404/`])

// 1. sitemap <-> exported pages -----------------------------------------
const sitemap = read('sitemap.xml')
const urls = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1])
assert(urls.length > 0, 'sitemap.xml lists no URLs')
assert(new Set(urls).size === urls.length, 'sitemap.xml lists a URL more than once')

for (const u of urls) {
  assert(u.startsWith(`${ORIGIN}/`), `sitemap lists ${u}, which is not on ${ORIGIN}`)
  assert(u.endsWith('/'), `sitemap lists ${u} without the trailing slash the export uses`)
  assert(existsSync(outPathFor(u)), `sitemap lists ${u} but no page was exported`)
}

const listed = new Set(urls)
for (const page of exportedPages()) {
  const html = readFileSync(outPathFor(page), 'utf8')
  if (isNoindex(html)) {
    assert(NOINDEX_ALLOWED.has(page), `${page} is noindex but is not the 404 page`)
    continue
  }
  assert(listed.has(page), `${page} was exported but is missing from the sitemap`)
}
assert(!urls.some((u) => /\/404\/?$/.test(u)), 'sitemap lists the 404 page')

// Three of the five sections are generated, so the sitemap is the one place
// that would notice a generator writing nothing at all.
for (const section of ['/reference/cli/', '/reference/mcp/', '/reference/config/', '/decisions/']) {
  assert(listed.has(`${ORIGIN}${section}`), `the sitemap has no ${section}; did its generator run?`)
}
const decisions = urls.filter((u) => u.startsWith(`${ORIGIN}/decisions/`) && u !== `${ORIGIN}/decisions/`)
const adrFiles = readdirSync(join(root, '..', 'docs/adrs')).filter((f) => /^\d{4}-.*\.md$/.test(f))
assert(
  decisions.length === adrFiles.length,
  `the sitemap has ${decisions.length} decision pages for ${adrFiles.length} ADRs in docs/adrs/`,
)

// 2. robots.txt ---------------------------------------------------------
const robots = read('robots.txt')
assert(/^User-agent:\s*\*\s*$/im.test(robots), 'robots.txt has no stanza for *')

/** The directives of the stanza that opens with `User-agent: <bot>`. */
function stanzaFor(bot) {
  const blocks = robots.split(/\n\s*\n/)
  return blocks.find((b) => new RegExp(`^User-agent:\\s*${bot}\\s*$`, 'im').test(b)) ?? ''
}
for (const bot of ['GPTBot', 'ClaudeBot', 'PerplexityBot', 'Google-Extended', 'CCBot']) {
  const stanza = stanzaFor(bot)
  assert(stanza !== '', `${bot} not named in robots.txt`)
  assert(/^Allow:\s*\/\s*$/im.test(stanza), `${bot} is named but not explicitly allowed`)
  assert(!/^Disallow:\s*\/\s*$/im.test(stanza), `${bot} is disallowed from the whole site`)
}
assert(
  new RegExp(`^Sitemap:\\s*${ORIGIN.replace(/[./]/g, '\\$&')}/sitemap\\.xml\\s*$`, 'im').test(robots),
  'robots.txt does not point at the sitemap',
)

// 3. canonicals ---------------------------------------------------------
const canonicalOf = (html) =>
  html.match(/<link[^>]+rel="canonical"[^>]*>/)?.[0].match(/href="([^"]+)"/)?.[1]
for (const page of exportedPages()) {
  const html = readFileSync(outPathFor(page), 'utf8')
  const canonical = canonicalOf(html)
  assert(canonical !== undefined, `${page} has no canonical link`)
  if (canonical !== undefined) {
    assert(canonical === page, `${page} declares canonical ${canonical}, expected itself`)
  }
}

// 4. structured data ----------------------------------------------------
function jsonLdBlocks(html) {
  return [
    ...html.matchAll(/<script[^>]+type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/g),
  ].map((m) => JSON.parse(m[1]))
}
const blocks = jsonLdBlocks(read('index.html'))
assert(blocks.length === 1, `index.html carries ${blocks.length} JSON-LD blocks, expected 1`)
const ld = blocks[0] ?? {}
assert(ld['@type'] === 'SoftwareApplication', 'JSON-LD is not a SoftwareApplication')
assert(ld.name === 'karani', 'JSON-LD name is not karani')
assert(ld.sameAs === REPOSITORY, 'JSON-LD repository (sameAs) is wrong')
assert(ld.author?.name === 'Kelyn Njeri', 'JSON-LD author is not Kelyn Njeri')
assert(ld.programmingLanguage !== undefined, 'JSON-LD has no programmingLanguage')
assert(ld.applicationCategory !== undefined, 'JSON-LD has no applicationCategory')
assert(ld.operatingSystem !== undefined, 'JSON-LD has no operatingSystem')
assert(/MIT/.test(JSON.stringify(ld.license ?? '')), 'JSON-LD licence is not MIT')

// karani has one user. None of these may appear, at any depth.
const ADOPTION = [
  'aggregateRating',
  'review',
  'rating',
  'ratingValue',
  'ratingCount',
  'reviewCount',
  'interactionCount',
  'interactionStatistic',
  'userInteractionCount',
  'downloadCount',
  'installCount',
  'downloadUrl',
  'award',
  'offers',
]
function keysOf(value, found = new Set()) {
  if (Array.isArray(value)) value.forEach((v) => keysOf(v, found))
  else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      found.add(k)
      keysOf(v, found)
    }
  }
  return found
}
const present = keysOf(ld)
for (const key of ADOPTION) {
  assert(!present.has(key), `JSON-LD carries "${key}": karani has one user`)
}
const prose = String(ld.description ?? '')
for (const word of ['production-ready', 'production ready', 'trusted', 'popular', 'widely used']) {
  assert(!prose.toLowerCase().includes(word), `JSON-LD description claims "${word}"`)
}

// 5. machine-readable prose ---------------------------------------------
const llms = read('llms.txt')
assert(/^# karani\n/.test(llms), 'llms.txt must open with "# karani"')
assert(/^> .+/m.test(llms), 'llms.txt needs a one-paragraph summary blockquote')
assert(
  llms.includes(`Version ${packageVersion()}`),
  `llms.txt must quote the version from pyproject.toml (${packageVersion()})`,
)
for (const section of ['Start here', 'Guides', 'Reference', 'Operations', 'Decisions', 'Project']) {
  assert(
    new RegExp(`^## ${section}$`, 'm').test(llms),
    `llms.txt has no "## ${section}" section (sections mirror the nav)`,
  )
}
const entries = [...llms.matchAll(/^- \[(.+?)\]\((https:\/\/[^)\s]+)\): (.+)$/gm)]
assert(entries.length > 0, 'llms.txt lists no pages')

// Every page the sitemap lists is in llms.txt, as a link to its markdown,
// and every link lands on a file that exists. The page count itself is not
// asserted: the sitemap is the source of truth.
const linked = new Set(entries.map((e) => e[2]))
for (const u of urls) {
  const md = `${u}index.md`
  assert(linked.has(md), `${u} is in the sitemap but not in llms.txt`)
  assert(existsSync(join(OUT, new URL(md).pathname)), `${md} is not written (llms.txt links to it)`)
}
for (const [, title, link] of entries) {
  assert(
    link.startsWith(`${ORIGIN}/`) && link.endsWith('index.md'),
    `llms.txt links "${title}" to ${link}`,
  )
  assert(existsSync(join(OUT, new URL(link).pathname)), `llms.txt links ${link}, which does not exist`)
}

const full = read('llms-full.txt')
assert(full.length > 100000, `llms-full.txt is ${full.length} characters; the site's prose should exceed 100,000`)
for (const u of urls) {
  assert(full.includes(`Source: ${u}\n`), `llms-full.txt does not contain ${u}`)
}

/** A markdown file with its code, fenced and inline, taken out. */
const withoutCode = (md) =>
  md
    .replace(/^(\s*)(`{3,}|~{3,})[^\n]*\n[\s\S]*?^\s*\2[`~]*\s*$/gm, '')
    .replace(/(`+)[\s\S]*?\1/g, '')

/** Every `index.md` under out/, as [path, text]. */
function generatedMarkdown() {
  const found = []
  const walk = (dir) => {
    for (const entry of readdirSync(dir)) {
      if (entry === '_next' || entry === '_pagefind') continue
      const full = join(dir, entry)
      if (statSync(full).isDirectory()) walk(full)
      else if (entry === 'index.md') found.push([relative(OUT, full), readFileSync(full, 'utf8')])
    }
  }
  walk(OUT)
  return found
}
const mds = generatedMarkdown()
assert(mds.length > 0, 'no markdown pages were written')

// A markdown variant sits beside every page that is in the sitemap.
for (const u of urls) {
  assert(existsSync(join(OUT, new URL(u).pathname, 'index.md')), `${u} has no index.md beside it`)
}

const COMPONENT_TAG = /<\/?(Verdict|RunReport|Hero|Band|Surface|SurfaceGrid|Note|Stat|Stats|Lede|Action)\b/
const JSX_TAG = /<\/?[A-Z][A-Za-z0-9.]*(?=[\s/>])/
for (const [path, md] of mds) {
  // An `import` is MDX only outside a code block.
  const prose = withoutCode(md)
  assert(!/^import\s.+\sfrom\s+['"]/m.test(prose), `${path}: an MDX import leaked into the markdown`)
  assert(!COMPONENT_TAG.test(prose), `${path}: a component tag leaked instead of its text`)
  // Backstop for a component added later: generate-llms.mjs only converts
  // the ones it knows, so any other capitalised tag outside code is raw JSX.
  const unknownTag = JSX_TAG.exec(prose)
  assert(!unknownTag, `${path}: raw JSX outside a code block (${unknownTag?.[0]}); teach generate-llms.mjs the component`)
  assert(!/\{\s*samples\./.test(md), `${path}: an unresolved {samples.*} expression`)
  assert(!/<\/?>/.test(prose), `${path}: a JSX fragment leaked`)
  assert(!/^---\n(title|description):/m.test(md), `${path}: frontmatter leaked`)
  assert(!/\]\(\/(?!\/)/.test(prose), `${path}: a site-relative link (it breaks out of context)`)
  assert(/^# .+\n\nSource: https:\/\//.test(md), `${path}: does not open with its title and address`)
}

// The conversion keeps the text a component stands for, instead of just
// dropping the tag. The first case is the one that matters most: a
// qualification result is not in the MDX at all, only a reference to it, so
// a model reading the page would otherwise get a qualification page with no
// qualification in it.
const qualification = mds.find(([p]) => p === 'guides/qualification/index.md')?.[1] ?? ''
assert(/\*\*qualified, fit \d+\/100\*\*/.test(qualification), 'the qualification page lost its verdict')
assert(
  /Strengths, each with the resume line behind it/.test(qualification),
  'the qualification page lost the strengths and their evidence',
)
assert(
  /Scored by .+ under prompt version /.test(qualification),
  'the qualification page lost the model and prompt version: a fit score is not comparable without them',
)
const hunting = mds.find(([p]) => p === 'guides/hunting/index.md')?.[1] ?? ''
assert(/karani run/.test(hunting) && /dropped\s+/.test(hunting), 'the hunting page lost its transcript')

const converted = toPlainMarkdown(`---
title: t
---

import { x } from '../x'

<Lede>Opening **prose**.</Lede>

<Note kind="tip" title="A title">
Body text.
</Note>

<SurfaceGrid>
  <Surface href="/a/" name="A surface" invocation="karani x">
    Because of this.
  </Surface>
</SurfaceGrid>

<Stats>
  <Stat value="3 / 4" label="Things" note="somewhere" />
</Stats>

<RunReport note="karani run">{samples.transcripts.hunt}</RunReport>

<Verdict note="karani qualify">{samples.verdicts.qualified}</Verdict>

\`dict[str, int]\` stays, and so does:

\`\`\`python
def f() -> dict[str, int]: ...
import karani
\`\`\`
`)
for (const wanted of [
  'Opening **prose**.',
  '**Tip: A title**',
  'Body text.',
  '**A surface**',
  'Because of this.',
  `${ORIGIN}/a/`,
  '**3 / 4** Things',
  'fetched',
  '**qualified, fit 91/100**',
  '`dict[str, int]` stays',
  'def f() -> dict[str, int]: ...',
  'import karani',
]) {
  assert(converted.includes(wanted), `toPlainMarkdown lost "${wanted}"`)
}
assert(!/import \{ x \}/.test(converted), 'toPlainMarkdown kept an MDX import')
assert(!COMPONENT_TAG.test(withoutCode(converted)), 'toPlainMarkdown left a component tag')

// 6. the honesty rule, automated ----------------------------------------
// karani has one user. This is a check for the specific phrases we know to
// avoid, not a complete guard: a claim worded some other way ("used by 40
// engineers") passes it. It fails closed on these.
const CLAIMS = [
  'trusted by',
  'used by thousands',
  'industry-leading',
  'customers love',
  'widely used',
  'widely adopted',
  'production-ready',
  'production ready',
  'battle-tested',
  'adopted by',
  'used in production',
  'thousands of users',
  'loved by',
  'world-class',
]
const phrasePattern = (w) => new RegExp(w.replace(/[-\s]/g, '[-\\s]'), 'gi')
for (const [label, text] of [['llms.txt', llms], ['llms-full.txt', full]]) {
  for (const w of CLAIMS) {
    for (const m of text.matchAll(phrasePattern(w))) {
      const context = text.slice(Math.max(0, m.index - 40), m.index + w.length + 40).replace(/\s+/g, ' ')
      assert(false, `${label} claims "${w}" (...${context}...): karani has one user`)
    }
  }
}

if (failures.length > 0) {
  console.error(`FAIL: ${failures.length} problem(s)`)
  for (const f of failures) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(
  `OK: ${urls.length} sitemap URLs all exported, every exported page listed and canonical, ` +
    `AI crawlers named, structured data claims no adoption, llms.txt lists every page, ` +
    `${mds.length} markdown pages are free of JSX, prose claims no adoption.`,
)
