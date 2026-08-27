"""Tool registry for the agent loop.

Each tool = (name, description, JSON-schema parameters, async callable).
The model sees the schema in its `tools` payload; when it emits a
`tool_calls` block, the loop looks the name up here and awaits the fn.

Design principles:
- Every tool is cheap (public API or free scrape) and returns text.
- Every tool has a hard timeout and byte cap so a slow/huge response
  can't blow the agent budget.
- Every tool is *tolerant* of failure — returns "no results" instead
  of raising, so the model can decide to try something else.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


# --- Tool infrastructure ---

ToolFn = Callable[..., Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema
    fn: ToolFn

    def openai_schema(self) -> dict[str, Any]:
        """Payload shape expected by OpenRouter / OpenAI-compatible APIs."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._by_name = {t.name: t for t in tools}

    def schemas(self) -> list[dict[str, Any]]:
        return [t.openai_schema() for t in self._by_name.values()]

    async def execute(self, name: str, arguments_json: str, timeout: float = 10.0) -> str:
        tool = self._by_name.get(name)
        if not tool:
            return f"ERROR: unknown tool {name!r}"
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as e:
            return f"ERROR: invalid JSON arguments — {e}"
        try:
            return await asyncio.wait_for(tool.fn(**args), timeout=timeout)
        except asyncio.TimeoutError:
            return f"ERROR: tool {name!r} timed out after {timeout}s"
        except Exception as e:
            log.warning("tool %s failed: %s", name, e)
            return f"ERROR: tool {name!r} raised {type(e).__name__}: {e}"


# --- Concrete tools ---

_MAX_TEXT_BYTES = 8_000  # ~2k tokens after truncation


def _truncate(text: str, limit: int = _MAX_TEXT_BYTES) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated at {limit} bytes]"


async def _get(url: str, headers: dict | None = None, timeout: float = 8.0) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "karani-agent/0.1", **(headers or {})},
    ) as client:
        return await client.get(url)


# 1. Web search — DuckDuckGo HTML, no key needed
_DDG_URL = "https://html.duckduckgo.com/html/?q={q}"


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo. Returns markdown of title + URL + snippet."""
    try:
        r = await _get(_DDG_URL.format(q=quote_plus(query)))
        if r.status_code != 200:
            return f"no results (status {r.status_code})"
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for i, item in enumerate(soup.select(".result")[:max_results], 1):
            title_el = item.select_one(".result__title a")
            snippet_el = item.select_one(".result__snippet")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
            results.append(f"{i}. **{title}**\n   {url}\n   {snippet}")
        return _truncate("\n".join(results) or "no results")
    except Exception as e:
        return f"search failed: {e}"


# 2. Fetch a URL — grabs a page, strips HTML, caps bytes
async def fetch_url(url: str) -> str:
    """Fetch an HTTPS URL and return the stripped text (capped ~8KB)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return "ERROR: only HTTPS URLs are allowed"
    try:
        r = await _get(url)
        if r.status_code != 200:
            return f"status {r.status_code}"
        text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        return _truncate(text)
    except Exception as e:
        return f"fetch failed: {e}"


# 3. GitHub org lookup — public API, no auth needed
_GH_ORG = "https://api.github.com/orgs/{org}"
_GH_REPOS = "https://api.github.com/orgs/{org}/repos?sort=pushed&per_page=10"


async def github_org(org: str) -> str:
    """Summarize a company's public GitHub presence: bio + top repos by pushed date."""
    try:
        info_r = await _get(_GH_ORG.format(org=org),
                            headers={"Accept": "application/vnd.github+json"})
        if info_r.status_code == 404:
            return f"no public github org named {org!r}"
        if info_r.status_code != 200:
            return f"github status {info_r.status_code}"
        info = info_r.json()
        repos_r = await _get(_GH_REPOS.format(org=org),
                             headers={"Accept": "application/vnd.github+json"})
        repos = repos_r.json() if repos_r.status_code == 200 else []
    except Exception as e:
        return f"github lookup failed: {e}"

    lines = [
        f"**{info.get('name') or org}** — {info.get('description') or 'no description'}",
        f"public repos: {info.get('public_repos', 0)} · followers: {info.get('followers', 0)}",
    ]
    if info.get("blog"):
        lines.append(f"blog: {info['blog']}")
    if repos:
        lines.append("\nRecent repos (by push date):")
        for r in repos[:10]:
            lines.append(
                f"- **{r.get('name')}** ({r.get('language') or 'n/a'}, "
                f"★{r.get('stargazers_count', 0)}) — "
                f"{r.get('description') or 'no description'}"
            )
    return _truncate("\n".join(lines))


# 4. Wikipedia summary — free, structured, great for company background
_WP_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"


async def wikipedia_summary(topic: str) -> str:
    """Return the Wikipedia summary paragraph for a company or topic."""
    try:
        r = await _get(_WP_SUMMARY.format(title=quote_plus(topic)))
        if r.status_code == 404:
            return f"no wikipedia article for {topic!r}"
        if r.status_code != 200:
            return f"wikipedia status {r.status_code}"
        d = r.json()
        parts = []
        if d.get("description"):
            parts.append(f"_{d['description']}_")
        if d.get("extract"):
            parts.append(d["extract"])
        return _truncate("\n\n".join(parts) or "no summary")
    except Exception as e:
        return f"wikipedia lookup failed: {e}"


# --- Default registry ---

DEFAULT_TOOLS: list[Tool] = [
    Tool(
        name="web_search",
        description=(
            "Search the web for information about a company, role, comp bands, "
            "remote culture, or engineering reputation. Use this first before "
            "fetching specific pages."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Search query, e.g. 'GitLab remote pay bands'"},
                "max_results": {"type": "integer", "default": 5,
                                "description": "Cap results (1-10)"},
            },
            "required": ["query"],
        },
        fn=web_search,
    ),
    Tool(
        name="fetch_url",
        description=(
            "Fetch an HTTPS page and return its stripped text (~8KB cap). "
            "Use this to read a specific engineering blog post or careers page "
            "discovered via web_search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full HTTPS URL"},
            },
            "required": ["url"],
        },
        fn=fetch_url,
    ),
    Tool(
        name="github_org",
        description=(
            "Summarize a company's public GitHub presence: bio, blog URL, "
            "and 10 most-recently pushed repos with primary language. "
            "Use this to gauge engineering culture and active tech stack."
        ),
        parameters={
            "type": "object",
            "properties": {
                "org": {"type": "string",
                        "description": "GitHub org slug, e.g. 'gitlab', 'huggingface'"},
            },
            "required": ["org"],
        },
        fn=github_org,
    ),
    Tool(
        name="wikipedia_summary",
        description=(
            "Get the Wikipedia summary for a company or topic. Good for "
            "founding-year, size, headquarters, and general background."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string",
                          "description": "Wikipedia article title, e.g. 'GitLab', 'Hugging Face'"},
            },
            "required": ["topic"],
        },
        fn=wikipedia_summary,
    ),
]


def default_registry() -> ToolRegistry:
    return ToolRegistry(DEFAULT_TOOLS)
