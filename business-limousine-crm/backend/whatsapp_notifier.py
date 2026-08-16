"""
whatsapp_notifier.py
Sends instant dispatch notifications to dispatchers via:
1. Telegram Bot API (100% Free, Official, Instant 0.1s delivery with action buttons)
2. CallMeBot Free WhatsApp API
3. Meta WhatsApp Cloud API / Twilio
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional, Union
import urllib.parse
import requests

from config import Config

logger = logging.getLogger("whatsapp_notifier")


class WhatsAppNotifier:
    def __init__(self, provider: Optional[str] = None, dispatcher_numbers: Optional[List[Union[str, Dict]]] = None):
        # Telegram Bot configuration (Primary & Recommended)
        self.telegram_bot_token = Config.TELEGRAM_BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN")
        raw_tg_chats = Config.TELEGRAM_CHAT_IDS or os.getenv("TELEGRAM_CHAT_IDS", "")
        self.telegram_chat_ids = [c.strip() for c in raw_tg_chats.split(",") if c.strip()]

        # CallMeBot configuration
        self.callmebot_api_key = Config.CALLMEBOT_API_KEY or os.getenv("CALLMEBOT_API_KEY")
        self.callmebot_phone = Config.CALLMEBOT_PHONE or os.getenv("CALLMEBOT_PHONE")

        # Meta WhatsApp Cloud API credentials
        self.meta_phone_number_id = Config.META_WA_PHONE_NUMBER_ID or os.getenv("META_WA_PHONE_NUMBER_ID") or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        self.meta_access_token = Config.META_WA_ACCESS_TOKEN or os.getenv("META_WA_ACCESS_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN")

        # Twilio credentials (optional fallback)
        self.twilio_account_sid = Config.TWILIO_ACCOUNT_SID or os.getenv("TWILIO_ACCOUNT_SID")
        self.twilio_auth_token = Config.TWILIO_AUTH_TOKEN or os.getenv("TWILIO_AUTH_TOKEN")
        self.twilio_from = Config.TWILIO_WHATSAPP_FROM or os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

        # Set default active provider
        if provider:
            self.provider = provider.lower()
        elif self.telegram_bot_token:
            self.provider = "telegram"
        elif self.callmebot_api_key:
            self.provider = "callmebot"
        elif self.meta_phone_number_id and self.meta_access_token:
            self.provider = "meta"
        elif self.twilio_account_sid:
            self.provider = "twilio"
        else:
            self.provider = "telegram"

        if dispatcher_numbers is not None:
            self.dispatcher_numbers = dispatcher_numbers
        else:
            raw = Config.DISPATCHER_WHATSAPP_NUMBERS or os.getenv("DISPATCHER_WHATSAPP_NUMBERS", "")
            self.dispatcher_numbers = [n.strip() for n in raw.split(",") if n.strip()]

        # Merge Telegram chat IDs into dispatch list if empty
        if not self.dispatcher_numbers and self.telegram_chat_ids:
            self.dispatcher_numbers = [{"type": "telegram", "chat_id": cid, "label": f"Telegram ({cid})"} for cid in self.telegram_chat_ids]

    def parse_recipient(self, item: Union[str, Dict]) -> Dict[str, str]:
        """Parses a recipient entry which can be a dict {'type': 'telegram', 'chat_id': '...'} or string."""
        if isinstance(item, dict):
            if "chat_id" in item or item.get("type") == "telegram":
                return {"type": "telegram", "chat_id": str(item.get("chat_id") or "").strip(), "label": item.get("label", "Telegram Dispatcher")}
            phone = str(item.get("phone") or "").strip()
            apikey = str(item.get("apikey") or "").strip()
            return {"type": "whatsapp", "phone": phone, "apikey": apikey or self.callmebot_api_key or ""}
        
        s = str(item or "").strip()
        # If it's a numeric Telegram Chat ID
        if s.isdigit() and len(s) >= 8 and not s.startswith("+"):
            return {"type": "telegram", "chat_id": s, "label": f"Telegram ({s})"}
        if ":" in s:
            parts = s.split(":", 1)
            return {"type": "whatsapp", "phone": parts[0].strip(), "apikey": parts[1].strip()}
        
        return {"type": "whatsapp", "phone": s, "apikey": self.callmebot_api_key or ""}

    @property
    def is_configured(self) -> bool:
        if self.provider == "telegram" or bool(self.telegram_bot_token):
            return bool(self.telegram_bot_token)
        elif self.provider == "callmebot":
            return bool(self.callmebot_api_key)
        elif self.provider == "meta":
            return bool(self.meta_phone_number_id and self.meta_access_token)
        elif self.provider == "twilio":
            return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from)
        return False

    def get_status(self) -> Dict:
        parsed_dispatchers = [self.parse_recipient(d) for d in self.dispatcher_numbers]
        
        masked_tg_token = ""
        if self.telegram_bot_token and len(self.telegram_bot_token) > 10:
            masked_tg_token = self.telegram_bot_token[:6] + "••••" + self.telegram_bot_token[-4:]

        return {
            "provider": self.provider,
            "configured": self.is_configured,
            "telegram": {
                "configured": bool(self.telegram_bot_token),
                "bot_token_masked": masked_tg_token,
                "bot_username": "BL_Dispatch_Bot",
                "chat_ids": self.telegram_chat_ids,
            },
            "callmebot": {
                "configured": bool(self.callmebot_api_key),
            },
            "dispatcher_count": len(self.dispatcher_numbers),
            "dispatcher_numbers": self.dispatcher_numbers,
        }

    def _clean_phone_number(self, phone: str) -> str:
        clean = re.sub(r"[^\d]", "", str(phone or ""))
        return clean

    def format_alert_message(self, client_name: str, client_email: str, client_phone: str = None,
                             origin: str = None, destination: str = None, trip_date: str = None,
                             minutes_waiting: int = 10, conversation_id: int = None) -> str:
        route_str = f"{origin} ➔ {destination}" if (origin and destination) else (origin or destination or "Route non spécifiée")
        date_str = trip_date or "Date à convenir"
        phone_str = f"\n📞 *Tél :* {client_phone}" if client_phone else ""

        msg = (
            f"🚨 *BUSINESS LIMOUSINE — DEMANDE EN ATTENTE*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Client :* {client_name}\n"
            f"✉️ *Email :* {client_email}{phone_str}\n"
            f"📍 *Trajet :* {route_str}\n"
            f"📅 *Date :* {date_str}\n"
            f"⏱️ *En attente depuis :* {minutes_waiting} minutes sans réponse\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👉 _Ouvrez la console Dispatch pour traiter la réservation._"
        )
        return msg

    def _send_telegram_message(self, chat_id: str, body: str) -> Dict:
        if not self.telegram_bot_token:
            return {"status": "failed", "error": "TELEGRAM_BOT_TOKEN non configuré", "target": chat_id}

        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": body,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and data.get("ok"):
                msg_id = data.get("result", {}).get("message_id")
                logger.info(f"Telegram alert sent successfully to {chat_id} (Msg ID: {msg_id})")
                return {"status": "sent", "id": msg_id, "target": chat_id, "provider": "telegram"}
            else:
                desc = data.get("description", f"HTTP {resp.status_code}")
                logger.error(f"Telegram API error for {chat_id}: {desc}")
                return {"status": "failed", "error": desc, "target": chat_id, "provider": "telegram"}
        except Exception as exc:
            logger.exception(f"HTTP request to Telegram failed for {chat_id}")
            return {"status": "failed", "error": str(exc), "target": chat_id, "provider": "telegram"}

    def _send_callmebot_message(self, phone: str, apikey: str, body: str) -> Dict:
        clean_num = self._clean_phone_number(phone)
        if not clean_num:
            return {"status": "failed", "error": "Numéro de téléphone invalide", "number": phone}
        if not apikey:
            return {"status": "failed", "error": f"Clé API CallMeBot manquante pour {phone}", "number": phone}

        encoded_text = urllib.parse.quote(body)
        url = f"https://api.callmebot.com/whatsapp.php?phone={clean_num}&text={encoded_text}&apikey={apikey}"

        try:
            resp = requests.get(url, timeout=15)
            text_resp = resp.text.strip()
            if resp.status_code == 200:
                return {"status": "sent", "number": clean_num, "provider": "callmebot"}
            else:
                return {"status": "failed", "error": text_resp or f"HTTP {resp.status_code}", "number": clean_num}
        except Exception as exc:
            return {"status": "failed", "error": str(exc), "number": clean_num}

    def send_pending_alert(self, client_name: str, client_email: str, client_phone: str = None,
                           origin: str = None, destination: str = None, trip_date: str = None,
                           minutes_waiting: int = 10, conversation_id: int = None,
                           numbers: Optional[List[Union[str, Dict]]] = None) -> Dict:
        body = self.format_alert_message(
            client_name=client_name or "Client Inconnu",
            client_email=client_email or "Non spécifié",
            client_phone=client_phone,
            origin=origin,
            destination=destination,
            trip_date=trip_date,
            minutes_waiting=minutes_waiting,
            conversation_id=conversation_id,
        )

        results = []
        sent_targets = set()

        # 1. Send via Telegram to all configured chat IDs
        if self.telegram_bot_token and self.telegram_chat_ids:
            for cid in self.telegram_chat_ids:
                clean_cid = str(cid).strip()
                if clean_cid and clean_cid not in sent_targets:
                    sent_targets.add(clean_cid)
                    res = self._send_telegram_message(clean_cid, body)
                    results.append(res)

        # 2. Also send to any specific items in numbers (deduplicated)
        target_list = numbers if numbers is not None else self.dispatcher_numbers
        for item in target_list:
            recip = self.parse_recipient(item)
            if recip.get("type") == "telegram" and recip.get("chat_id"):
                cid = str(recip.get("chat_id")).strip()
                if cid and cid not in sent_targets:
                    sent_targets.add(cid)
                    results.append(self._send_telegram_message(cid, body))
            elif recip.get("type") == "whatsapp" and recip.get("phone") and recip.get("apikey"):
                phone = str(recip.get("phone")).strip()
                if phone and phone not in sent_targets:
                    sent_targets.add(phone)
                    results.append(self._send_callmebot_message(phone, recip.get("apikey"), body))

        sent_count = sum(1 for r in results if r.get("status") == "sent")
        return {
            "sent": sent_count > 0,
            "count": sent_count,
            "total": len(results),
            "results": results,
        }

    def send_test_alert(self, to_target: str, apikey: Optional[str] = None) -> Dict:
        test_body = (
            f"✅ *BUSINESS LIMOUSINE — TEST D'ALERTE DISPATCH*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 *Canal d'alerte opérationnel en temps réel !*\n"
            f"Vous recevrez ici instantanément les nouvelles réservations VIP en attente."
        )

        clean_target = str(to_target or "").strip()
        
        # If target is Telegram Chat ID or empty (uses default chat ID 8000019066)
        if not clean_target or (clean_target.isdigit() and len(clean_target) >= 6 and not clean_target.startswith("+")):
            cid = clean_target or (self.telegram_chat_ids[0] if self.telegram_chat_ids else "8000019066")
            res = self._send_telegram_message(cid, test_body)
            if res.get("status") == "sent":
                return {
                    "success": True,
                    "provider": "telegram",
                    "id": res.get("id"),
                    "to": cid,
                    "message": f"Alerte Telegram envoyée avec succès à l'ID {cid} (Bot: @BL_Dispatch_Bot) !",
                }
            else:
                return {
                    "success": False,
                    "provider": "telegram",
                    "error": res.get("error", "Échec d'envoi Telegram"),
                    "to": cid,
                }
        
        # WhatsApp CallMeBot
        recip = self.parse_recipient(clean_target)
        phone = recip.get("phone")
        key = apikey or recip.get("apikey") or self.callmebot_api_key
        res = self._send_callmebot_message(phone, key, test_body)
        if res.get("status") == "sent":
            return {
                "success": True,
                "provider": "callmebot",
                "to": phone,
                "message": f"Message WhatsApp envoyé avec succès via CallMeBot à {phone} !",
            }
        else:
            return {
                "success": False,
                "provider": "callmebot",
                "error": res.get("error", "Échec d'envoi CallMeBot"),
                "to": phone,
            }




