"""Response watcher: Gmail ingestion, classification, matching, state.

No network and no mailbox: the Gmail client takes an httpx transport, so
every test drives a fake inbox. Credentials are stripped by conftest and
set per test with monkeypatch.setenv.
"""
from __future__ import annotations

import base64

import httpx
import pytest

from karani.watcher.gmail import (
    GMAIL_READONLY_SCOPE,
    SCOPES,
    GmailAuthError,
    GmailClient,
)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _message(
    gmail_id: str = "m1", thread_id: str = "t1",
    sender: str = "Acme Recruiting <no-reply@acme.greenhouse.io>",
    subject: str = "Your application to Acme",
    body: str = "Thanks for applying.",
    internal_date: str = "1761500000000",
) -> dict:
    return {
        "id": gmail_id,
        "threadId": thread_id,
        "internalDate": internal_date,
        "snippet": body[:60],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "To", "value": "kelyn@example.com"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body)}},
                {"mimeType": "text/html",
                 "body": {"data": _b64(f"<p>{body}</p>")}},
            ],
        },
    }


@pytest.fixture
def gmail_creds(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "rtoken")


@pytest.fixture
def inbox():
    """A fake Gmail: list + get + token refresh over httpx.MockTransport."""
    state: dict = {"messages": [_message()], "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "at-1",
                                             "expires_in": 3599})
        if url.rstrip("?").endswith("/users/me/messages") or \
                "/users/me/messages?" in url:
            return httpx.Response(200, json={"messages": [
                {"id": m["id"], "threadId": m["threadId"]}
                for m in state["messages"]
            ]})
        for m in state["messages"]:
            if f"/messages/{m['id']}" in url:
                return httpx.Response(200, json=m)
        return httpx.Response(404, json={"error": "not found"})

    state["transport"] = httpx.MockTransport(handler)
    return state


# --- read-only scope is a hard constraint, not a convention ---

def test_scope_is_read_only():
    assert GMAIL_READONLY_SCOPE.endswith("/auth/gmail.readonly")
    assert SCOPES == (GMAIL_READONLY_SCOPE,)


def test_watcher_package_never_references_a_write_endpoint():
    """A read-only scope makes sending technically impossible; this test
    makes an accidental re-scope visible in review instead of at runtime."""
    import pathlib

    import karani.watcher as watcher_pkg

    root = pathlib.Path(watcher_pkg.__file__).parent
    forbidden = ("gmail.send", "gmail.modify", "gmail.compose",
                 "gmail.insert", "messages/send", "/trash", "/untrash",
                 "messages/batchModify", "auth/gmail.labels")
    for path in sorted(root.rglob("*.py")):
        text = path.read_text()
        for needle in forbidden:
            assert needle not in text, f"{path.name} references {needle}"


def test_missing_credentials_raise_before_any_request():
    with pytest.raises(GmailAuthError):
        GmailClient()


# --- fetching ---

@pytest.mark.asyncio
async def test_recent_returns_parsed_messages(gmail_creds, inbox):
    client = GmailClient(transport=inbox["transport"])
    messages = await client.recent(lookback_days=7, limit=10)

    assert len(messages) == 1
    msg = messages[0]
    assert msg.gmail_id == "m1"
    assert msg.thread_id == "t1"
    assert msg.from_address == "no-reply@acme.greenhouse.io"
    assert msg.from_name == "Acme Recruiting"
    assert msg.from_domain == "acme.greenhouse.io"
    assert msg.subject == "Your application to Acme"
    assert "Thanks for applying" in msg.body
    assert msg.received_at is not None


@pytest.mark.asyncio
async def test_access_token_is_refreshed_once_and_reused(gmail_creds, inbox):
    inbox["messages"] = [_message("m1", "t1"), _message("m2", "t2")]
    client = GmailClient(transport=inbox["transport"])
    await client.recent(lookback_days=7, limit=10)

    token_calls = [r for r in inbox["requests"]
                   if "oauth2.googleapis.com" in str(r.url)]
    assert len(token_calls) == 1
    assert all(r.method == "GET" for r in inbox["requests"]
               if "gmail.googleapis.com" in str(r.url))
    api_calls = [r for r in inbox["requests"]
                 if "gmail.googleapis.com" in str(r.url)]
    assert api_calls[0].headers["authorization"] == "Bearer at-1"


@pytest.mark.asyncio
async def test_query_scopes_the_search_to_the_lookback_window(
        gmail_creds, inbox):
    client = GmailClient(transport=inbox["transport"])
    await client.recent(lookback_days=3, limit=5, extra_query="label:inbox")

    listing = next(r for r in inbox["requests"]
                   if "/users/me/messages" in str(r.url)
                   and "/messages/" not in str(r.url))
    q = listing.url.params["q"]
    assert "newer_than:3d" in q
    assert "label:inbox" in q
    assert listing.url.params["maxResults"] == "5"


@pytest.mark.asyncio
async def test_a_dead_message_fetch_does_not_sink_the_batch(
        gmail_creds, inbox):
    """One unreadable message must not cost us the whole pass."""
    inbox["messages"] = [_message("m1", "t1"), _message("m2", "t2")]

    real = inbox["transport"]

    def handler(request: httpx.Request) -> httpx.Response:
        if "/messages/m1" in str(request.url):
            return httpx.Response(500, json={"error": "boom"})
        return real.handler(request)

    client = GmailClient(transport=httpx.MockTransport(handler))
    messages = await client.recent(lookback_days=7, limit=10)
    assert [m.gmail_id for m in messages] == ["m2"]


@pytest.mark.asyncio
async def test_expired_refresh_token_raises_a_named_error(gmail_creds):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = GmailClient(transport=httpx.MockTransport(handler))
    with pytest.raises(GmailAuthError) as exc:
        await client.recent(lookback_days=7, limit=10)
    assert "invalid_grant" in str(exc.value)


@pytest.mark.asyncio
async def test_plain_text_body_is_preferred_over_html(gmail_creds, inbox):
    inbox["messages"] = [{
        "id": "m9", "threadId": "t9", "internalDate": "1761500000000",
        "snippet": "s",
        "payload": {
            "mimeType": "text/html",
            "headers": [{"name": "From", "value": "a@b.com"},
                        {"name": "Subject", "value": "s"}],
            "body": {"data": _b64("<html><body>Hi <b>there</b></body></html>")},
        },
    }]
    client = GmailClient(transport=inbox["transport"])
    [msg] = await client.recent(lookback_days=7, limit=10)
    assert "Hi there" in msg.body
    assert "<b>" not in msg.body


@pytest.mark.asyncio
async def test_token_refresh_posts_only_to_the_token_endpoint(
        gmail_creds, inbox):
    client = GmailClient(transport=inbox["transport"])
    await client.recent(lookback_days=7, limit=10)
    posts = [r for r in inbox["requests"] if r.method == "POST"]
    assert len(posts) == 1
    assert "oauth2.googleapis.com/token" in str(posts[0].url)
