"""Gmail ingestion over the REST API, with a READ-ONLY OAuth scope.

Why the API and not IMAP: IMAP hands over a password that can send. The
`gmail.readonly` scope cannot — there is no code path, in this module or
anywhere downstream, that could mail something on the user's behalf even
if it tried. That is the point of the choice, so the scope tuple below
is a one-line constant and a test asserts nothing in the package names a
write endpoint.

The runtime needs no Google SDK: a refresh-token grant is one form POST
and the two calls we make are plain GETs. `google-auth-oauthlib` is only
pulled in by `authorize()`, the one-time flow that mints the refresh
token, and lives behind the optional `gmail` extra (guarded import, same
pattern as the Anthropic SDK).

Credentials come from the environment only (CLAUDE.md 4.8):
GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import re
from datetime import datetime, timezone

import httpx

from .models import InboundMessage

log = logging.getLogger(__name__)

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES: tuple[str, ...] = (GMAIL_READONLY_SCOPE,)

API_BASE = "https://gmail.googleapis.com/gmail/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - public URL

ENV_CLIENT_ID = "GMAIL_CLIENT_ID"
ENV_CLIENT_SECRET = "GMAIL_CLIENT_SECRET"  # noqa: S105 - var name, not a key
ENV_REFRESH_TOKEN = "GMAIL_REFRESH_TOKEN"  # noqa: S105 - var name, not a key

# Body cap: a long quoted thread is mostly our own text, and the
# classifier is billed per token.
MAX_BODY_CHARS = 8000

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


class GmailAuthError(RuntimeError):
    """Credentials are missing, or Google refused them."""


def configured() -> bool:
    """True when all three env vars are present — the watcher is opt-in
    and a missing mailbox must degrade, not crash (ADR 0019's contract)."""
    return all(os.getenv(v) for v in
               (ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_REFRESH_TOKEN))


def _decode(data: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", text)
    text = _TAG.sub("", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    text = _WS.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _walk(part: dict, out: dict[str, str]) -> None:
    """Collect the first text/plain and text/html bodies in the tree."""
    mime = (part.get("mimeType") or "").lower()
    data = (part.get("body") or {}).get("data")
    if data and mime in ("text/plain", "text/html") and mime not in out:
        out[mime] = _decode(data)
    for child in part.get("parts") or ():
        _walk(child, out)


def _headers(payload: dict) -> dict[str, str]:
    return {(h.get("name") or "").lower(): h.get("value") or ""
            for h in payload.get("headers") or ()}


def _split_from(value: str) -> tuple[str, str]:
    """'Acme Recruiting <no-reply@acme.io>' -> (name, address)."""
    match = re.match(r"\s*(?:\"?(?P<name>[^\"<]*?)\"?)?\s*<(?P<addr>[^>]+)>",
                     value or "")
    if match:
        return (match.group("name") or "").strip(), match.group("addr").strip()
    return "", (value or "").strip()


def parse_message(raw: dict) -> InboundMessage:
    """Gmail's `messages.get` payload -> our model. Pure, so the parser
    is testable without a transport."""
    payload = raw.get("payload") or {}
    headers = _headers(payload)
    name, address = _split_from(headers.get("from", ""))

    bodies: dict[str, str] = {}
    _walk(payload, bodies)
    body = bodies.get("text/plain") or _strip_html(bodies.get("text/html", ""))
    if not body:
        body = raw.get("snippet") or ""

    received: datetime | None = None
    internal = raw.get("internalDate")
    if internal:
        try:
            received = datetime.fromtimestamp(int(internal) / 1000,
                                              tz=timezone.utc)
        except (TypeError, ValueError):
            received = None

    return InboundMessage(
        gmail_id=raw.get("id") or "",
        thread_id=raw.get("threadId") or "",
        from_address=address,
        from_name=name,
        subject=headers.get("subject", ""),
        body=body[:MAX_BODY_CHARS],
        received_at=received,
    )


class GmailClient:
    """Read-only Gmail reader.

    `transport` exists so tests drive a fake inbox: the suite must never
    reach a real mailbox (CLAUDE.md 6).
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._client_id = client_id or os.getenv(ENV_CLIENT_ID, "")
        self._client_secret = client_secret or os.getenv(ENV_CLIENT_SECRET, "")
        self._refresh_token = refresh_token or os.getenv(ENV_REFRESH_TOKEN, "")
        missing = [name for name, value in (
            (ENV_CLIENT_ID, self._client_id),
            (ENV_CLIENT_SECRET, self._client_secret),
            (ENV_REFRESH_TOKEN, self._refresh_token),
        ) if not value]
        if missing:
            raise GmailAuthError(
                f"{', '.join(missing)} not set. Run `karani watch "
                f"--authorize` once, then put the values in .env."
            )
        self._transport = transport
        self._timeout = timeout
        self._access_token: str | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport,
                                 timeout=self._timeout)

    async def _token(self) -> str:
        """Exchange the refresh token once per client instance.

        Access tokens live an hour and a watch pass is seconds, so there
        is no expiry bookkeeping to get wrong.
        """
        if self._access_token:
            return self._access_token
        async with self._client() as http:
            response = await http.post(TOKEN_URL, data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            })
        if response.status_code != 200:
            detail = ""
            try:
                detail = response.json().get("error", "")
            except ValueError:
                detail = response.text[:120]
            raise GmailAuthError(
                f"Gmail refused the refresh token ({detail or response.status_code}). "
                f"Re-run `karani watch --authorize`."
            )
        self._access_token = response.json().get("access_token", "")
        if not self._access_token:
            raise GmailAuthError("Gmail returned no access token.")
        return self._access_token

    async def _get(self, http: httpx.AsyncClient, path: str,
                   params: dict | None = None) -> dict:
        response = await http.get(
            f"{API_BASE}{path}", params=params,
            headers={"Authorization": f"Bearer {await self._token()}"},
        )
        response.raise_for_status()
        return response.json()

    async def recent(
        self, *, lookback_days: int = 14, limit: int = 50,
        extra_query: str = "",
    ) -> list[InboundMessage]:
        """Messages from the last `lookback_days`, newest first.

        A single unreadable message is logged and dropped — one bad row
        must not cost the pass, the same contract the ingestion fetchers
        hold.
        """
        query = " ".join(part for part in
                         (f"newer_than:{int(lookback_days)}d", extra_query)
                         if part)
        await self._token()
        out: list[InboundMessage] = []
        async with self._client() as http:
            listing = await self._get(http, "/users/me/messages", {
                "q": query, "maxResults": max(1, int(limit)),
            })
            for stub in (listing.get("messages") or ())[:limit]:
                gmail_id = stub.get("id")
                if not gmail_id:
                    continue
                try:
                    raw = await self._get(
                        http, f"/users/me/messages/{gmail_id}",
                        {"format": "full"})
                except Exception as exc:
                    log.warning("gmail: could not read message %s (%s)",
                                gmail_id, exc)
                    continue
                out.append(parse_message(raw))
        return out


def authorize(*, port: int = 0) -> str:
    """One-time consent flow; prints the refresh token to paste into .env.

    Needs `pip install 'karani[gmail]'`. Imported here rather than at
    module scope so the optional dependency stays optional — the same
    pattern the Anthropic provider uses.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:  # pragma: no cover - exercised by hand
        raise GmailAuthError(
            "The authorization flow needs the `gmail` extra: "
            "uv sync --extra gmail (or pip install 'karani[gmail]')."
        ) from exc

    client_id = os.getenv(ENV_CLIENT_ID, "")
    client_secret = os.getenv(ENV_CLIENT_SECRET, "")
    if not (client_id and client_secret):
        raise GmailAuthError(
            f"Set {ENV_CLIENT_ID} and {ENV_CLIENT_SECRET} first — they come "
            f"from the OAuth client you create in Google Cloud. "
            f"See docs/watcher.md."
        )
    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URL,
            "redirect_uris": ["http://localhost"],
        }},
        scopes=list(SCOPES),
    )
    credentials = flow.run_local_server(port=port)
    if not credentials.refresh_token:  # pragma: no cover
        raise GmailAuthError(
            "Google returned no refresh token — revoke karani's access at "
            "myaccount.google.com/permissions and run this again."
        )
    return credentials.refresh_token
