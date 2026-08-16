"""
Reclassifies all existing messages in the database using the updated Gemini model.
Run with:
    .\\venv\\Scripts\\python reclassify.py
"""
import time
from database import db_session
from ai_classifier import classify_email
from email_parser import extract_form_fields
import models

def main():
    print("Reclassifying existing conversations in database...")
    with db_session() as conn:
        conversations = conn.execute("SELECT * FROM conversations").fetchall()
        for convo in conversations:
            cid = convo["id"]
            messages = conn.execute("SELECT * FROM messages WHERE conversation_id = ? AND direction = 'inbound'", (cid,)).fetchall()
            if not messages:
                continue
            
            # Reclassify first inbound message
            first_msg = messages[0]
            print(f"\nProcessing Conversation ID {cid} ({convo['client_email']})...")
            ai_result = classify_email(
                subject=first_msg["subject"],
                body=first_msg["body_text"],
                from_name=convo["client_name"] or "",
                from_addr=convo["client_email"] or "",
            )
            print(f"-> Classified as {ai_result['category']} (confidence: {ai_result['confidence']})")
            if ai_result.get("origin") or ai_result.get("destination"):
                print(f"-> Route: {ai_result.get('origin')} -> {ai_result.get('destination')}")
            
            # Extract form fields fallback
            form_fields = extract_form_fields(first_msg["body_text"])
            for key in ("client_name", "client_phone", "trip_date", "origin", "destination"):
                form_key = {"client_name": "name", "client_phone": "phone"}.get(key, key)
                if not ai_result.get(key) and form_fields.get(form_key):
                    ai_result[key] = form_fields[form_key]

            # Update conversation
            models.update_conversation_fields(
                conn,
                cid,
                client_name=ai_result.get("client_name") or convo["client_name"],
                client_phone=ai_result.get("client_phone"),
                status=ai_result["category"],
                trip_date=ai_result.get("trip_date"),
                origin=ai_result.get("origin"),
                destination=ai_result.get("destination"),
            )
            time.sleep(0.5)

    print("\nAll conversations reclassified successfully!")

if __name__ == "__main__":
    main()
