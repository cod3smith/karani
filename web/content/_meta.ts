import type { MetaRecord } from 'nextra'

/*
 * The navigation, ordered by what a reader is doing rather than by how the
 * project is built.
 *
 *   Start here   install it, say what to hunt, run one pass
 *   Guides       one surface or one stage at a time
 *   Reference    things you look up: the CLI, the MCP tools, karani.toml,
 *                the environment. Three of the four are generated.
 *   Operations   running it unattended, and what to do when it stops
 *   Decisions    every ADR, browsable. Generated from docs/adrs/.
 *   Project      written about karani rather than for someone using it
 *
 * The last section is the point of the split. All of it is worth having and
 * none of it belongs in a user's path.
 */
const meta: MetaRecord = {
  index: {
    title: 'Home',
    theme: { sidebar: false, toc: false, pagination: false, breadcrumb: false, layout: 'full' },
  },
  start: { title: 'Start here' },
  guides: { title: 'Guides' },
  reference: { title: 'Reference' },
  operations: { title: 'Operations' },
  decisions: { title: 'Decisions' },
  project: { title: 'Project' },
  licence: { title: 'Licence' },
}

export default meta
