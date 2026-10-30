import type { Metadata } from 'next'
import { Newsreader, Source_Serif_4 } from 'next/font/google'
import { GeistMono } from 'geist/font/mono'
import { GeistSans } from 'geist/font/sans'
import { Footer, Layout, Navbar } from 'nextra-theme-docs'
import { Head } from 'nextra/components'
import { getPageMap } from 'nextra/page-map'
import 'nextra-theme-docs/style.css'
import './globals.css'
import { Mark } from '../components/Mark'
import { ORIGIN, REPOSITORY } from '../site.config.mjs'

/*
 * Three typefaces, one role each.
 *
 *   Geist Sans    headings and chrome
 *   Source Serif  body prose
 *   Geist Mono    code, and every word the pipeline says
 *
 * Plus one display face, Newsreader, for the wordmark and the largest
 * headings only. A newspaper face for a tool whose job is reading notices
 * and keeping the record.
 */
const sourceSerif = Source_Serif_4({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-source-serif',
  axes: ['opsz'],
})

/*
 * The display face. Its variable name is what --ka-font-display in
 * tokens.css reads.
 *
 * Newsreader is a 200-800 family, so 400 is a real weight here rather than
 * the lightest one available. Every rule that sets the display face must
 * still ask for a weight listed below, which scripts/check-furniture.mjs
 * enforces: a rule asking for a weight the layout never loaded falls back
 * silently and no build says so.
 */
const newsreader = Newsreader({
  subsets: ['latin'],
  weight: ['400'],
  style: ['normal', 'italic'],
  display: 'swap',
  variable: '--font-newsreader',
})

export const metadata: Metadata = {
  metadataBase: new URL(ORIGIN),
  title: {
    default: 'karani — a job hunt that drafts everything and submits nothing',
    template: '%s — karani',
  },
  description:
    'karani watches nine job boards, drops 95% of what it finds on deterministic rules, scores the rest against your resume, drafts the application, and hands you the pen. It never submits.',
  applicationName: 'karani',
  // Only the small variant is declared: icon.svg's thin slabs collapse at 16px.
  icons: {
    icon: [{ url: '/favicon.svg', type: 'image/svg+xml' }],
    shortcut: '/favicon.svg',
    // Next links a file-convention icon only when `icons` is unset, so the
    // Apple icon (app/apple-icon.png/route.tsx) has to be named here.
    apple: [{ url: '/apple-icon.png', sizes: '180x180', type: 'image/png' }],
  },
  authors: [{ name: 'Kelyn Njeri' }],
  // Relative, so each page resolves it against metadataBase and its own path
  // and declares itself, with the trailing slash the export serves.
  alternates: { canonical: './' },
  openGraph: {
    type: 'website',
    siteName: 'karani',
    url: `${ORIGIN}/`,
    // Rendered at build time by app/opengraph-image.png/route.tsx.
    images: [
      {
        url: '/opengraph-image.png',
        width: 1200,
        height: 630,
        alt: 'karani: a job hunt that drafts everything and submits nothing',
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    images: ['/opengraph-image.png'],
  },
}

/*
 * Structured data, and only what can be checked against the repository.
 * karani is a personal tool with one user, so there is deliberately no
 * rating, review, download or interaction count here, and none may be added
 * until it is true (scripts/check-seo.mjs fails on them).
 */
const jsonLd = {
  '@context': 'https://schema.org',
  '@type': 'SoftwareApplication',
  name: 'karani',
  description:
    'A self-hosted job-application pipeline. It ingests postings from nine boards, pre-filters them deterministically, qualifies the survivors against a resume with an LLM, drafts the application pack, and tracks the outcome. It never submits an application.',
  url: `${ORIGIN}/`,
  applicationCategory: 'DeveloperApplication',
  operatingSystem: 'Linux, macOS',
  programmingLanguage: ['Python'],
  sameAs: REPOSITORY,
  license: 'https://spdx.org/licenses/MIT.html',
  author: { '@type': 'Person', name: 'Kelyn Njeri' },
}

const navbar = (
  <Navbar logo={<Mark />} projectLink={REPOSITORY} />
)

const footer = (
  <Footer>
    <div className="ka-footer">
      <p>
        <strong>karani</strong> — Swahili for clerk. It reads every posting,
        files every pack, keeps every record, and hands you the pen.
      </p>
      <p>
        <a href="/licence/">MIT licensed</a>. Copyright (c) 2026 Kelyn Paul
        Njeri.
      </p>
      <p className="ka-mono">NAIROBI, EAT (UTC+3) &middot; MMXXVI</p>
    </div>
  </Footer>
)

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      dir="ltr"
      suppressHydrationWarning
      className={`${GeistSans.variable} ${GeistMono.variable} ${sourceSerif.variable} ${newsreader.variable}`}
    >
      {/*
        Nextra derives its own accent (links, active nav, focus rings) and page
        background from these, so they are set here rather than overridden in
        our stylesheet. One source of truth beats a layer of `!important`.
        The hue is the submission console's accent, 199; the lightness differs
        per theme because #7dd3fc is 1.7:1 on white.
      */}
      <Head
        color={{
          hue: { light: 199, dark: 199 },
          saturation: { light: 85, dark: 95 },
          lightness: { light: 28, dark: 74 },
        }}
        backgroundColor={{ light: '#f6f7f9', dark: '#0b0d10' }}
      >
        {/*
          One unconditional value, placed first. The site opens dark whatever
          the OS prefers (`defaultTheme: 'dark'` below), so a light variant
          keyed to prefers-color-scheme would give an OS-light visitor pale
          browser chrome around a near-black page. Nextra's <Head> still emits
          its own light/dark pair after this; a browser takes the first
          theme-color whose media matches, which is this one, and
          check-furniture --built asserts that it stays first.
        */}
        <meta name="theme-color" content="#0b0d10" />
      </Head>
      <body className="ka-grain">
        <script
          type="application/ld+json"
          // `<` is escaped so no string in the block can close the script tag.
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, '\\u003c') }}
        />
        <Layout
          navbar={navbar}
          footer={footer}
          pageMap={await getPageMap()}
          docsRepositoryBase={`${REPOSITORY}/tree/main/web/content`}
          editLink="Edit this page on GitHub"
          // One maintainer, no triage capacity: a feedback link that goes
          // nowhere is worse than none.
          feedback={{ content: null }}
          sidebar={{ defaultMenuCollapseLevel: 1, autoCollapse: false, toggleButton: true }}
          toc={{ title: 'On this page', backToTop: 'Back to top' }}
          darkMode
          // Dark is what a visitor sees on arrival, because that is what the
          // submission console is; light stays one toggle away.
          nextThemes={{ defaultTheme: 'dark' }}
        >
          {children}
        </Layout>
      </body>
    </html>
  )
}
