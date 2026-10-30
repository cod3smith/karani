import type { MetaRecord } from 'nextra'

// cli.mdx, mcp.mdx and config.mdx are generated on every docs build by
// web/scripts/generate_cli.py, generate_mcp.py and generate_config.py.
// environment.mdx is hand-written: there is no schema to read it from,
// because these are read with os.getenv at the point of use.
const meta: MetaRecord = {
  cli: 'Command line',
  mcp: 'MCP tools',
  config: 'karani.toml',
  environment: 'Environment variables',
}

export default meta
