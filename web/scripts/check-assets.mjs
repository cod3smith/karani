/**
 * Fail when the generated raster assets stop meeting their spec.
 *
 * Two routes produce files at export time:
 * app/opengraph-image.png/route.tsx (the social card) and
 * app/apple-icon.png/route.tsx (the home-screen icon). Plus
 * app/not-found.tsx, which exports as 404.html. This checks the build
 * output, so it needs `out/` to exist: run `npm run build` first.
 *
 * The two images are route handlers in folders named `*.png`, so they
 * export as `out/opengraph-image.png` and `out/apple-icon.png`. (The file
 * conventions would write them with no extension, which GitHub Pages types
 * by extension and so would likely serve as application/octet-stream --
 * and scrapers check Content-Type.) Each is checked by its PNG signature
 * and the width and height in its IHDR chunk, and every reference to them
 * in the emitted HTML must resolve to a file that exists.
 *
 * Usage:
 *   node scripts/check-assets.mjs
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ORIGIN } from '../site.config.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const OUT = join(root, 'out')

const failures = []
function assert(cond, message) {
  if (!cond) failures.push(message)
}

// ---- the built rasters -----------------------------------------------------

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

/** Width and height from a PNG's IHDR, or null if the file is not a PNG. */
function pngSize(path) {
  const bytes = readFileSync(path)
  if (bytes.length < 24 || !bytes.subarray(0, 8).equals(PNG_SIGNATURE)) return null
  if (bytes.subarray(12, 16).toString('latin1') !== 'IHDR') return null
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) }
}

function checkRaster(file, width, height) {
  const path = join(OUT, file)
  if (!existsSync(path)) {
    failures.push(`out/${file} does not exist (run npm run build)`)
    return
  }
  const size = pngSize(path)
  assert(size !== null, `out/${file} is not a PNG`)
  if (size) {
    assert(
      size.width === width && size.height === height,
      `out/${file}: expected ${width}x${height}, got ${size.width}x${size.height}`,
    )
  }
}

if (!existsSync(OUT)) {
  console.error('FAIL: out/ does not exist; run npm run build first')
  process.exit(1)
}

checkRaster('opengraph-image.png', 1200, 630)
checkRaster('apple-icon.png', 180, 180)

// GitHub Pages types a response by its extension, and scrapers check
// Content-Type, so an extensionless image is one they may refuse.
for (const bare of ['opengraph-image', 'apple-icon']) {
  assert(!existsSync(join(OUT, bare)), `out/${bare} exists without an extension`)
}

// ---- every reference to them -----------------------------------------------

function walk(dir) {
  const files = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name !== '_pagefind' && entry.name !== '_next') files.push(...walk(path))
    } else if (/\.(html|txt)$/.test(entry.name)) files.push(path)
  }
  return files
}

for (const file of walk(OUT)) {
  // `/opengraph-image` or `/apple-icon` not followed by `.png` is a
  // reference to the extensionless file that does not exist.
  const text = readFileSync(file, 'utf8')
  const bad = text.match(/\/(?:opengraph-image|apple-icon)(?!\.png)(?=["'?#\s\\)])/g)
  if (bad) {
    failures.push(`${file.slice(OUT.length + 1)} has an extensionless image reference (${bad.length}x)`)
  }
}

const readOut = (p) => (existsSync(join(OUT, p)) ? readFileSync(join(OUT, p), 'utf8') : null)

/** The value of a tag's attribute, or null. */
const attr = (html, tag, key, keyValue, valueName) =>
  html.match(new RegExp(`<${tag}[^>]*${key}="${keyValue}"[^>]*${valueName}="([^"]*)"`))?.[1] ??
  html.match(new RegExp(`<${tag}[^>]*${valueName}="([^"]*)"[^>]*${key}="${keyValue}"`))?.[1] ??
  null

/** A reference must be absolute on the site's own origin and name a file in out/. */
function resolvesToFile(ref) {
  if (ref === null) return false
  const path = ref.replace(ORIGIN, '').split(/[?#]/)[0]
  return path.startsWith('/') && existsSync(join(OUT, path))
}

// One page from each kind: the landing page, the 404, a hand-written guide,
// and a generated reference page. A social card that only works on the
// landing page is the usual shape of this bug.
for (const page of [
  'index.html',
  '404.html',
  'guides/qualification/index.html',
  'reference/cli/index.html',
  'decisions/0012-autopilot-draft-human-send/index.html',
]) {
  const html = readOut(page)
  if (html === null) {
    failures.push(`out/${page} is missing`)
    continue
  }
  const og = attr(html, 'meta', 'property', 'og:image', 'content')
  const tw = attr(html, 'meta', 'name', 'twitter:image', 'content')
  const apple = attr(html, 'link', 'rel', 'apple-touch-icon', 'href')
  assert(resolvesToFile(og) && /\.png$/.test(og ?? ''), `${page}: og:image (${og}) does not resolve to a .png in out/`)
  assert(resolvesToFile(tw) && /\.png$/.test(tw ?? ''), `${page}: twitter:image (${tw}) does not resolve to a .png in out/`)
  assert(
    resolvesToFile(apple) && /\.png$/.test(apple ?? ''),
    `${page}: apple-touch-icon (${apple}) does not resolve to a .png in out/`,
  )
  assert((html.match(/property="og:image"/g) ?? []).length === 1, `${page}: expected exactly one og:image tag`)
  assert(/property="og:image:width" content="1200"/.test(html), `${page}: og:image:width must be 1200`)
  assert(/property="og:image:height" content="630"/.test(html), `${page}: og:image:height must be 630`)
}

// ---- the masthead and the locality line ------------------------------------
//
// The masthead is the landing page's cover: it must be on the landing page
// exactly once and on no other page. The locality line is the footer's:
// every page that has a footer must carry it. Both were named in the design
// and are the kind of thing that gets built once and then quietly dropped
// by a refactor, which is why they are asserted against the output rather
// than the source.

const MASTHEAD = /<div[^>]*\bdata-masthead\b[^>]*>/g
const LOCALITY = 'NAIROBI, EAT (UTC+3)'
for (const file of walk(OUT).filter((f) => f.endsWith('.html'))) {
  const rel = file.slice(OUT.length + 1)
  const html = readFileSync(file, 'utf8')
  const mastheads = html.match(MASTHEAD)?.length ?? 0
  if (rel === 'index.html') {
    assert(mastheads === 1, `index.html: expected exactly one masthead, found ${mastheads}`)
    const m = html.match(/<div[^>]*data-masthead[^>]*>([\s\S]*?)<\/div>/)?.[1] ?? ''
    const text = m.replace(/<[^>]+>/g, '|').split('|').filter(Boolean)
    assert(
      text.join(' / ') === `VOL. 01 - THE CLERK / ${LOCALITY} / MMXXVI`,
      `index.html: the masthead reads "${text.join(' / ')}"`,
    )
    assert(
      html.indexOf('data-masthead') < html.search(/<h1[ >]/),
      'index.html: the masthead must sit above the hero title',
    )
  } else {
    assert(mastheads === 0, `${rel}: the masthead appears here, but only the landing page has one`)
  }
  if (html.includes('<footer')) {
    assert(html.includes(`${LOCALITY} · MMXXVI`), `${rel}: the footer is missing the locality line`)
  }
}

// ---- the 404 ----------------------------------------------------------------

const notFound = readOut('404.html')
if (notFound) {
  assert(/Nothing is filed at this address/.test(notFound), '404.html is not the refusal page')
  assert(/var\(--ka-refuse\)/.test(notFound), '404.html must draw the cut stroke in var(--ka-refuse)')
}

// ---- the search index -------------------------------------------------------
//
// Search is one of the three things the site has to do, and it is built by a
// separate tool after the export. A missing or empty index looks exactly
// like a working site until somebody types in the box.

const entry = readOut('_pagefind/pagefind-entry.json')
assert(entry !== null, 'out/_pagefind/pagefind-entry.json is missing; the search index was not built')
if (entry) {
  const fragments = existsSync(join(OUT, '_pagefind/fragment'))
    ? readdirSync(join(OUT, '_pagefind/fragment')).length
    : 0
  const htmlPages = walk(OUT).filter((f) => f.endsWith('.html')).length
  assert(fragments > 0, 'the search index has no fragments; nothing was indexed')
  // One fragment per indexable page. The 404 is noindex and carries no
  // pagefind body, so a small shortfall is expected; half the site missing
  // is the failure this catches.
  assert(
    fragments >= htmlPages - 3,
    `the search index has ${fragments} fragments for ${htmlPages} pages; most of the site was not indexed`,
  )
}

if (failures.length) {
  for (const f of failures) console.error(`FAIL: ${f}`)
  process.exit(1)
}
console.log('check-assets: ok')
