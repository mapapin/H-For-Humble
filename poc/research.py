"""Linkup: real-time web research around an audit.

- suggest_journeys(url): what the site is, the journeys real visitors come for (written as goals our agents can
  run, ending before any payment or submission), and whether the site publishes an accessibility statement.
- site_context(url): the declared accessibility compliance (RGAA / WCAG statement) and the legal context, shown
  next to what the agents measured: "declared partially compliant" vs "keyboard users cannot finish the journey".

Needs LINKUP_API_KEY. Local or private sites (localhost, 10.x, 192.168.x...) are invisible to a web search, so we
skip them. Every call has a timeout and returns None on failure: research never blocks an audit.
"""

from __future__ import annotations

import ipaddress
import json
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

API = "https://api.linkup.so/v1/search"
TIMEOUT_S = 60

JOURNEYS_SCHEMA = {
    "type": "object",
    "properties": {
        "site_name": {"type": "string"},
        "what_it_is": {"type": "string", "description": "One sentence"},
        "audience": {"type": "string", "description": "Who uses it"},
        "journeys": {
            "type": "array",
            "description": "3 key visitor journeys",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short name, 3-6 words"},
                    "goal": {"type": "string", "description": "Instruction for a tester, ending with the information to report"},
                    "why": {"type": "string", "description": "Why this journey matters for the business"},
                },
                "required": ["title", "goal"],
            },
        },
        "accessibility_statement_url": {"type": "string"},
        "declared_compliance": {"type": "string", "description": "What the statement declares, or 'none found'"},
    },
    "required": ["site_name", "what_it_is", "journeys"],
}

CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "organisation": {"type": "string"},
        "sector": {"type": "string"},
        "accessibility_statement_url": {"type": "string", "description": "URL of its accessibility statement, or empty"},
        "declared_compliance": {"type": "string", "description": "Declared level (e.g. 'partially compliant with RGAA 4.1, 62%'), or 'none found'"},
        "audit_date": {"type": "string", "description": "Date of the declared audit, or empty"},
        "self_assessed": {"type": "boolean", "description": "True if the declared audit was internal"},
        "legal_context": {"type": "string", "description": "Which accessibility laws apply to this kind of service in its market (e.g. the European Accessibility Act since June 2025 for e-commerce), in two sentences"},
    },
    "required": ["organisation", "declared_compliance", "legal_context"],
}


def is_public(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".test", ".local", ".internal")):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return "." in host


def available() -> bool:
    return bool(os.environ.get("LINKUP_API_KEY"))


def _search(q: str, schema: dict, depth: str = "standard") -> dict[str, Any] | None:
    key = os.environ.get("LINKUP_API_KEY")
    if not key:
        return None
    r = httpx.post(API, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT_S, json={
        "q": q, "depth": depth, "outputType": "structured", "structuredOutputSchema": json.dumps(schema),
        "includeSources": True,
    })
    r.raise_for_status()
    body = r.json()
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    sources = [{"name": s.get("name"), "url": s.get("url")} for s in body.get("sources") or [] if s.get("url")][:6]
    return {**data, "sources": sources}


def suggest_journeys(url: str) -> dict[str, Any] | None:
    if not is_public(url):
        return None
    q = (f"Website {url} : what is it and who uses it? List the 3 most important things visitors come to do on this "
         "website, written as goals for a usability tester using only this website (one sentence each, imperative, "
         "starting from the home page, never paying, ordering, applying, signing up or submitting a form, and "
         "ending with the specific information the tester must report, e.g. 'report the opening hours shown'). "
         "Also: does it publish an accessibility statement (déclaration d'accessibilité, RGAA or WCAG), and what "
         "compliance does it declare?")
    data = _search(q, JOURNEYS_SCHEMA)
    if not data:
        return None
    data["journeys"] = [j for j in data.get("journeys") or [] if j.get("title") and len(j.get("goal") or "") >= 10][:3]
    return {**data, "source": "linkup"}


def site_context(url: str) -> dict[str, Any] | None:
    if not is_public(url):
        return None
    host = urlsplit(url).hostname
    q = (f"Accessibility of the website {host}: find its accessibility statement (déclaration d'accessibilité / "
         "accessibility statement), the compliance level it declares (RGAA or WCAG, percentage if given), the date "
         "and whether the audit was internal. Also which accessibility laws apply to this kind of service in its "
         "market (for example the European Accessibility Act, French RGAA obligations).")
    data = _search(q, CONTEXT_SCHEMA)
    return {**data, "source": "linkup", "url": url} if data else None
