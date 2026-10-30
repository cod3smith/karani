#!/usr/bin/env node
/**
 * Write out/CNAME, the file GitHub Pages reads to learn its custom domain.
 *
 * Derived from ORIGIN rather than kept as a static file in public/, because
 * the domain already lives in site.config.mjs and a second copy is a second
 * thing to forget. A static public/CNAME that disagreed with ORIGIN would
 * give a site whose canonicals point at one host and whose server answers
 * on another, and nothing would say so.
 *
 * **This is not cosmetic for this site.** A GitHub Pages *project* site
 * with no custom domain is served at `<account>.github.io/<repo>` — a
 * subpath, which is exactly the shape measured to break every prose link
 * and both declared icons (see site.config.mjs). The custom domain is what
 * makes the site a zone root, so this file is load-bearing: without it the
 * site deploys and looks fine and most of its links 404.
 *
 * Harmless on any other host. Netlify, Cloudflare Pages and Vercel all
 * ignore a stray CNAME file, so the build does not need to know where it
 * is going.
 *
 * Usage:
 *   node scripts/write-cname.mjs [out-dir]
 */
import { existsSync } from 'node:fs'
import { writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ORIGIN } from '../site.config.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const out = resolve(process.argv[2] ?? join(root, 'out'))

if (!existsSync(join(out, 'index.html'))) {
  console.error(`cname: ${out}/index.html does not exist; run "next build" first`)
  process.exit(1)
}

const { hostname, pathname } = new URL(ORIGIN)

// A path in ORIGIN means somebody has configured a subpath deployment,
// which this site does not support. Failing here is much cheaper than
// finding out from a 404 on every link after a deploy.
if (pathname !== '/') {
  console.error(
    `cname: ORIGIN (${ORIGIN}) has a path. This site must be served from a ` +
      `zone root; see web/site.config.mjs for the measurement.`,
  )
  process.exit(1)
}

await writeFile(join(out, 'CNAME'), `${hostname}\n`, 'utf8')
console.log(`cname: ${hostname}`)
