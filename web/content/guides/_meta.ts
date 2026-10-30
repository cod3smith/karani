import type { MetaRecord } from 'nextra'

/*
 * Ordered along the pipeline, then by surface: find a role, judge it, write
 * the application, send it, hear back, measure. The surfaces that can drive
 * all of that come after, and memory sits with them because it is consumed
 * by every stage rather than being one.
 */
const meta: MetaRecord = {
  hunting: 'Hunting and filtering',
  qualification: 'Qualification',
  providers: 'LLM providers',
  drafting: 'Drafting a pack',
  submission: 'The submission console',
  watcher: 'The inbox watcher',
  analytics: 'Conversion analytics',
  mcp: 'Connecting an MCP client',
  slack: 'Slack',
  notion: 'The Notion board',
  memory: 'Memory',
}

export default meta
