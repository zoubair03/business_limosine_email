"""
Diagnostic tool to test your .env configuration for:
1. Google Gemini API
2. IMAP (Inbound email)
3. SMTP (Outbound email)

Run with:
    .\\venv\\Scripts\\python test_connection.py
"""
import imaplib
import smtplib
import sys
from config import Config
from ai_classifier import classify_email

def print_status(component, success, message=""):
    badge = "[PASS]" if success else "[FAIL]"
    print(f"{badge} {component}: {message}")

def test_gemini():
    print("\n--- Testing Google Gemini AI ---")
    if not Config.GEMINI_API_KEY or Config.GEMINI_API_KEY == "change-me":
        print_status("Gemini API", False, "GEMINI_API_KEY is not set in backend/.env")
        return False
    
    print(f"Model: {Config.GEMINI_MODEL}")
    try:
        res = classify_email("Test subject", "I need a ride tomorrow from Airport to Downtown Hotel.")
        if res.get("category") in ("NEW_REQUEST", "DISCUSSION", "OTHER"):
            print_status("Gemini API", True, f"Successfully classified test message as {res.get('category')} (confidence: {res.get('confidence')})")
            return True
        else:
            print_status("Gemini API", False, f"Unexpected response structure: {res}")
            return False
    except Exception as exc:
        print_status("Gemini API", False, f"Error: {exc}")
        return False

def test_imap():
    print("\n--- Testing IMAP (Incoming Mail) ---")
    if not Config.IMAP_USER or Config.IMAP_USER == "contact@business-limousine.com" or Config.IMAP_PASSWORD == "change-me":
        print_status("IMAP", False, "IMAP_USER or IMAP_PASSWORD is not configured in backend/.env")
        return False
    
    print(f"Connecting to {Config.IMAP_HOST}:{Config.IMAP_PORT} as {Config.IMAP_USER}...")
    try:
        conn = imaplib.IMAP4_SSL(Config.IMAP_HOST, Config.IMAP_PORT, timeout=15)
        conn.login(Config.IMAP_USER, Config.IMAP_PASSWORD)
        status, counts = conn.select(Config.IMAP_FOLDER)
        if status == "OK":
            total_msgs = counts[0].decode() if counts and counts[0] else "0"
            print_status("IMAP", True, f"Connected to folder '{Config.IMAP_FOLDER}' ({total_msgs} messages in mailbox)")
            conn.logout()
            return True
        else:
            print_status("IMAP", False, f"Could not select folder '{Config.IMAP_FOLDER}'")
            conn.logout()
            return False
    except Exception as exc:
        print_status("IMAP", False, f"Connection failed: {exc}")
        return False

def test_smtp():
    print("\n--- Testing SMTP (Outgoing Mail) ---")
    if not Config.SMTP_USER or Config.SMTP_USER == "contact@business-limousine.com" or Config.SMTP_PASSWORD == "change-me":
        print_status("SMTP", False, "SMTP_USER or SMTP_PASSWORD is not configured in backend/.env")
        return False
    
    print(f"Connecting to {Config.SMTP_HOST}:{Config.SMTP_PORT} as {Config.SMTP_USER}...")
    try:
        if Config.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15) as server:
                server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
        else:
            with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
        print_status("SMTP", True, f"Successfully authenticated with {Config.SMTP_HOST}")
        return True
    except Exception as exc:
        print_status("SMTP", False, f"Authentication failed: {exc}")
        return False

def main():
    print("=========================================")
    print(" Business Limousine CRM - Connection Test")
    print("=========================================")
    g_ok = test_gemini()
    i_ok = test_imap()
    s_ok = test_smtp()
    
    print("\n=========================================")
    if g_ok and i_ok and s_ok:
        print("ALL CHECKS PASSED! Your CRM is ready for live inbox syncing.")
    else:
        print("Some checks did not pass. Check the error messages above and update backend/.env.")
    print("=========================================")

if __name__ == "__main__":
    main()
