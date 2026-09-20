"""Reading a story or an analysis page, so scenarios can be written from it.

The requirements are already written down, in Jira or in Confluence, and
retyping them into a brief is both work and a chance to leave something out.
Paste the link and what is behind it becomes the text the scenario writer
already takes.

Both of these are Server / Data Center rather than Cloud, which decides the
whole authentication story: a Personal Access Token in an `Authorization:
Bearer` header. Cloud wants an API token sent as Basic auth against the
account's email address, which is a different credential entirely — sending one
where the other is expected is a 401 that says nothing useful.

Nothing is interpreted here. Whatever the model is going to be given, the
tester can read first.
"""

import html
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import httpx

import config
from documents import MAX_CHARS, TRUNCATION_NOTE, tidy

TIMEOUT = 30.0


class TrackerError(Exception):
    """The link could not be read, with a reason worth showing a tester."""


def settings(service: str) -> Tuple[str, str]:
    """The base URL and token for one service, read at call time.

    Read now rather than at import because they are saved from Settings while
    the server is running, and the next paste is expected to use them.
    """
    import os

    prefix = "JIRA" if service == "jira" else "CONFLUENCE"
    base = (os.environ.get(f"{prefix}_BASE_URL", "") or "").strip().rstrip("/")
    token = (os.environ.get(f"{prefix}_TOKEN", "") or "").strip()
    return base, token


def configured(service: str) -> bool:
    base, token = settings(service)
    return bool(base and token)


def service_for(url: str) -> Optional[str]:
    """Which of the two a link belongs to.

    Decided by the configured host first, because a company can call them
    anything; the path shapes are the fallback for a link pasted before the
    settings were saved.
    """
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return None
    for service in ("jira", "confluence"):
        base, _ = settings(service)
        if base and (urlparse(base).hostname or "").lower() == host:
            return service
    if "confluence" in host or "/display/" in url or "pageId=" in url:
        return "confluence"
    if "jira" in host or re.search(r"/browse/[A-Z][A-Z0-9_]+-\d+", url or ""):
        return "jira"
    return None


def issue_key(url: str) -> Optional[str]:
    """The ABC-123 in a Jira link, wherever it sits.

    `/browse/ABC-123` is the common shape, but a link copied out of a board or
    a filter carries it as `selectedIssue=` instead, and a tester pasting one
    of those has not done anything wrong.
    """
    match = re.search(r"/browse/([A-Z][A-Z0-9_]*-\d+)", url or "", re.I)
    if match:
        return match.group(1).upper()
    query = parse_qs(urlparse(url or "").query)
    for key in ("selectedIssue", "issueKey", "issue"):
        value = (query.get(key) or [""])[0]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", value):
            return value.upper()
    if re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", (url or "").strip(), re.I):
        return url.strip().upper()
    return None


def confluence_target(url: str) -> Optional[Dict[str, str]]:
    """How to ask Confluence for the page a link points at.

    Two shapes, and they need different requests: `pageId=` is the id, while
    `/display/SPACE/Page+Title` names it, which has to be looked up by space
    and title because the id is nowhere in the link.
    """
    parsed = urlparse(url or "")
    page_id = (parse_qs(parsed.query).get("pageId") or [""])[0]
    if page_id.isdigit():
        return {"id": page_id}
    match = re.search(r"/display/([^/]+)/([^/?#]+)", parsed.path or "")
    if match:
        return {
            "space": unquote(match.group(1)),
            "title": unquote(match.group(2)).replace("+", " "),
        }
    match = re.search(r"/spaces/([^/]+)/pages/(\d+)", parsed.path or "")
    if match:
        return {"id": match.group(2)}
    return None


_TAG = re.compile(r"<[^>]+>")
_ROW_END = re.compile(r"</t[dh]>\s*", re.I)
_BLOCK_END = re.compile(r"</(p|div|li|tr|h[1-6]|table)>", re.I)


def from_storage(markup: str) -> str:
    """Confluence's storage format as prose.

    It is XHTML, so the tags have to go — but not before the shape they carry
    is turned into something readable. Acceptance criteria live in tables here
    as often as in paragraphs, and stripping the tags first would run a whole
    table into one unbroken line.
    """
    text = markup or ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = _ROW_END.sub(" | ", text)
    text = _BLOCK_END.sub("\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    # A cell separator left at the end of a row is the tag's, not the text's.
    text = re.sub(r"\s*\|\s*$", "", text, flags=re.M)
    return tidy(text)


async def _get(service: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    base, token = settings(service)
    if not base or not token:
        raise TrackerError(
            f"Add the {service.title()} address and access token in Settings first."
        )
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        response = await client.get(
            f"{base}{path}", params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if response.status_code in (401, 403):
        raise TrackerError(
            f"{service.title()} rejected the token. Check it in Settings — these are "
            "Server installations, so it has to be a Personal Access Token."
        )
    if response.status_code == 404:
        raise TrackerError(f"{service.title()} has nothing at that link.")
    if response.status_code >= 400:
        raise TrackerError(f"{service.title()} answered {response.status_code}.")
    try:
        return response.json()
    except Exception as exc:  # noqa: BLE001
        raise TrackerError(f"{service.title()} did not answer with JSON.") from exc


async def fetch_issue(url: str) -> Dict[str, Any]:
    key = issue_key(url)
    if not key:
        raise TrackerError("That does not look like a Jira issue link (…/browse/ABC-123).")
    data = await _get("jira", f"/rest/api/2/issue/{key}",
                      {"fields": "summary,description,issuetype,status,labels"})
    fields = data.get("fields") or {}
    parts = [f"{key} — {fields.get('summary') or ''}".strip(" —")]
    kind = ((fields.get("issuetype") or {}).get("name") or "").strip()
    if kind:
        parts.append(f"Type: {kind}")
    description = (fields.get("description") or "").strip()
    if description:
        parts += ["", description]
    return {
        "service": "jira", "key": key,
        "title": fields.get("summary") or key,
        "text": tidy("\n".join(parts)),
        "url": url,
    }


async def fetch_page(url: str) -> Dict[str, Any]:
    target = confluence_target(url)
    if not target:
        raise TrackerError(
            "That does not look like a Confluence page link "
            "(…/pages/viewpage.action?pageId=… or …/display/SPACE/Page)."
        )
    if "id" in target:
        data = await _get("confluence", f"/rest/api/content/{target['id']}",
                          {"expand": "body.storage"})
    else:
        found = await _get("confluence", "/rest/api/content", {
            "spaceKey": target["space"], "title": target["title"],
            "expand": "body.storage", "limit": 1,
        })
        results = found.get("results") or []
        if not results:
            raise TrackerError(
                f"Confluence has no page called “{target['title']}” in {target['space']}."
            )
        data = results[0]

    body = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
    title = data.get("title") or "Confluence page"
    return {
        "service": "confluence", "key": str(data.get("id") or ""),
        "title": title,
        "text": tidy(f"{title}\n\n{from_storage(body)}"),
        "url": url,
    }


async def fetch(url: str) -> Dict[str, Any]:
    """Whatever is behind the link, as text, with a note if it was cut."""
    service = service_for(url)
    if service == "jira":
        result = await fetch_issue(url)
    elif service == "confluence":
        result = await fetch_page(url)
    else:
        raise TrackerError(
            "That link is neither a Jira issue nor a Confluence page. Save the "
            "addresses in Settings if they are on your own servers."
        )

    if not result["text"]:
        # An empty brief is answered with invented scenarios, so it is refused
        # here rather than passed on as though it said something.
        raise TrackerError(
            f"“{result['title']}” has no text in it — nothing to write scenarios from."
        )
    note = None
    if len(result["text"]) > MAX_CHARS:
        note = f"Kept the first {MAX_CHARS:,} characters of {len(result['text']):,}."
        result["text"] = result["text"][:MAX_CHARS] + TRUNCATION_NOTE.format(n=MAX_CHARS)
    result["note"] = note
    result["characters"] = len(result["text"])
    return result
