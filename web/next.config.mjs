import nextra from 'nextra'

const withNextra = nextra({
  // Every code block gets a copy button. Most of this site is commands
  // somebody is about to run.
  defaultShowCopyCode: true,
  // Shiki, with one theme per colour scheme rather than a single theme forced
  // through a filter. The pair is the high-contrast GitHub one, chosen because
  // every token colour in it clears 4.5:1 against the surface a code block is
  // actually drawn on (white in light mode, #14171b in dark; the stylesheet
  // overrides the theme's own background). `npm run shiki` re-measures that
  // against the built HTML, so this is a gate rather than an assumption.
  // Bundled Shiki themes only.
  mdxOptions: {
    rehypePrettyCodeOptions: {
      theme: {
        light: 'github-light-high-contrast',
        dark: 'github-dark-high-contrast',
      },
    },
  },
})

export default withNextra({
  // GitHub Pages serves a directory of files, not a Node server.
  output: 'export',
  // Pages has no image optimiser.
  images: { unoptimized: true },
  // Without this, `/guides/qualification` is emitted as
  // `guides/qualification.html` and relative links between pages resolve one
  // level too high.
  trailingSlash: true,
  // Deliberately empty, and deliberately not configurable. A basePath
  // prefixes assets and next/link navigation but leaves a prose link and a
  // declared icon alone, so on a subpath every markdown link and both
  // favicons 404 while the page itself renders. The content is written with
  // root-absolute links on purpose (they survive being read as markdown out
  // of context), so this site is served from a zone root. site.config.mjs
  // has the measurement.
  basePath: '',
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
})
