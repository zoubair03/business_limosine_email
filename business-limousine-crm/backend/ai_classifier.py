"""
Calls the Google Gemini API to classify an inbound email and pull out
structured client/trip details in a single request.

Uses the raw REST endpoint (rather than the google-generativeai SDK) to
keep the dependency footprint small and the request/response shape fully
visible and easy to debug.
"""
import json
import logging
import time

import requests

from config import Config

logger = logging.getLogger("ai_classifier")

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

VALID_CATEGORIES = {"NEW_REQUEST", "DISCUSSION", "OTHER"}

PROMPT_TEMPLATE = """You are the mail classifier for a chauffeur / limousine service called \
"Business Limousine". You will be shown one email (subject + body) from the company's shared \
inbox. Classify it and, when possible, extract booking details.

Categories (choose exactly one):
- NEW_REQUEST: a prospective or returning customer asking for a quote, availability, or to book \
a ride, and this looks like the START of a new conversation (not a reply in an existing thread).
- DISCUSSION: a follow-up message that is clearly part of an existing conversation with a \
customer (replies, confirmations, questions about an existing booking, thank-you notes, etc).
- OTHER: anything not from a real customer lead — newsletters, spam, social media notifications, \
vendor marketing, automated system mail, etc.

Respond with ONLY a single JSON object (no markdown fences, no commentary) with exactly these \
keys:
{{
  "category": "NEW_REQUEST" | "DISCUSSION" | "OTHER",
  "confidence": <number 0.0-1.0>,
  "client_name": <string or null>,
  "client_email": <string or null>,
  "client_phone": <string or null>,
  "trip_date": <string or null, in the email's own wording, e.g. "August 20, 2026 10:00 AM">,
  "origin": <string or null>,
  "destination": <string or null>,
  "summary": <one short sentence summarizing the email>
}}

Email subject: {subject}
Email from: {from_name} <{from_addr}>
Email body:
---
{body}
---
"""

_FALLBACK = {
    "category": "OTHER",
    "confidence": 0.0,
    "client_name": None,
    "client_email": None,
    "client_phone": None,
    "trip_date": None,
    "origin": None,
    "destination": None,
    "summary": None,
}


def _strip_code_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def classify_email(subject, body, from_name="", from_addr="", timeout=30):
    """
    Returns a dict shaped like _FALLBACK. Never raises: on any failure
    (network, auth, bad JSON) it logs and returns the OTHER fallback so a
    single flaky API call can never crash the sync worker.
    """
    if not Config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not configured; skipping AI classification")
        return dict(_FALLBACK)

    prompt = PROMPT_TEMPLATE.format(
        subject=subject or "(no subject)",
        from_name=from_name or "",
        from_addr=from_addr or "",
        body=(body or "")[:6000],  # keep prompts bounded
    )

    url = GEMINI_ENDPOINT.format(model=Config.GEMINI_MODEL)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    parsed = None
    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                params={"key": Config.GEMINI_API_KEY},
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 429:
                logger.warning("Gemini rate limited (429); waiting before retry (attempt %d/3)", attempt + 1)
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(_strip_code_fences(text))
            break
        except Exception as exc:
            if attempt == 2:
                logger.error("Gemini classification failed: %s", exc)
                return dict(_FALLBACK)
            time.sleep(1)

    if not parsed:
        return dict(_FALLBACK)

    result = dict(_FALLBACK)
    result.update({k: v for k, v in parsed.items() if k in _FALLBACK})

    if result["category"] not in VALID_CATEGORIES:
        result["category"] = "OTHER"
    try:
        result["confidence"] = float(result["confidence"] or 0.0)
    except (TypeError, ValueError):
        result["confidence"] = 0.0

    return result
