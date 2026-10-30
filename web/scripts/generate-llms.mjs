#!/usr/bin/env node
/**
 * Publish the docs as prose a language model can read, not HTML it has to parse.
 *
 * Runs after `next build` and writes three things into out/:
 *
 *   llms.txt        the index: a title, a one-paragraph summary, then one
 *                   section per navigation group with a link and a
 *                   description for every page (the llms.txt convention).
 *   llms-full.txt   every page's prose, concatenated in navigation order.
 *   <route>/index.md  each page on its own, beside its index.html. The site
 *                   exports with trailingSlash, so a page is
 *                   <route>/index.html and its markdown is <route>/index.md:
 *                   same directory, no collision.
 *
 * The source is the MDX under content/, not the rendered HTML. That is what
 * makes the output prose: the HTML is full of chrome and a model reading it
 * has to guess where the content is.
 *
 * karani is a tool people reach for by asking an assistant how to do
 * something, which makes this more than a nicety. The pages a model is most
 * often asked about -- the CLI verbs, the MCP tools, the config keys -- are
 * the generated ones, written on every docs build and gitignored, and
 * `npm run build` runs the generators first so they are always present here.
 *
 * toPlainMarkdown is the step that decides whether a model reads the pages
 * or reads the JSX. The site's components carry real content: a <Verdict>
 * holds a whole qualification result and that result is not in the MDX at
 * all, it is a reference to a generated module. Stripping the tags and
 * keeping nothing would hand a model a qualification page with no
 * qualification in it. So each component renders to the text it stands for,
 * and scripts/check-seo.mjs fails if a tag, an import or an unresolved
 * expression survives.
 *
 * Plain Node, no dependencies. Nothing is parsed with a library: the MDX
 * this site uses is small and regular.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { ORIGIN } from '../site.config.mjs'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
export { ORIGIN }
const CONTENT = join(ROOT, 'content')

// ---------------------------------------------------------------------------
// Reading the content tree

/** Parse the small YAML subset the frontmatter uses: `key: value` lines. */
export function parseFrontmatter(source, where = 'page') {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(source)
  if (!m) return { data: {}, body: source }
  const data = {}
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z][\w-]*):\s*(.*)$/.exec(line)
    if (!kv) {
      if (line.trim() !== '') throw new Error(`${where}: frontmatter line not understood: ${line}`)
      continue
    }
    let value = kv[2].trim()
    if (value.startsWith('"')) value = JSON.parse(value)
    else if (value.startsWith("'")) value = value.slice(1, -1).replace(/''/g, "'")
    else if (/^[>|]/.test(value)) throw new Error(`${where}: block scalars are not supported in frontmatter`)
    data[kv[1]] = value
  }
  return { data, body: source.slice(m[0].length) }
}

/**
 * A directory's `_meta.ts`, as an ordered object. The file is TypeScript,
 * but what it exports is a literal; strip the one type annotation and
 * evaluate it.
 */
function loadMeta(dir) {
  const file = join(dir, '_meta.ts')
  if (!existsSync(file)) return {}
  const js = readFileSync(file, 'utf8')
    .replace(/^import type .*$/gm, '')
    .replace(/const meta: MetaRecord =/, 'const meta =')
    .replace(/export default meta/, 'return meta')
  return new Function(js)()
}

const metaTitle = (entry, fallback) =>
  typeof entry === 'string' ? entry : (entry?.title ?? fallback)

/**
 * The entries of a directory in navigation order: what `_meta.ts` lists, in
 * its order, then anything it does not list, alphabetically (which is what
 * the sidebar does). `index` leads when present.
 */
function orderedEntries(dir) {
  const meta = loadMeta(dir)
  const onDisk = new Set()
  for (const name of readdirSync(dir)) {
    if (name.startsWith('_') || name.startsWith('.')) continue
    const full = join(dir, name)
    if (statSync(full).isDirectory()) onDisk.add(name)
    else if (name.endsWith('.mdx')) onDisk.add(name.slice(0, -4))
  }
  const keys = []
  if (onDisk.has('index') && !('index' in meta)) keys.push('index')
  for (const key of Object.keys(meta)) {
    const entry = meta[key]
    if (typeof entry === 'object' && (entry.type === 'separator' || entry.href)) continue
    if (onDisk.has(key)) keys.push(key)
  }
  for (const key of [...onDisk].sort()) if (!keys.includes(key)) keys.push(key)
  return keys.map((key) => ({ key, title: metaTitle(meta[key], key) }))
}

function pagesIn(dir, routePrefix, section) {
  const pages = []
  for (const { key } of orderedEntries(dir)) {
    const mdx = join(dir, `${key}.mdx`)
    if (existsSync(mdx)) {
      const route = key === 'index' ? `${routePrefix}/` : `${routePrefix}/${key}/`
      pages.push(pageFrom(mdx, route, section))
    } else if (statSync(join(dir, key)).isDirectory()) {
      pages.push(...pagesIn(join(dir, key), `${routePrefix}/${key}`, section))
    }
  }
  return pages
}

function pageFrom(file, route, section) {
  const where = file.slice(ROOT.length + 1)
  const { data, body } = parseFrontmatter(readFileSync(file, 'utf8'), where)
  if (!data.title) throw new Error(`${where}: no title in the frontmatter`)
  if (!data.description) throw new Error(`${where}: no description in the frontmatter`)
  return { route, title: data.title, description: data.description, body, section, file: where }
}

/**
 * Every page, in navigation order, each tagged with the top-level group it
 * sits in. A group is a top-level entry of content/_meta.ts: a directory
 * (the pages inside it, in its own order) or a single page.
 */
export function collectPages() {
  const pages = []
  for (const { key, title } of orderedEntries(CONTENT)) {
    const mdx = join(CONTENT, `${key}.mdx`)
    if (existsSync(mdx)) {
      pages.push(pageFrom(mdx, key === 'index' ? '/' : `/${key}/`, title))
    } else {
      pages.push(...pagesIn(join(CONTENT, key), `/${key}`, title))
    }
  }
  return pages
}

// ---------------------------------------------------------------------------
// The data the components refer to

/**
 * The generated modules an MDX expression may name, each from its
 * lib/<name>.generated.ts. Named here once and nowhere else: a new
 * generator that a page imports needs one line added, and until this list
 * has it the expression fails to resolve rather than rendering as its own
 * source.
 */
const GENERATED = ['samples']

/**
 * lib/*.generated.ts export plain literals. Pages pass them to components
 * as `{samples.transcripts.hunt}`, so to render a component to text the
 * reference has to be resolved.
 */
let generatedData
function loadGenerated() {
  if (generatedData) return generatedData
  generatedData = {}
  for (const name of GENERATED) {
    const file = join(ROOT, 'lib', `${name}.generated.ts`)
    if (!existsSync(file)) {
      throw new Error(`lib/${name}.generated.ts is missing; run "npm run generate" first`)
    }
    const js = readFileSync(file, 'utf8')
      .replace(/^export type .*$/gm, '')
      .replace(/^export const (\w+) =/m, 'const $1 =')
      .replace(/\} as const/, '}')
    generatedData[name] = new Function(`${js}\nreturn ${name}`)()
  }
  return generatedData
}

// ---------------------------------------------------------------------------
// MDX -> plain markdown

/** Components in the global MDX map (web/mdx-components.tsx) that carry content. */
const COMPONENTS = new Set([
  'Verdict',
  'RunReport',
  'Note',
  'Stat',
  'Stats',
  'Surface',
  'SurfaceGrid',
  'Lede',
  'Hero',
  'Action',
  'Band',
])

const abs = (href) => (href.startsWith('/') ? `${ORIGIN}${href}` : href)
const collapse = (s) => s.replace(/\s+/g, ' ').trim()

/** Index just past the `}` that closes the `{` at `i`, skipping JS strings. */
function skipBraces(s, i) {
  let depth = 0
  for (; i < s.length; i++) {
    const c = s[i]
    if (c === '"' || c === "'") {
      for (i++; i < s.length && s[i] !== c; i++) if (s[i] === '\\') i++
    } else if (c === '`') {
      for (i++; i < s.length && s[i] !== '`'; i++) {
        if (s[i] === '\\') i++
        else if (s[i] === '$' && s[i + 1] === '{') i = skipBraces(s, i + 1) - 1
      }
    } else if (c === '{') depth++
    else if (c === '}' && --depth === 0) return i + 1
  }
  throw new Error('unbalanced { in MDX')
}

/** An attribute value that is JSX (`{<>text <Action .../></>}`) reduced to text. */
function jsxToText(raw) {
  return collapse(
    raw
      .replace(/<Action\b([^>]*)>([\s\S]*?)<\/Action>/g, (_, attrs, text) => {
        const href = /href="([^"]*)"/.exec(attrs)?.[1]
        return href ? `[${collapse(text)}](${abs(href)})` : collapse(text)
      })
      .replace(/<\/?[A-Za-z]*[^>]*>/g, ' '),
  ).replace(/\) \[/g, ') · [')
}

/** Parse a tag's attributes, starting just after its name. */
function parseAttrs(s, i) {
  const attrs = {}
  for (;;) {
    while (/\s/.test(s[i])) i++
    if (s.startsWith('/>', i)) return { attrs, end: i + 2, selfClosing: true }
    if (s[i] === '>') return { attrs, end: i + 1, selfClosing: false }
    const name = /^[A-Za-z_][\w-]*/.exec(s.slice(i))?.[0]
    if (!name) throw new Error(`cannot parse attributes near: ${s.slice(i, i + 40)}`)
    i += name.length
    if (s[i] !== '=') {
      attrs[name] = true
      continue
    }
    i++
    if (s[i] === '"' || s[i] === "'") {
      const close = s.indexOf(s[i], i + 1)
      attrs[name] = s.slice(i + 1, close)
      i = close + 1
    } else if (s[i] === '{') {
      const end = skipBraces(s, i)
      const inner = s.slice(i + 1, end - 1).trim()
      // `{"a string"}` and `{`a template`}` are just strings; anything else is JSX.
      attrs[name] = /^["'`]/.test(inner) ? new Function(`return ${inner}`)() : jsxToText(inner)
      i = end
    } else {
      throw new Error(`cannot parse attribute value near: ${s.slice(i, i + 40)}`)
    }
  }
}

const fenced = (text, info = 'text') => {
  const longest = Math.max(2, ...[...text.matchAll(/`+/g)].map((m) => m[0].length))
  const fence = '`'.repeat(longest + 1)
  return `\n\n${fence}${info}\n${text.replace(/\s+$/, '')}\n${fence}\n\n`
}

/** Remove the indentation the MDX source put on a component's children. */
function dedent(text) {
  const lines = text.split('\n')
  const indents = lines.filter((l) => l.trim() !== '').map((l) => /^[ \t]*/.exec(l)[0].length)
  const strip = indents.length ? Math.min(...indents) : 0
  return lines.map((l) => l.slice(Math.min(strip, /^[ \t]*/.exec(l)[0].length))).join('\n')
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1)

/**
 * A qualification result as prose.
 *
 * The <Verdict> component is handed a whole object rather than a string, so
 * there is no text in the MDX to fall back on -- this function is the only
 * thing standing between a model and a qualification page with no
 * qualification in it. It keeps what the plate shows and in the same order:
 * the verdict and score, the strengths WITH their evidence (the evidence is
 * the load-bearing half, since a claim karani cannot cite is dropped), the
 * gaps with severities, the flags, the angle, the reason, and the model and
 * prompt version -- because a fit score is only comparable within the pair
 * that produced it.
 */
function verdictToText(r) {
  const lines = [
    '',
    `**${r.verdict}, fit ${r.fit_score}/100** — ${r.job.title} at ${r.job.company} (job ${r.job.id})`,
    '',
  ]
  if (r.strengths?.length) {
    lines.push('Strengths, each with the resume line behind it:', '')
    for (const s of r.strengths) {
      lines.push(`- ${s.claim} — "${s.evidence_from_resume}"`)
    }
    lines.push('')
  }
  if (r.gaps?.length) {
    lines.push('Gaps:', '')
    for (const g of r.gaps) {
      lines.push(`- (${g.severity}) ${g.requirement}${g.mitigation ? ` — ${g.mitigation}` : ''}`)
    }
    lines.push('')
  }
  if (r.red_flags?.length) {
    lines.push('Red flags:', '')
    for (const f of r.red_flags) lines.push(`- ${f}`)
    lines.push('')
  }
  if (r.recommended_positioning) {
    lines.push(`Recommended positioning: ${r.recommended_positioning}`, '')
  }
  const reason = r.verdict === 'skip' ? r.why_skip : r.why_apply
  if (reason) lines.push(`Verdict reason: ${reason}`, '')
  lines.push(`Scored by ${r.model} under prompt version ${r.prompt_version}.`, '')
  return lines.join('\n')
}

/**
 * What each component becomes. `content` is the already-converted children.
 * A component with no entry here would be left as a tag, and the check in
 * check-seo.mjs would then fail, so the list cannot silently fall behind.
 */
const RENDER = {
  Lede: (_, content) => `\n\n${dedent(content).trim()}\n\n`,
  SurfaceGrid: (_, content) => `\n\n${dedent(content).trim()}\n\n`,
  Stats: (_, content) => `\n\n${content.trim()}\n\n`,
  Stat: (a) => `\n- **${a.value}** ${a.label ?? ''}${a.note ? ` (${a.note})` : ''}\n`,
  Surface: (a, content) => {
    const how = a.invocation ? ` (\`${a.invocation}\`)` : ''
    const link = a.href ? ` [Read more](${abs(a.href)})` : ''
    return `\n- **${a.name ?? ''}**${how}: ${collapse(content)}${link}\n`
  },
  Note: (a, content) => {
    const kind = cap(typeof a.kind === 'string' ? a.kind : 'note')
    const heading = a.title ? `${kind}: ${a.title}` : kind
    return `\n\n**${heading}**\n\n${dedent(content).trim()}\n\n`
  },
  // The children are an expression resolved by the scanner; see below.
  Verdict: (a, content) => `${a.note ? `\n\n*${a.note}*` : ''}${content}`,
  RunReport: (a, content) => `${a.note ? `\n\n*${a.note}*` : ''}${content}`,
  Action: (a, content) => `[${collapse(content)}](${abs(a.href ?? '')})`,
  Band: (a, content) =>
    `\n\n${a.label ? `*${a.label}*\n\n` : ''}${a.title ? `## ${a.title}\n\n` : ''}${dedent(content).trim()}\n\n`,
  Hero: (a, content) => {
    const actions = a.actions ? `\n\n${a.actions}` : ''
    return `\n\n${a.eyebrow ? `*${a.eyebrow}*\n\n` : ''}${a.title ? `**${a.title}**\n\n` : ''}${dedent(content).trim()}${actions}\n\n`
  },
}

/** Components whose children are a resolved expression rather than markdown. */
const RESOLVES_CHILDREN = new Set(['Verdict', 'RunReport'])

/**
 * MDX -> markdown a model can read.
 *
 *   - the frontmatter and the `import` block are dropped
 *   - each component is replaced by the text it renders
 *   - code, fenced or inline, is never touched
 *   - site-relative links are made absolute, so they still work out of context
 */
export function toPlainMarkdown(source) {
  const s = parseFrontmatter(source).body
  const root = { name: null, buf: [] }
  const stack = [root]
  const emit = (text) => stack[stack.length - 1].buf.push(text)

  let i = 0
  let fence = null // { char, len } while inside a fenced code block
  while (i < s.length) {
    const atLineStart = i === 0 || s[i - 1] === '\n'
    if (atLineStart) {
      const lineEnd = s.indexOf('\n', i) === -1 ? s.length : s.indexOf('\n', i) + 1
      const line = s.slice(i, lineEnd)
      if (fence) {
        const close = new RegExp(`^\\s*\\${fence.char}{${fence.len},}\\s*$`)
        if (close.test(line.replace(/\n$/, ''))) fence = null
        emit(line)
        i = lineEnd
        continue
      }
      const open = /^\s*(`{3,}|~{3,})/.exec(line)
      if (open) {
        fence = { char: open[1][0], len: open[1].length }
        emit(line)
        i = lineEnd
        continue
      }
      if (/^import\s.+\sfrom\s+['"][^'"]+['"];?\s*$/.test(line)) {
        i = lineEnd
        continue
      }
    }

    const c = s[i]

    if (c === '`') {
      // Inline code: copy through the matching run of backticks, untouched.
      let n = 0
      while (s[i + n] === '`') n++
      const close = s.indexOf('`'.repeat(n), i + n)
      const end = close === -1 ? i + n : close + n
      emit(s.slice(i, end))
      i = end
      continue
    }

    if (c === '<') {
      const m = /^<(\/?)([A-Z][A-Za-z]*)/.exec(s.slice(i, i + 40))
      if (m && COMPONENTS.has(m[2])) {
        const name = m[2]
        if (m[1]) {
          const end = s.indexOf('>', i) + 1
          const frame = stack.pop()
          if (frame.name !== name) throw new Error(`</${name}> closes <${frame.name}>`)
          emit(RENDER[name](frame.attrs, frame.buf.join('')))
          i = end
        } else {
          const { attrs, end, selfClosing } = parseAttrs(s, i + 1 + name.length)
          if (selfClosing) emit(RENDER[name](attrs, ''))
          else stack.push({ name, attrs, buf: [] })
          i = end
        }
        continue
      }
    }

    if (c === '{') {
      // A JSX expression. Inside <Verdict> and <RunReport> it is the content
      // being shown; resolve it. A bare `{/* comment */}` is dropped.
      const end = skipBraces(s, i)
      const inner = s.slice(i + 1, end - 1).trim()
      const top = stack[stack.length - 1].name
      if (/^\/\*[\s\S]*\*\/$/.test(inner)) {
        i = end
        continue
      }
      if (RESOLVES_CHILDREN.has(top)) {
        const data = loadGenerated()
        const value = new Function(...GENERATED, `return (${inner})`)(
          ...GENERATED.map((name) => data[name]),
        )
        // A transcript is text and belongs in a fence; a qualification
        // result is an object and belongs as prose.
        emit(typeof value === 'string' ? fenced(value) : verdictToText(value))
        i = end
        continue
      }
    }

    emit(c)
    i += 1
  }
  if (stack.length !== 1) throw new Error(`<${stack[stack.length - 1].name}> is never closed`)

  const out = root.buf.join('')
  return absoluteLinks(out)
    .replace(/[ \t]+$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** `](/path/)` -> `](https://.../path/)`, outside fenced code. */
function absoluteLinks(markdown) {
  let inFence = false
  return markdown
    .split('\n')
    .map((line) => {
      if (/^\s*(```|~~~)/.test(line)) inFence = !inFence
      return inFence ? line : line.replace(/\]\((\/(?!\/)[^)\s]*)\)/g, (_, p) => `](${ORIGIN}${p})`)
    })
    .join('\n')
}

// ---------------------------------------------------------------------------
// Writing

const urlOf = (route) => `${ORIGIN}${route}`
const mdUrlOf = (route) => `${urlOf(route)}index.md`
const mdPathOf = (route) => join(route.replace(/^\/|\/$/g, ''), 'index.md')

/** The version in pyproject.toml's [project]: the one place it is set. */
export function packageVersion() {
  const toml = readFileSync(join(ROOT, '..', 'pyproject.toml'), 'utf8')
  const project = /\[project\]([\s\S]*?)(?:\n\[|$)/.exec(toml)?.[1] ?? ''
  const version = /^version\s*=\s*"([^"]+)"/m.exec(project)?.[1]
  if (!version) throw new Error('no version in [project] of pyproject.toml')
  return version
}

/**
 * A page as markdown: its heading, where it lives, then the prose. The
 * page's own H1 is kept when it opens with one; otherwise the frontmatter
 * title stands in, so every page starts the same way.
 */
export function pageMarkdown(page) {
  const prose = toPlainMarkdown(page.body)
  const own = /^# (.+)\n*/.exec(prose)
  const title = own ? own[1] : page.title
  const rest = own ? prose.slice(own[0].length) : prose
  return `# ${title}\n\nSource: ${urlOf(page.route)}\n\n${rest}\n`
}

export function indexText(pages, rootPage) {
  const lines = [
    '# karani',
    '',
    `> ${rootPage.description} Version ${packageVersion()}, MIT licensed, self-hosted.`,
    '',
    'Each link below is the plain-markdown form of a page; the same page is served as HTML at the same address without `index.md`. The whole site in one file is [llms-full.txt](' +
      `${ORIGIN}/llms-full.txt` +
      ').',
    '',
    'Every job posting, company and person in the examples is fabricated. karani never submits an application.',
  ]
  let section = null
  for (const page of pages) {
    if (page.section !== section) {
      section = page.section
      lines.push('', `## ${section}`, '')
    }
    lines.push(`- [${page.title}](${mdUrlOf(page.route)}): ${collapse(page.description)}`)
  }
  return `${lines.join('\n')}\n`
}

export function fullText(pages) {
  const header = `# karani: the complete documentation\n\nEvery page of ${ORIGIN}/ as plain markdown, in navigation order. Each page starts with its title and its address.\n`
  return `${[header, ...pages.map(pageMarkdown)].join('\n---\n\n')}`
}

async function main() {
  const out = resolve(process.argv[2] ?? join(ROOT, 'out'))
  if (!existsSync(join(out, 'index.html'))) {
    console.error(`llms: ${out}/index.html does not exist; run "next build" first`)
    process.exit(1)
  }
  const pages = collectPages()
  const rootPage = pages.find((p) => p.route === '/')
  if (!rootPage) throw new Error('content/index.mdx not found')

  // A markdown variant is only useful beside a page that exists.
  const missing = pages.filter((p) => !existsSync(join(out, p.route.replace(/^\/|\/$/g, ''), 'index.html')))
  if (missing.length) {
    console.error(`llms: no exported page for ${missing.map((p) => p.route).join(', ')}`)
    process.exit(1)
  }

  await writeFile(join(out, 'llms.txt'), indexText(pages, rootPage), 'utf8')
  await writeFile(join(out, 'llms-full.txt'), fullText(pages), 'utf8')
  for (const page of pages) {
    const file = join(out, mdPathOf(page.route))
    await mkdir(dirname(file), { recursive: true })
    await writeFile(file, pageMarkdown(page), 'utf8')
  }
  console.log(`llms: llms.txt, llms-full.txt and ${pages.length} markdown pages`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main()
}
