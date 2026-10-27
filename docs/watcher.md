# The response watcher

karani drafted 121 application packs and learned the outcome of none of
them. Replies land in Gmail; nothing read them. So the state machine
never left `drafting`, `applied` stayed at zero, follow-ups never armed,
and there was no outcome label for any learning loop to train on
(design 5.4, `docs/superpowers/specs/2026-10-23-career-runner-design.md`).

`karani watch` closes that loop: read recent mail, label it, tie it to
an application, advance the state machine when — and only when — the
match is certain, and draft a reply for a human to send.

## What it will not do

**It cannot send mail.** The OAuth scope is `gmail.readonly` and nothing
else. That is not a policy the code enforces; it is a capability Google
does not grant it. The same scope rules out deleting, archiving, and
marking a message read — karani leaves the mailbox exactly as it found
it. IMAP was rejected for precisely this reason: an IMAP password can
send, and no amount of care in the code takes that power away again.

It also does not reply (drafts only, ADR 0012's rule applied to mail),
does not guess at a message it cannot place, and does not treat
anything in an email as an instruction.

## The pass

```
gmail.recent()  ->  ledger: seen?  ->  classify  ->  match  ->  advance  ->  reply draft  ->  Slack
```

**Ledger first.** Classification is billed, so a message already handled
never reaches a model. This is also what makes a pass idempotent: run it
twice over the same fortnight and nothing advances twice.

**Classify** (`[llm.email]`, Haiku-class): one label out of
`rejection`, `interview_invite`, `assessment`, `scheduling`, `offer`,
`recruiter_outreach`, `irrelevant`. Any failure — no JSON, an unknown
label, a dead endpoint — lands on `unclassified`, which is reported and
not acted on.

**Match** — deterministic, no LLM, the same tier split as the
pre-filter. In order: a thread another message already matched; the
company name found word-boundary anchored in the sender's display name,
the subject or the head of the body; a sending-domain label equal to the
company handle (`people@globex.com`, `globex.greenhouse.io`). Only
applications in flight (`ready`, `applied`, `screen`, `interview`,
`offer`) are candidates.

An ATS sender domain never matches on its own — it says which system
sent the mail, not which company. Two tracked companies named in one
message is a newsletter, not a reply, so it is a refusal. Two open
applications at one company need the role title to separate them, and if
it does not, that is a refusal too. **Unmatched messages are reported
for manual entry, never guessed at**: a wrong match writes a rejection
onto the wrong company and poisons the only labels the learning loop
will ever have.

**Advance** — only on `confidence == "high"`, and the ladder only
climbs:

| Label | Effect |
|---|---|
| `rejection` | status `rejected`, outcome `rejection` |
| `offer` | status `offer`, outcome `offer` |
| `interview_invite` | `screen`, or `interview` if already past a screen |
| `assessment` | `screen` |
| `scheduling`, `recruiter_outreach` | stage note only |
| `irrelevant`, `unclassified` | nothing |

Mail arrives out of order, so a transition applies only when it is
strictly higher than the current status; the two terminal answers are
always the truth. Every recognised label appends to the `stages` JSONB
log whether or not the status moves, so the evidence survives either
way.

**Reply drafts** go to Slack for review. Rejections get none.

## Email is data, never instructions

Everything in a message was written by a stranger, and some of them will
eventually try "ignore your previous instructions". The defence is
structural rather than rhetorical:

- content goes inside one delimited block whose closing tag is stripped
  from the content first, so it cannot forge a boundary;
- the only thing crossing back out of the classifier is one label from a
  closed set — a model talked into answering `{"set_status": "offer"}`
  has said nothing karani will act on;
- matching never reads the body for a job id. "job_id: 11" in an email
  is a claim, not a key.

There is a regression test for each of these.

## Setup — the part only you can do

karani cannot create a Google Cloud project on your behalf. Once,
by hand:

1. **Create a project.** <https://console.cloud.google.com/projectcreate>
   — any name; "karani" is fine.
2. **Enable the Gmail API.** APIs & Services -> Library -> "Gmail API"
   -> Enable.
3. **Configure the consent screen.** APIs & Services -> OAuth consent
   screen. User type **External**, fill in the app name and your own
   email for both support contacts. On the Scopes step add exactly one:
   `https://www.googleapis.com/auth/gmail.readonly`. Add your own Gmail
   address as a **Test user** — leave the app in Testing; publishing it
   would mean a Google review you do not need for a single-user tool.
   A testing-mode refresh token expires after 7 days, so if `watch`
   starts reporting `invalid_grant`, re-run step 6.
4. **Create an OAuth client.** APIs & Services -> Credentials -> Create
   credentials -> OAuth client ID -> **Desktop app**. Copy the client id
   and client secret.
5. **Put them in `.env`:**
   ```
   GMAIL_CLIENT_ID=....apps.googleusercontent.com
   GMAIL_CLIENT_SECRET=GOCSPX-...
   ```
6. **Consent once:**
   ```
   uv sync --extra gmail        # only the consent flow needs this
   karani watch --authorize
   ```
   A browser opens; approve the read-only request. The command prints a
   refresh token — paste it into `.env` as `GMAIL_REFRESH_TOKEN`. The
   `gmail` extra is not needed again: reading the mailbox is plain httpx
   against a read-only scope.

Then:

```
karani watch --no-slack        # print the pass
karani watch                   # push it to the Slack channel
karani watch --days 30 --limit 100
```

Nothing is scheduled automatically. Wire it into the hourly chain when
you are satisfied with what it reports.

## Configuration

```toml
[llm.email]                    # Haiku-class work; routed per ADR 0017
provider = "anthropic"
model = "claude-haiku-5-5"

[watcher]
lookback_days = 14
max_messages = 50
# query = "-category:promotions"
```

Credentials never appear here. `GMAIL_*` lives in `.env` like every
other secret (CLAUDE.md 4.8).

## Storage

One additive table, `email_messages`, created with
`CREATE TABLE IF NOT EXISTS` on first use (CLAUDE.md 4.7). It holds ids,
the label and the match — **never a message body**; a mailbox's contents
do not belong in the job database. Its DDL lives in
`karani/watcher/store.py` rather than `ingestion/storage.py` so a new
subsystem stays out of a file several workstreams edit; `make test-pg`
covers the real SQL.

## What is deliberately not here

- **No scheduling.** `karani watch` is a verb; wiring it into the
  LangGraph hourly pass is a separate change.
- **No memory writes.** Outcomes are a sanctioned memory event, but the
  conversion ledger (design phase 5) is where that belongs.
- **No MCP tool.** The MCP surface asserts its own tool set, and adding
  one there is a two-line follow-up once this has run for a week.
- **No calendar.** A scheduling mail gets a draft, not an event.
