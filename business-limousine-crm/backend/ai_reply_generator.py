"""
AI Smart Reply Generator for Business Limousine.
Uses Google Gemini API to analyze incoming conversation threads and generate
professional, executive-level chauffeur service email replies (Quotes, Confirmations,
Detail Requests, or Custom prompt drafts) matching the client's language.
"""
import json
import logging
import time
import requests

from config import Config

logger = logging.getLogger("ai_reply_generator")

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

PROMPT_TEMPLATE = """You are the Senior Executive Dispatcher at "Business Limousine" — a premier VIP chauffeur and luxury limousine service based in Brussels, Belgium, serving high-profile corporate executives, diplomats, and VIP travelers across Europe.

Analyze the client's information, trip details, and the full email thread below.
Then, compose high-end, courteous, and impeccably phrased email reply options.

CRITICAL RULES:
1. DETECT THE CLIENT'S LANGUAGE (French, English, Dutch, German, Arabic, Portuguese, Spanish, etc.) from the conversation. Write ALL email drafts in that EXACT same language! If client wrote in French, reply in flawless luxury French. If English, in polished executive British/International English.
2. Tone must be prestigious, discreet, professional, and warmly welcoming.
3. If dispatcher provides specific custom instructions or price in DISPATCHER INSTRUCTIONS, incorporate them seamlessly.
4. Fleet reference:
   - Mercedes-Benz S-Class VIP (1-3 passengers, 2 suitcases)
   - Mercedes-Benz V-Class Luxury Van (1-7 passengers, 7 suitcases)
   - Mercedes-Benz E-Class Executive (1-3 passengers, 2 suitcases)
   - Mercedes-Benz Sprinter VIP (up to 16 passengers)
5. Standard VIP Inclusions: Flight tracking in real-time, Meet & Greet with personalized name tablet at arrivals, 60 min free waiting time for airport pickups, complimentary onboard mineral water & high-speed Wi-Fi, all taxes & tolls included.
6. ABSOLUTELY NO SIGNATURE, NO SENDER NAME, NO PHONE NUMBER, AND NO COMPANY FOOTER IN THE DRAFT BODY:
   The CRM system automatically attaches the official HTML signature block separately.
   The draft body MUST end ONLY with a short closing sentence or sign-off line (for example: "Best regards," or "Restant à votre entière disposition," or "Met vriendelijke groet,").
   DO NOT append "Lasaad", "Zoubair", "Phone:", "+32...", or "Business Limousine Services - Worldwide Travel Services".
7. DYNAMIC & NATURAL LANGUAGE:
   Do NOT repeat rigid hardcoded boilerplate. Adapt the phrasing naturally and elegantly to the client's specific questions and tone in their language.

Return ONLY a valid JSON object (no markdown fences, no explanatory text outside the JSON) with this exact schema:
{{
  "language": "<detected language code, e.g. EN, FR, NL, DE, AR, PT, ES>",
  "summary": "<1-sentence summary of client's request in English>",
  "drafts": [
    {{
      "id": "quote",
      "title": "💎 Luxury Quote",
      "subject": "<Re: Subject line in client language>",
      "body": "<Draft body text with polite greeting, clear itinerary breakdown, vehicle proposal, rate details, and closing sentence. NO signature or phone footer.>"
    }},
    {{
      "id": "confirm",
      "title": "✅ VIP Confirmation",
      "subject": "<Re: Subject line in client language>",
      "body": "<Draft body text confirming trip schedule, vehicle, meet & greet details, and closing sentence. NO signature or phone footer.>"
    }},
    {{
      "id": "details",
      "title": "❓ Request Details",
      "subject": "<Re: Subject line in client language>",
      "body": "<Draft body text politely requesting missing parameters and closing sentence. NO signature or phone footer.>"
    }}
  ]
}}

CLIENT DETAILS:
- Name: {client_name}
- Email: {client_email}
- Phone: {client_phone}

TRIP ITINERARY:
- Pickup / Origin: {origin}
- Drop-off / Destination: {destination}
- Schedule / Date: {trip_date}

DISPATCHER INSTRUCTIONS (Optional):
{dispatcher_instructions}

FULL CONVERSATION HISTORY:
{thread_history}
"""


def _strip_code_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def _clean_ai_draft_body(text):
    if not text:
        return ""
    cleaned = text.strip()
    
    # Strip signature markers if the model included any
    markers = [
        "Business Limousine Services",
        "Business Limousine",
        "Groundtransportation",
        "Phone:",
        "Kind regards | Met vriendelijke groet",
        "مع أطيب التحيات",
        "уважением",
    ]
    
    lowest_idx = -1
    for marker in markers:
        idx = cleaned.find(marker)
        if idx != -1:
            if lowest_idx == -1 or idx < lowest_idx:
                lowest_idx = idx

    if lowest_idx != -1:
        before = cleaned[:lowest_idx].rstrip()
        lines = before.split("\n")
        while lines and (lines[-1].strip().lower() in [
            "lasaad", "zoubair", "admin", "administrator", "dispatch",
            "operations", "team", "warm regards", "best regards",
            "sincerely", "cordialement", "salutations", ""
        ]):
            lines.pop()
        cleaned = "\n".join(lines).rstrip()

    return cleaned


def _clean_parsed_drafts(parsed):
    if not parsed or "drafts" not in parsed:
        return parsed
    for draft in parsed["drafts"]:
        if "body" in draft:
            draft["body"] = _clean_ai_draft_body(draft["body"])
    return parsed


def generate_smart_replies(conversation, messages, custom_instructions=None, timeout=35):
    """
    Generates structured AI smart replies for the given conversation.
    Returns a dict with 'language', 'summary', and 'drafts' list.
    """
    if not Config.GEMINI_API_KEY:
        return {
            "language": "EN",
            "summary": "AI API key not configured.",
            "drafts": _get_fallback_drafts(conversation),
        }

    # Format thread history
    thread_lines = []
    for m in messages:
        direction = "CLIENT (Inbound)" if m["direction"] == "inbound" else "DISPATCH (Outbound)"
        date_str = m.get("received_at") or m.get("created_at") or ""
        sender = m.get("from_addr") or ""
        body = (m.get("body_text") or "").strip()
        thread_lines.append(f"[{direction} | {date_str} | {sender}]\nSubject: {m.get('subject', '')}\n{body}\n---")

    thread_history = "\n".join(thread_lines) if thread_lines else "(No prior messages in thread)"

    client_name = conversation.get("client_name") or conversation.get("client_email", "").split("@")[0] or "Valued Client"
    prompt = PROMPT_TEMPLATE.format(
        client_name=client_name,
        client_email=conversation.get("client_email") or "",
        client_phone=conversation.get("client_phone") or "Not provided",
        origin=conversation.get("origin") or "Not specified",
        destination=conversation.get("destination") or "Not specified",
        trip_date=conversation.get("trip_date") or "Not specified",
        dispatcher_instructions=custom_instructions or "None provided. Generate standard comprehensive VIP options.",
        thread_history=thread_history[:7000],
    )

    url = GEMINI_ENDPOINT.format(model=Config.GEMINI_MODEL)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.25,
            "responseMimeType": "application/json",
        },
    }

    parsed = None
    for attempt in range(4):
        try:
            resp = requests.post(
                url,
                params={"key": Config.GEMINI_API_KEY},
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 429:
                wait_secs = 3 + attempt * 2
                logger.warning("Gemini rate limited (429); waiting %ds (attempt %d/4)", wait_secs, attempt + 1)
                time.sleep(wait_secs)
                continue
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(_strip_code_fences(text))
            break
        except Exception as exc:
            logger.warning("Gemini smart reply generation attempt %d failed: %s", attempt + 1, exc)
            if attempt == 3:
                return {
                    "language": "EN",
                    "summary": f"Could not generate AI reply: {exc}",
                    "drafts": _get_fallback_drafts(conversation),
                }
            time.sleep(2)

    if not parsed or "drafts" not in parsed:
        return {
            "language": "EN",
            "summary": "AI generation fallback.",
            "drafts": _get_fallback_drafts(conversation),
        }

    return _clean_parsed_drafts(parsed)


def _get_fallback_drafts(conversation):
    client_name = conversation.get("client_name") or "Valued Client"
    origin = conversation.get("origin") or "Pickup Location"
    destination = conversation.get("destination") or "Drop-off Location"
    trip_date = conversation.get("trip_date") or "as requested"

    return [
        {
            "id": "quote",
            "title": "💎 Luxury Quote",
            "subject": f"Re: Offre de transport VIP - {origin} vers {destination}",
            "body": f"Bonjour {client_name},\n\nNous avons le plaisir de vous proposer notre service de chauffeur VIP pour votre trajet :\n\n• Date : {trip_date}\n• Départ : {origin}\n• Destination : {destination}\n• Véhicule : Mercedes-Benz Classe S VIP / Classe V\n• Inclusions : Accueil personnalisé pancarte, suivi du vol en temps réel, rafraîchissements & Wi-Fi à bord.\n• Tarif : [Tarif à compléter] € HTVA\n\nN'hésitez pas à nous faire part de vos éventuelles précisions.",
        },
        {
            "id": "confirm",
            "title": "✅ VIP Confirmation",
            "subject": f"Re: Confirmation de réservation - Business Limousine",
            "body": f"Bonjour {client_name},\n\nNous vous confirmons avec plaisir votre réservation VIP :\n\n• Client : {client_name}\n• Trajet : {origin} ➔ {destination}\n• Date & Heure : {trip_date}\n• Véhicule : Mercedes-Benz VIP\n\nLes coordonnées de votre chauffeur vous seront communiquées avant la prise en charge.",
        },
        {
            "id": "details",
            "title": "❓ Request Details",
            "subject": f"Re: Précisions sur votre trajet - Business Limousine",
            "body": f"Bonjour {client_name},\n\nNous vous remercions pour votre demande. Afin de vous proposer la formule la plus adaptée, pourriez-vous nous préciser :\n\n• Horaires exacts et numéro de vol (si arrivée aéroport)\n• Nombre de passagers & bagages\n• Vos éventuelles demandes spécifiques\n\nDans l'attente de votre retour,",
        },
    ]
