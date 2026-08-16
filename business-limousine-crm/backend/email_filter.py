"""
Pre-classification filter that runs before ai_classifier.py, to avoid
spending Gemini API calls on obvious non-lead mail (system notifications,
newsletters, vendor spam...).

Three layers, checked in this order:
1. Whitelist  (table `allowed_senders`) -> exact domain/email match -> NEVER
   filtered, always goes to the AI classifier. Checked first so a known
   corporate account can never be accidentally blocked.
2. Blacklist  (table `blocked_senders`) -> exact domain/email match -> skip
   the AI entirely, force-classify as OTHER.
3. Heuristics (sender pattern + subject keywords, scored) -> high enough
   score -> skip the AI, force-classify as OTHER.

pre_filter() returns None when the email should still go through the
normal classify_email() call in ai_classifier.py.
"""
import logging

import models

logger = logging.getLogger("email_filter")

# Weight given when the sender's local-part matches an automated-mail
# pattern (noreply@, notifications@, ...) — a strong signal on its own.
SENDER_PATTERN_WEIGHT = 2

# Weight given per matched subject keyword, capped so that a single
# ambiguous keyword (e.g. "facture") can never trigger OTHER by itself.
SUBJECT_KEYWORD_WEIGHT = 1
SUBJECT_KEYWORD_CAP = 2

# Combined score needed to skip the AI call and auto-classify as OTHER.
SCORE_THRESHOLD = 3

SENDER_AUTOMATED_PATTERNS = [
    "no-reply@", "noreply@", "donotreply@", "do-not-reply@",
    "notifications@", "notification@", "notify@",
    "mailer-daemon@", "postmaster@", "bounce@", "bounces@",
]

# Bilingual (FR/EN) since the mailbox receives both. Keep this list narrow —
# it is a *signal*, not a decision on its own (see SUBJECT_KEYWORD_CAP).
SUBJECT_KEYWORDS = [
    "unsubscribe", "désabonner", "désinscription", "se désinscrire",
    "newsletter", "backup", "sauvegarde",
    "invoice reminder", "rappel de paiement",
    "confirm your subscription", "confirmez votre abonnement",
]

_FALLBACK_OTHER = {
    "category": "OTHER",
    "confidence": 1.0,
    "client_name": None,
    "client_email": None,
    "client_phone": None,
    "trip_date": None,
    "origin": None,
    "destination": None,
    "summary": None,
}


def _domain_of(addr):
    if not addr or "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1].strip().lower()


def _other_result(reason):
    result = dict(_FALLBACK_OTHER)
    result["summary"] = f"Filtered pre-AI: {reason}"
    return result


def pre_filter(conn, from_addr, subject, body_text=""):
    """
    Returns a classification dict shaped like ai_classifier.classify_email()'s
    output if this email should be auto-classified as OTHER without calling
    Gemini, or None if it should still go through normal AI classification.
    """
    from_addr = (from_addr or "").strip().lower()
    subject_lower = (subject or "").strip().lower()
    domain = _domain_of(from_addr)

    if not from_addr:
        return None

    # --- Layer 1: whitelist always wins, checked first ---
    if models.is_sender_allowed(conn, from_addr, domain):
        return None

    # --- Layer 2: blacklist -> instant OTHER, zero AI call ---
    blocked = models.get_matching_blocked_sender(conn, from_addr, domain)
    if blocked:
        models.record_blocked_sender_hit(conn, blocked["id"])
        logger.info("Blocked sender matched (pattern=%s): %s", blocked["pattern"], from_addr)
        return _other_result(f"blocklist match ({blocked['pattern']})")

    # --- Layer 3: heuristics ---
    score = 0
    matched = []

    if any(p in from_addr for p in SENDER_AUTOMATED_PATTERNS):
        score += SENDER_PATTERN_WEIGHT
        matched.append("automated sender pattern")

    keyword_hits = sum(1 for kw in SUBJECT_KEYWORDS if kw in subject_lower)
    if keyword_hits:
        score += min(keyword_hits * SUBJECT_KEYWORD_WEIGHT, SUBJECT_KEYWORD_CAP)
        matched.append(f"{keyword_hits} subject keyword(s)")

    if score >= SCORE_THRESHOLD:
        logger.info(
            "Heuristic filter matched (%s, score=%d): %s",
            ", ".join(matched), score, from_addr,
        )
        return _other_result(f"heuristics ({', '.join(matched)}, score {score})")

    return None
