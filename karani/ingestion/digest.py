"""Digest renderers — text, markdown, or standalone HTML.

The HTML digest is a single self-contained file (inline CSS, no external deps).
Open it in a browser or attach it to an email. It renders the top qualified/
maybe rows sorted by fit_score, with why-apply, strengths, gaps, evidence,
and one-shot copy-paste commands to record your reaction.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone


def _qual(row: dict) -> dict:
    q = row.get("qualification") or {}
    if isinstance(q, str):
        try:
            q = json.loads(q)
        except Exception:
            q = {}
    return q


def _fmt_comp(row: dict) -> str:
    lo = row.get("comp_min_usd")
    hi = row.get("comp_max_usd")
    if not lo:
        return "comp: n/a"
    if hi:
        return f"${lo:,} – ${hi:,}"
    return f"${lo:,}+"


def _fmt_posted(row: dict) -> str:
    ts = row.get("posted_at")
    if not ts:
        return ""
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return ""
    return ts.strftime("%b %d")


def render_text(rows: list[dict]) -> str:
    """Compact terminal format (what the existing `digest` CLI uses)."""
    if not rows:
        return "no qualified rows — run `qualify` first"
    lines: list[str] = []
    for i, r in enumerate(rows, 1):
        q = _qual(r)
        fit = r.get("fit_score") or q.get("fit_score") or "?"
        verdict = (r.get("verdict") or q.get("verdict") or "?").upper()
        why = q.get("why_apply") or q.get("why_skip") or ""
        lines.append(
            f"\n[{i:>2}] {verdict:<9} fit={fit:<3}  "
            f"{r.get('company_display', '')} — {r.get('title', '')}  "
            f"({_fmt_comp(r)})"
        )
        lines.append(f"     {r.get('apply_url', '')}")
        if why:
            lines.append(f"     {why}")
    return "\n".join(lines)


def render_markdown(rows: list[dict]) -> str:
    """Markdown for pasting into a doc or emailing."""
    if not rows:
        return "_no qualified rows_"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = [f"# Job digest — {now}\n", f"_{len(rows)} roles ranked by fit_\n"]
    for i, r in enumerate(rows, 1):
        q = _qual(r)
        fit = r.get("fit_score") or q.get("fit_score") or "?"
        verdict = r.get("verdict") or q.get("verdict") or "?"
        out.append(
            f"\n## {i}. {r.get('company_display', '')} — {r.get('title', '')}"
        )
        out.append(
            f"**{verdict.upper()}** · fit {fit}/100 · {_fmt_comp(r)} · "
            f"{r.get('location_raw', '')} · [apply]({r.get('apply_url', '')})"
        )
        if q.get("why_apply"):
            out.append(f"\n**Why apply:** {q['why_apply']}")
        if q.get("recommended_positioning"):
            out.append(f"\n**Positioning:** {q['recommended_positioning']}")
        if q.get("strengths"):
            out.append("\n**Strengths:**")
            for s in q["strengths"][:5]:
                claim = s.get("claim", "")
                ev = s.get("evidence_from_resume", "")
                out.append(f"- {claim} — _{ev}_")
        if q.get("gaps"):
            out.append("\n**Gaps:**")
            for g in q["gaps"][:5]:
                req = g.get("requirement", "")
                mit = g.get("mitigation", "")
                sev = g.get("severity", "")
                out.append(f"- ({sev}) {req} — {mit or '_no mitigation_'}")
        if q.get("red_flags"):
            out.append("\n**Red flags:**")
            for rf in q["red_flags"]:
                out.append(f"- {rf}")
        out.append(
            f"\n_record verdict_: "
            f"`python -m ingestion.cli verdict {r.get('id')} apply` "
            f"(or shortlist / later / skip / applied)"
        )
    return "\n".join(out)


_HTML_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>karani · daily digest</title>
<style>
:root {
  --bg: #0b0d10; --card: #14171b; --border: #1e2329;
  --text: #e6e8ea; --muted: #8a9099; --accent: #7dd3fc;
  --qualified: #34d399; --maybe: #fbbf24; --skip: #f87171;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
       background: var(--bg); color: var(--text); line-height: 1.5; }
.wrap { max-width: 960px; margin: 0 auto; padding: 32px 24px 80px; }
header { border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }
h1 { margin: 0; font-size: 24px; font-weight: 600; }
header .sub { color: var(--muted); font-size: 14px; margin-top: 4px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
        padding: 20px; margin-bottom: 16px; }
.title-row { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.title-row .rank { color: var(--muted); font-size: 14px; min-width: 24px; }
.title-row h2 { margin: 0; font-size: 18px; font-weight: 600; }
.title-row .company { color: var(--muted); }
.meta { display: flex; gap: 12px; flex-wrap: wrap; color: var(--muted);
        font-size: 13px; margin-top: 6px; }
.meta a { color: var(--accent); text-decoration: none; }
.meta a:hover { text-decoration: underline; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
         font-size: 11px; font-weight: 600; text-transform: uppercase;
         letter-spacing: 0.03em; }
.badge.qualified { background: rgba(52,211,153,0.15); color: var(--qualified); }
.badge.maybe     { background: rgba(251,191,36,0.15); color: var(--maybe); }
.badge.skip      { background: rgba(248,113,113,0.15); color: var(--skip); }
.fit { font-variant-numeric: tabular-nums; font-weight: 600; }
.why { margin-top: 12px; font-size: 15px; }
.section { margin-top: 12px; }
.section h3 { margin: 0 0 6px; font-size: 12px; font-weight: 600;
              text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
.section ul { margin: 0; padding-left: 18px; }
.section li { margin: 4px 0; font-size: 14px; }
.section li .evidence { color: var(--muted); font-style: italic; }
.section li .sev { font-size: 11px; color: var(--muted); text-transform: uppercase; }
.cmd { margin-top: 14px; padding: 10px 12px; background: rgba(125,211,252,0.08);
       border-left: 3px solid var(--accent); font-family: ui-monospace, Menlo, monospace;
       font-size: 12px; color: var(--text); border-radius: 4px; overflow-x: auto; }
.empty { text-align: center; color: var(--muted); padding: 40px 0; }
</style></head><body><div class="wrap">"""

_HTML_TAIL = "</div></body></html>"


def render_html(rows: list[dict]) -> str:
    """Standalone HTML digest — inline CSS, no external assets."""
    now = datetime.now(timezone.utc).strftime("%A, %B %d, %Y · %H:%M UTC")
    body = [_HTML_HEAD]
    body.append(
        f'<header><h1>karani · daily digest</h1>'
        f'<div class="sub">{len(rows)} role{"s" if len(rows) != 1 else ""} '
        f'ranked by fit · {html.escape(now)}</div></header>'
    )
    if not rows:
        body.append('<div class="empty">No qualified rows yet — '
                    'run <code>python -m ingestion.cli qualify</code> first.</div>')
        body.append(_HTML_TAIL)
        return "\n".join(body)

    for i, r in enumerate(rows, 1):
        q = _qual(r)
        fit = r.get("fit_score") or q.get("fit_score") or 0
        verdict = (r.get("verdict") or q.get("verdict") or "maybe").lower()
        cls = verdict if verdict in ("qualified", "maybe", "skip") else "maybe"
        posted = _fmt_posted(r)

        card = ['<div class="card">']
        card.append('<div class="title-row">')
        card.append(f'<span class="rank">#{i}</span>')
        card.append(
            f'<h2>{html.escape(r.get("title") or "")}</h2>'
            f'<span class="company">— {html.escape(r.get("company_display") or "")}</span>'
        )
        card.append(f'<span class="badge {cls}">{verdict}</span>')
        card.append(f'<span class="fit">{fit}/100</span>')
        card.append("</div>")

        card.append('<div class="meta">')
        card.append(f'<span>{html.escape(_fmt_comp(r))}</span>')
        if r.get("location_raw"):
            card.append(f'<span>{html.escape(r["location_raw"])}</span>')
        if r.get("role_category"):
            card.append(f'<span>{html.escape(r["role_category"])}</span>')
        if posted:
            card.append(f'<span>posted {html.escape(posted)}</span>')
        if r.get("apply_url"):
            card.append(f'<a href="{html.escape(r["apply_url"])}" '
                        f'target="_blank" rel="noreferrer">apply →</a>')
        card.append("</div>")

        if q.get("why_apply"):
            card.append(f'<div class="why">{html.escape(q["why_apply"])}</div>')
        if q.get("recommended_positioning"):
            card.append(
                f'<div class="section"><h3>Positioning</h3>'
                f'<div>{html.escape(q["recommended_positioning"])}</div></div>'
            )
        if q.get("strengths"):
            card.append('<div class="section"><h3>Strengths</h3><ul>')
            for s in q["strengths"][:5]:
                card.append(
                    f'<li>{html.escape(s.get("claim", ""))} '
                    f'<span class="evidence">— '
                    f'{html.escape(s.get("evidence_from_resume", ""))}</span></li>'
                )
            card.append("</ul></div>")
        if q.get("gaps"):
            card.append('<div class="section"><h3>Gaps</h3><ul>')
            for g in q["gaps"][:5]:
                sev = g.get("severity", "")
                card.append(
                    f'<li><span class="sev">[{html.escape(sev)}]</span> '
                    f'{html.escape(g.get("requirement", ""))} '
                    f'<span class="evidence">— '
                    f'{html.escape(g.get("mitigation", "") or "no mitigation")}'
                    f'</span></li>'
                )
            card.append("</ul></div>")
        if q.get("red_flags"):
            card.append('<div class="section"><h3>Red flags</h3><ul>')
            for rf in q["red_flags"]:
                card.append(f'<li>{html.escape(rf)}</li>')
            card.append("</ul></div>")
        if q.get("evidence_gathered"):
            card.append('<div class="section"><h3>Evidence gathered</h3><ul>')
            for e in q["evidence_gathered"][:5]:
                card.append(f'<li>{html.escape(e)}</li>')
            card.append("</ul></div>")

        card.append(
            f'<div class="cmd">python -m ingestion.cli verdict '
            f'{r.get("id")} apply  '
            f'<span style="color:var(--muted)">'
            f'# or: shortlist / later / skip / applied</span></div>'
        )
        card.append("</div>")
        body.append("\n".join(card))
    body.append(_HTML_TAIL)
    return "\n".join(body)


def render(rows: list[dict], fmt: str) -> str:
    fmt = (fmt or "text").lower()
    if fmt == "html":
        return render_html(rows)
    if fmt in {"md", "markdown"}:
        return render_markdown(rows)
    return render_text(rows)


__all__: list[str] = ["render", "render_html", "render_markdown", "render_text"]
