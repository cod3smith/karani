/**
 * Fail when anything real reaches the built site.
 *
 * The repository is public and so are these docs, and karani's whole
 * subject matter is one person's resume and the jobs they are applying to.
 * So the examples are fabricated: an invented candidate, invented companies,
 * invented postings whose URLs resolve to nothing
 * (web/scripts/fixtures.py). This is the gate that keeps that true, because
 * "remember not to paste a real posting into an example" is not a control.
 *
 * Four checks, in order of what they would cost if they failed:
 *
 *   1. **Nothing from data/resume.md appears in out/.** Email addresses,
 *      phone numbers, URLs and the candidate's own name, pulled out of the
 *      real resume if one is present and searched for in every built page.
 *      This is the one that matters: a resume is the most personal file in
 *      the repository and it is gitignored precisely so it never leaves the
 *      machine.
 *   2. **Every ATS application URL names a fabricated slug.** A real
 *      posting pasted into an example is the likeliest leak, and it has a
 *      recognisable shape: `boards.greenhouse.io/<slug>`,
 *      `jobs.lever.co/<slug>`, and the Ashby and Workable equivalents. The
 *      illustrative placeholder (`acme`, in "the `acme` in
 *      boards.greenhouse.io/acme") is declared alongside the companies, so
 *      it is allowed by name rather than by a pattern that would also let a
 *      real slug through.
 *   3. **The fabricated candidate and companies do appear.** A gate that
 *      checks an allowlist has to know the allowlist is being used, or a
 *      refactor that dropped every example would pass it silently.
 *   4. **Nothing in out/ looks like a credential.** An API key pasted into
 *      a prose example would be published, and the shapes are
 *      recognisable.
 *
 * The resume check is skipped, loudly, when `data/resume.md` is absent --
 * which is the normal state of a fresh clone, since the file is gitignored.
 * A skip is printed rather than silent, because a check that quietly checks
 * nothing is worse than no check.
 *
 * Usage:
 *   node scripts/check-synthetic.mjs
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ORIGIN, REPOSITORY } from '../site.config.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const repo = join(root, '..')
const OUT = join(root, 'out')

const failures = []
const notes = []
function assert(cond, message) {
  if (!cond) failures.push(message)
}

if (!existsSync(OUT)) {
  console.error('FAIL: out/ does not exist; run npm run build first')
  process.exit(1)
}

const allowlistPath = join(root, 'lib/fabricated.generated.json')
if (!existsSync(allowlistPath)) {
  console.error(
    'FAIL: lib/fabricated.generated.json is missing. It is written by ' +
      'scripts/capture_samples.py from scripts/fixtures.py, and without it ' +
      'this check has no allowlist to work from.',
  )
  process.exit(1)
}
const fabricated = JSON.parse(readFileSync(allowlistPath, 'utf8'))

/** Every built page and text file, as [relative path, contents]. */
function builtFiles() {
  const found = []
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) {
        // _next is compiled JavaScript and _pagefind is a search index; both
        // are derived from the pages, which are checked.
        if (entry.name !== '_next' && entry.name !== '_pagefind') walk(path)
      } else if (/\.(html|txt|md|json|xml)$/.test(entry.name)) {
        found.push([path.slice(OUT.length + 1), readFileSync(path, 'utf8')])
      }
    }
  }
  walk(OUT)
  return found
}
const files = builtFiles()
assert(files.length > 0, 'no built files found to check')

// --- 1. nothing from the real resume ---------------------------------------

const resumePath = join(repo, 'data/resume.md')
if (!existsSync(resumePath)) {
  notes.push(
    'data/resume.md is absent, so the resume-leak check had nothing to ' +
      'compare against. That is the normal state of a fresh clone (the file ' +
      'is gitignored); run this on a machine that has one to exercise it.',
  )
} else {
  const resume = readFileSync(resumePath, 'utf8')

  /**
   * The identifying strings in a resume, by shape rather than by guessing at
   * names. Each is specific enough that a match in the built site is a leak
   * and not a coincidence.
   */
  const identifiers = new Set()

  for (const m of resume.matchAll(/[\w.+-]+@[\w-]+\.[\w.]{2,}/g)) identifiers.add(m[0])
  for (const m of resume.matchAll(/https?:\/\/[^\s)<>\]]+/g)) identifiers.add(m[0])
  // A phone number: seven or more digits, allowing the usual separators.
  for (const m of resume.matchAll(/\+?\d[\d\s().-]{6,}\d/g)) {
    const digits = m[0].replace(/\D/g, '')
    if (digits.length >= 7) identifiers.add(m[0].trim())
  }
  // The name, from the document's own title line.
  const h1 = /^#\s+(.+)$/m.exec(resume)?.[1]?.trim()
  if (h1 && h1.length > 2) identifiers.add(h1)

  // Deliberately NOT extracted: company and job-title phrases from the
  // resume's own headings. A resume says "Senior Software Engineer" and so
  // does this site, legitimately, on most pages -- an earlier version of
  // this check extracted those phrases and failed the build on the
  // qualification guide. A company name in a resume is also
  // indistinguishable from a company name in prose. So this check covers
  // the identifiers that are unambiguous, and the ATS-URL check below
  // covers the realistic leak, which is a real posting pasted into an
  // example.

  // The project's own public identifiers are not a leak. The repository URL
  // is in the README, in pyproject.toml and on every page of this site by
  // design, and it shares a prefix with the author's GitHub profile, which
  // the resume lists.
  for (const value of [...identifiers]) {
    if (REPOSITORY.startsWith(value) || value.startsWith(REPOSITORY) || ORIGIN.startsWith(value)) {
      identifiers.delete(value)
    }
  }

  // Anything already declared fabricated is, by definition, not a leak.
  for (const value of [fabricated.candidate.name, fabricated.candidate.email]) {
    identifiers.delete(value)
  }
  for (const company of fabricated.companies) identifiers.delete(company.name)

  assert(
    identifiers.size > 0,
    'data/resume.md exists but no identifying strings were extracted from ' +
      'it, so this check is looking at nothing. Teach the extractor the ' +
      "resume's shape rather than letting it pass vacuously.",
  )

  let searched = 0
  for (const identifier of identifiers) {
    searched += 1
    for (const [path, text] of files) {
      if (text.includes(identifier)) {
        // Deliberately not printed: the point is to keep it out of a log as
        // well as out of the site.
        failures.push(
          `${path} contains a string from data/resume.md (${identifier.length} characters, ` +
            `starting "${identifier.slice(0, 3)}..."). Examples must be fabricated; see web/scripts/fixtures.py.`,
        )
        break
      }
    }
  }
  notes.push(`checked ${searched} identifying strings from data/resume.md against ${files.length} built files`)
}

// --- 2. every application URL names a fabricated slug ----------------------

const ATS_URL = [
  /boards\.greenhouse\.io\/([\w-]+)/g,
  /job-boards\.greenhouse\.io\/([\w-]+)/g,
  /jobs\.lever\.co\/([\w-]+)/g,
  /jobs\.ashbyhq\.com\/([\w-]+)/g,
  /apply\.workable\.com\/([\w-]+)/g,
  /api\.ashbyhq\.com\/posting-api\/job-board\/([\w-]+)/g,
]
const slugs = new Set([
  ...fabricated.companies.map((c) => c.slug),
  ...(fabricated.placeholder_slugs ?? []),
])
let urlsChecked = 0
for (const [path, text] of files) {
  for (const pattern of ATS_URL) {
    for (const m of text.matchAll(pattern)) {
      urlsChecked += 1
      assert(
        slugs.has(m[1]),
        `${path} has an application URL for "${m[1]}", which is not a fabricated ` +
          `company. A real posting must never appear in the docs; add the ` +
          `example to web/scripts/fixtures.py or remove it.`,
      )
    }
  }
}

// --- 3. the allowlist is actually in use -----------------------------------

const everything = files.map(([, text]) => text).join('\n')
assert(
  everything.includes(fabricated.candidate.name),
  `the fabricated candidate (${fabricated.candidate.name}) appears nowhere in the built site, ` +
    `so checks 1 and 2 are comparing against an allowlist nothing uses`,
)
for (const company of fabricated.companies) {
  assert(
    everything.includes(company.name),
    `the fabricated company "${company.name}" appears nowhere in the built site. ` +
      `Either use it in an example or remove it from web/scripts/fixtures.py, ` +
      `so the allowlist stays a description of what is actually there.`,
  )
}

// --- 4. nothing that looks like a credential -------------------------------
//
// Prose about keys is fine and this site has plenty of it; a key is not.
// These patterns are each a real provider's own prefix, so a match is the
// thing itself rather than a mention of it.

const SECRET_SHAPES = [
  { name: 'an OpenAI-style key', re: /\bsk-[A-Za-z0-9]{20,}/g },
  { name: 'an Anthropic key', re: /\bsk-ant-[A-Za-z0-9_-]{20,}/g },
  { name: 'an OpenRouter key', re: /\bsk-or-v1-[A-Za-z0-9]{20,}/g },
  { name: 'a Slack bot token', re: /\bxox[baprs]-[A-Za-z0-9-]{20,}/g },
  { name: 'a Slack app token', re: /\bxapp-\d-[A-Za-z0-9-]{20,}/g },
  { name: 'a Notion token', re: /\b(?:secret_|ntn_)[A-Za-z0-9]{30,}/g },
  { name: 'a Google OAuth secret', re: /\bGOCSPX-[A-Za-z0-9_-]{20,}/g },
  { name: 'a Postgres DSN with a password', re: /postgres(?:ql)?:\/\/[^\s:@/]+:[^\s@/]+@/g },
]
for (const [path, text] of files) {
  for (const { name, re } of SECRET_SHAPES) {
    for (const m of text.matchAll(re)) {
      // The compose stack's own throwaway credentials are documented on
      // purpose: `karani:karani` on localhost is in docker-compose.yml and
      // is not a secret. The pattern stops at the `@`, so the host has to
      // be read from the text after the match rather than from m[0].
      const after = text.slice(m.index + m[0].length, m.index + m[0].length + 9)
      if (/^karani:karani@$/.test(m[0].replace(/^postgres(ql)?:\/\//, '')) && after.startsWith('localhost')) {
        continue
      }
      failures.push(`${path} contains ${name} (${m[0].length} characters). Credentials belong in .env.`)
    }
  }
}

// --- report ----------------------------------------------------------------

for (const note of notes) console.log(`note: ${note}`)

if (failures.length) {
  for (const f of failures) console.error(`FAIL: ${f}`)
  process.exit(1)
}
console.log(
  `check-synthetic: ok (${files.length} built files, ${urlsChecked} application URLs, ` +
    `${fabricated.companies.length} fabricated companies all in use)`,
)
