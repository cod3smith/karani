"""The submission console: one HTML file, no server, no submit button.

An hour-long session that clears the queue needs three things per job
on one screen: the link to the real form, the documents to attach, and
every field's answer one tap from the clipboard. That is all this is.

Deliberately a static file. A server would need a port, a process and
a reason to be running when the laptop sleeps; a file opens from
iCloud or AirDrop on a phone, which is where the queue actually gets
cleared. The cost is that "mark submitted" cannot write to Postgres
from the page, so the console remembers the ticks in localStorage and
hands back one `karani submitted <ids>` command to run afterwards.

Nothing in the page posts anywhere. karani prepares; a human clicks
(ADR 0012).
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Sequence

from .answers import AnswerSheet

_STYLE = """
:root {
  --bg:#0b0d10; --card:#14171b; --border:#1e2329; --text:#e6e8ea;
  --muted:#8a9099; --accent:#7dd3fc; --ok:#34d399; --warn:#fbbf24;
  --legal:#a78bfa; --done:#2a2f36;
}
* { box-sizing:border-box; -webkit-text-size-adjust:100%; }
body { margin:0; background:var(--bg); color:var(--text); line-height:1.5;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif; }
.wrap { max-width:860px; margin:0 auto; padding:20px 14px 140px; }
header { border-bottom:1px solid var(--border); padding-bottom:14px;
  margin-bottom:18px; }
h1 { margin:0; font-size:20px; font-weight:600; }
.sub { color:var(--muted); font-size:13px; margin-top:4px; }
.rule { margin-top:10px; font-size:13px; color:var(--muted);
  border-left:3px solid var(--accent); padding:6px 10px;
  background:rgba(125,211,252,0.07); border-radius:0 4px 4px 0; }
.card { background:var(--card); border:1px solid var(--border);
  border-radius:10px; padding:16px 14px; margin-bottom:16px; }
.card.done { opacity:0.45; }
.head { display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; }
.head h2 { margin:0; font-size:17px; font-weight:600; }
.head .co { color:var(--muted); font-size:15px; }
.badge { font-size:10px; font-weight:700; letter-spacing:0.05em;
  text-transform:uppercase; padding:2px 7px; border-radius:4px; }
.badge.review { background:rgba(251,191,36,0.16); color:var(--warn); }
.badge.fallback { background:rgba(138,144,153,0.16); color:var(--muted); }
.meta { display:flex; gap:10px; flex-wrap:wrap; align-items:center;
  margin-top:10px; font-size:13px; color:var(--muted); }
a { color:var(--accent); }
a.apply { display:inline-block; padding:8px 14px; border-radius:7px;
  background:rgba(125,211,252,0.14); border:1px solid rgba(125,211,252,0.35);
  text-decoration:none; font-weight:600; }
.docs { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
.docs a { font-size:13px; padding:6px 10px; border-radius:6px;
  border:1px solid var(--border); text-decoration:none;
  background:rgba(255,255,255,0.02); }
.note { margin-top:10px; font-size:13px; color:var(--muted);
  border-left:2px solid var(--legal); padding-left:10px; }
ol.fields { list-style:none; margin:14px 0 0; padding:0; }
.field { border-top:1px solid var(--border); padding:12px 0; }
.field.needs-input { background:rgba(251,191,36,0.05); }
.field.voluntary { opacity:0.6; }
.q { display:flex; gap:8px; align-items:baseline; flex-wrap:wrap;
  font-size:14px; font-weight:600; }
.src { font-size:10px; font-weight:700; text-transform:uppercase;
  letter-spacing:0.04em; color:var(--muted); border:1px solid var(--border);
  border-radius:4px; padding:1px 5px; }
.src.legal { color:var(--legal); border-color:rgba(167,139,250,0.4); }
.src.document { color:var(--ok); border-color:rgba(52,211,153,0.4); }
.req { color:var(--warn); font-size:11px; }
.row { display:flex; gap:8px; margin-top:6px; align-items:flex-start; }
textarea { flex:1; width:100%; background:#0e1114; color:var(--text);
  border:1px solid var(--border); border-radius:6px; padding:8px 10px;
  font:inherit; font-size:14px; resize:vertical; }
textarea.empty { color:var(--muted); }
button { background:rgba(125,211,252,0.12); color:var(--accent);
  border:1px solid rgba(125,211,252,0.3); border-radius:6px;
  padding:8px 12px; font:inherit; font-size:13px; font-weight:600;
  cursor:pointer; white-space:nowrap; min-height:38px; }
button:active { background:rgba(125,211,252,0.25); }
button.mark { color:var(--ok); border-color:rgba(52,211,153,0.35);
  background:rgba(52,211,153,0.1); }
.hint { font-size:12px; color:var(--muted); margin-top:4px; }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }
.hidden { position:absolute; left:-9999px; width:1px; height:1px; }
.bar { position:fixed; left:0; right:0; bottom:0; background:#0e1114;
  border-top:1px solid var(--border); padding:10px 14px;
  display:flex; gap:8px; align-items:center; flex-wrap:wrap;
  font-size:13px; color:var(--muted); }
.bar code { color:var(--text); font-family:ui-monospace,Menlo,monospace;
  font-size:13px; overflow-x:auto; }
.empty-state { text-align:center; color:var(--muted); padding:48px 0; }
@media (max-width:520px) {
  .wrap { padding:14px 10px 160px; }
  .row { flex-direction:column; }
  .row button { width:100%; }
  a.apply { display:block; text-align:center; }
}
"""

_SCRIPT = """
(function () {
  var KEY = 'karani.submitted';
  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; }
    catch (e) { return []; }
  }
  function save(ids) {
    try { localStorage.setItem(KEY, JSON.stringify(ids)); } catch (e) {}
  }
  function flash(button, text) {
    if (!button) { return; }
    var was = button.getAttribute('data-label') || button.textContent;
    button.setAttribute('data-label', was);
    button.textContent = text;
    setTimeout(function () { button.textContent = was; }, 1300);
  }
  function legacyCopy(el) {
    // Safari over file:// has no async clipboard; a readonly textarea
    // plus setSelectionRange is the one path that works on iOS.
    try {
      el.focus(); el.select(); el.setSelectionRange(0, 999999);
      return document.execCommand('copy');
    } catch (e) { return false; }
  }
  function copyFrom(el, button) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(el.value).then(function () {
        flash(button, 'copied');
      }, function () {
        flash(button, legacyCopy(el) ? 'copied' : 'select it and copy');
      });
      return;
    }
    flash(button, legacyCopy(el) ? 'copied' : 'select it and copy');
  }
  function refresh() {
    var ids = load();
    document.querySelectorAll('.card').forEach(function (card) {
      var id = card.getAttribute('data-job');
      var marked = ids.indexOf(id) !== -1;
      card.classList.toggle('done', marked);
      var button = card.querySelector('button.mark');
      if (button) {
        button.textContent = marked ? 'submitted - undo' : 'mark submitted';
      }
    });
    var bar = document.getElementById('bar-text');
    var cmd = document.getElementById('bar-cmd');
    if (!bar || !cmd) { return; }
    if (ids.length) {
      cmd.value = 'karani submitted ' + ids.join(' ');
      bar.textContent = ids.length + ' marked - run this to record them:';
    } else {
      cmd.value = '';
      bar.textContent = 'Mark each application as you send it, then run ' +
        'the command that appears here.';
    }
    var show = document.getElementById('bar-show');
    if (show) { show.textContent = cmd.value; }
  }
  document.addEventListener('click', function (event) {
    var target = event.target;
    var copy = target.getAttribute && target.getAttribute('data-copy');
    if (copy) {
      var field = document.getElementById(copy);
      if (field) { copyFrom(field, target); }
      return;
    }
    var mark = target.getAttribute && target.getAttribute('data-mark');
    if (mark) {
      var ids = load();
      var at = ids.indexOf(mark);
      if (at === -1) { ids.push(mark); } else { ids.splice(at, 1); }
      save(ids);
      refresh();
    }
  });
  refresh();
})();
"""


def _esc(text: object) -> str:
    return html.escape(str(text or ""), quote=True)


def _rows(value: str) -> int:
    lines = value.count("\n") + max(1, len(value) // 70)
    return max(1, min(10, lines))


def _answer_block(sheet: AnswerSheet, index: int, answer) -> list[str]:
    field_id = f"f{sheet.job_id}-{index}"
    classes = ["field"]
    if answer.source == "voluntary":
        classes.append("voluntary")
    elif answer.needs_input:
        classes.append("needs-input")

    out = [f'<li class="{" ".join(classes)}">',
           '<div class="q">', f'<span>{_esc(answer.label)}</span>']
    if answer.source:
        out.append(f'<span class="src {_esc(answer.source)}">'
                   f'{_esc(answer.source)}</span>')
    if answer.required:
        out.append('<span class="req">required</span>')
    out.append("</div>")

    if answer.source == "voluntary":
        out.append(f'<div class="hint">{_esc(answer.note)}</div></li>')
        return out

    placeholder = "" if answer.value else "- your answer -"
    empty = "" if answer.value else " empty"
    out += [
        '<div class="row">',
        f'<textarea id="{field_id}" class="val{empty}" readonly '
        f'rows="{_rows(answer.value)}" '
        f'placeholder="{placeholder}">{_esc(answer.value)}</textarea>',
        f'<button data-copy="{field_id}">copy</button>',
        "</div>",
    ]
    if answer.note:
        out.append(f'<div class="hint">{_esc(answer.note)}</div>')
    if answer.needs_input and not answer.note:
        out.append('<div class="hint">karani has no source for this one '
                   '- answer it yourself on the form.</div>')
    out.append("</li>")
    return out


def _sheet_text(sheet: AnswerSheet) -> str:
    """The whole sheet as plain text, for one copy into a notes app."""
    lines = [f"{sheet.company} - {sheet.title}", sheet.apply_url, ""]
    for answer in sheet.answers:
        if answer.source == "voluntary":
            continue
        lines += [f"{answer.label}", answer.value or "[needs your answer]", ""]
    return "\n".join(lines)


def _card(sheet: AnswerSheet) -> str:
    out = [f'<section class="card" data-job="{sheet.job_id}">',
           '<div class="head">',
           f'<h2>{_esc(sheet.title)}</h2>',
           f'<span class="co">{_esc(sheet.company)}</span>']
    if sheet.needs_review:
        out.append('<span class="badge review">needs review</span>')
    if not sheet.introspected:
        out.append('<span class="badge fallback">standard fields</span>')
    out.append("</div>")

    out.append('<div class="meta">')
    if sheet.apply_url:
        out.append(f'<a class="apply" href="{_esc(sheet.apply_url)}" '
                   f'target="_blank" rel="noopener noreferrer">'
                   f'open the form</a>')
    counted = sum(1 for a in sheet.answers if a.source != "voluntary")
    out.append(f'<span>{counted} fields - {sheet.answered} answered'
               + (f", {sheet.outstanding} need you" if sheet.outstanding
                  else "") + f' - job {sheet.job_id}</span>')
    out.append("</div>")

    if sheet.documents:
        out.append('<div class="docs">')
        for name, url in sheet.documents.items():
            out.append(f'<a href="{_esc(url)}" target="_blank" '
                       f'rel="noopener noreferrer">{_esc(name)}</a>')
        out.append("</div>")

    if sheet.legal_note:
        out.append(f'<div class="note">{_esc(sheet.legal_note)}</div>')
    if sheet.question_note and not sheet.introspected:
        out.append(f'<div class="hint">{_esc(sheet.question_note)}</div>')

    out.append('<ol class="fields">')
    for index, answer in enumerate(sheet.answers):
        out += _answer_block(sheet, index, answer)
    out.append("</ol>")

    all_id = f"all-{sheet.job_id}"
    out += [
        '<div class="actions">',
        f'<button data-copy="{all_id}">copy the whole sheet</button>',
        f'<button class="mark" data-mark="{sheet.job_id}">'
        f'mark submitted</button>',
        "</div>",
        f'<textarea id="{all_id}" class="hidden" readonly>'
        f'{_esc(_sheet_text(sheet))}</textarea>',
        "</section>",
    ]
    return "\n".join(out)


def render_console(sheets: Sequence[AnswerSheet],
                   generated_at: datetime | None = None) -> str:
    now = (generated_at or datetime.now(timezone.utc)).strftime(
        "%A, %B %d, %Y at %H:%M UTC")
    count = len(sheets)
    body = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, '
        'initial-scale=1">',
        "<title>karani · submission console</title>",
        f"<style>{_STYLE}</style></head><body><div class=\"wrap\">",
        "<header><h1>submission console</h1>",
        f'<div class="sub">{count} pack{"" if count == 1 else "s"} ready · '
        f'{_esc(now)}</div>',
        '<div class="rule">karani never submits. Open the form, paste the '
        'answers, attach the documents, click send yourself - then mark it '
        'here.</div></header>',
    ]

    if not sheets:
        body.append('<div class="empty-state">Nothing waiting to be sent. '
                    'Draft a pack first: <code>karani draft &lt;job_id&gt;'
                    '</code></div>')
    else:
        body += [_card(sheet) for sheet in sheets]

    body += [
        '<div class="bar">',
        '<span id="bar-text">Mark each application as you send it, then '
        'run the command that appears here.</span>',
        '<code id="bar-show"></code>',
        '<input id="bar-cmd" class="hidden" readonly value="">',
        '<button data-copy="bar-cmd">copy command</button>',
        "</div>",
        "</div>",
        f"<script>{_SCRIPT}</script>",
        "</body></html>",
    ]
    return "\n".join(body)


__all__ = ["render_console"]
