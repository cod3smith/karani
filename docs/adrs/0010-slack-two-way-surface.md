# 0010 · Slack as the two-way daily surface (Socket Mode bridge)

**Status:** accepted

## Context

The daily loop needed a push channel (roadmap 1.3) and Kelyn wants to
talk back — record verdicts, request drafts, query memory — from the same
place the digest lands. Options: email (one-way), a web dashboard
(deferred, Tier 6), or Slack.

Inbound Slack requires receiving events. The Events API over HTTP needs a
public HTTPS endpoint — wrong for a pipeline that runs on a laptop.
Socket Mode holds an outbound websocket instead: no ingress, no tunnel.

## Decision

`slackbridge/` package, split by dependency weight:

1. **Push path is dependency-free.** `SlackClient` speaks the Web API
   with httpx (same pattern as the OpenRouter client): `chat.postMessage`
   with Block Kit bodies for the digest and next-actions pushes. Works in
   cron with zero optional deps.
2. **Pull path is an optional extra.** The Socket Mode listener
   (`python -m slackbridge`) needs `uv sync --extra slack` (slack-sdk),
   import-guarded like anthropic/mem0. It turns messages (`verdict 123
   apply`, `actions`, `prep 45`, `recall gitlab`) into the same
   Storage/runner/memory calls the CLI and MCP server make — a third thin
   adapter, zero business logic.
3. **Safety rules in the listener:** ack every envelope before processing
   (Slack redelivers un-acked envelopes — that would double-execute
   billed commands); ignore bot messages and subtypes (loop protection);
   honor `SLACK_CHANNEL` as a conversation filter; every command handler
   catches its own errors and replies with a message — a typo can never
   kill the daemon.
4. **Karani drafts, Kelyn sends.** The bridge posts materials and
   worklists into Slack; it never messages anyone else or submits
   anything on his behalf (vision non-goals).

## Consequences

- **Positive:** digest, fast-lane alerts, and follow-up reminders arrive
  where Kelyn already is; reacting takes one reply, feeding verdicts and
  memory with near-zero friction.
- **Positive:** command dispatch is a pure function
  (`handle_command`) — fully testable without slack-sdk or a socket.
- **Negative:** a third surface to keep in sync with CLI/MCP. Mitigation:
  same rule as ADR 0008 — capability lands in Storage/runners first.
- **Negative:** Socket Mode needs a long-running process. Acceptable — it
  can run under launchd next to the cron loop; pushes still work when
  it's down.
- **Neutral:** requires a one-time Slack app setup (Socket Mode on,
  `chat:write` + `im:history` scopes, `message.im` event subscription);
  documented in `slackbridge/listener.py` and `.env.example`.
