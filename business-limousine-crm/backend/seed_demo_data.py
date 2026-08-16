"""
Seed script to insert realistic demo data into crm.db for previewing and testing the CRM dashboard.
Run with:
    .\\venv\\Scripts\\python seed_demo_data.py
"""
import sys
from pathlib import Path

from config import Config
from database import db_session, init_db
import models

def seed():
    init_db()
    with db_session() as conn:
        # Check if already seeded
        existing = models.list_conversations(conn)
        if existing:
            print(f"Database already contains {len(existing)} conversation(s). Skipping seed.")
            return

        print("Seeding sample conversations...")

        # 1. VIP Airport Transfer (NEW_REQUEST)
        cid1, _ = models.get_or_create_conversation(
            conn,
            client_email="sophia.laurent@luxury-corp.ch",
            client_name="Sophia Laurent",
            status="NEW_REQUEST",
        )
        models.update_conversation_fields(
            conn,
            cid1,
            client_phone="+41 79 123 45 67",
            trip_date="Aug 22, 2026 at 14:30",
            origin="Geneva Airport (GVA) Terminal 1",
            destination="Hotel President Wilson, Geneva",
        )
        models.add_message(
            conn,
            conversation_id=cid1,
            direction="inbound",
            subject="VIP Transfer Request - GVA to Hotel President Wilson",
            body_text=(
                "Hello Business Limousine team,\n\n"
                "I would like to book a Mercedes S-Class transfer from Geneva Airport (GVA) "
                "to Hotel President Wilson on August 22, 2026 at 14:30.\n"
                "We will have 2 passengers with 3 pieces of luggage.\n\n"
                "Please confirm availability and pricing.\n\n"
                "Best regards,\nSophia Laurent\n+41 79 123 45 67"
            ),
            body_html=None,
            from_addr="sophia.laurent@luxury-corp.ch",
            to_addr="contact@business-limousine.com",
            message_id="<demo-msg-101@luxury-corp.ch>",
            ai_category="NEW_REQUEST",
            ai_confidence=0.98,
            received_at="2026-08-15T18:20:00Z",
        )
        models.add_note(
            conn,
            cid1,
            note_text="High priority corporate client (Luxury Corp CH). Requested Mercedes S-Class.",
            author="System",
        )

        # 2. Executive Roadshow (DISCUSSION)
        cid2, _ = models.get_or_create_conversation(
            conn,
            client_email="marcus.vance@vance-holdings.com",
            client_name="Marcus Vance",
            status="DISCUSSION",
        )
        models.update_conversation_fields(
            conn,
            cid2,
            client_phone="+44 20 7946 0912",
            trip_date="Aug 25, 2026 full-day (08:00 - 20:00)",
            origin="Four Seasons Hotel des Bergues",
            destination="Lausanne Palace & back to Geneva",
        )
        models.add_message(
            conn,
            conversation_id=cid2,
            direction="inbound",
            subject="Full-day roadshow chauffeur inquiry",
            body_text=(
                "Good afternoon,\n\n"
                "We require a dedicated chauffeur with a Mercedes V-Class for a full-day executive roadshow "
                "on August 25. Pickup at 08:00 at Four Seasons Hotel des Bergues, traveling to meetings in Lausanne, "
                "and returning by 20:00.\n\n"
                "Kind regards,\nMarcus Vance"
            ),
            body_html=None,
            from_addr="marcus.vance@vance-holdings.com",
            to_addr="contact@business-limousine.com",
            message_id="<demo-msg-201@vance-holdings.com>",
            ai_category="NEW_REQUEST",
            ai_confidence=0.95,
            received_at="2026-08-15T14:10:00Z",
        )
        models.add_message(
            conn,
            conversation_id=cid2,
            direction="outbound",
            subject="Re: Full-day roadshow chauffeur inquiry",
            body_text=(
                "Dear Mr. Vance,\n\n"
                "Thank you for reaching out to Business Limousine.\n"
                "We have a premium Mercedes V-Class Extra Long available for your August 25 roadshow. "
                "The full-day 12-hour package is CHF 1,850 all-inclusive of fuel, tolls, and onboard refreshments.\n\n"
                "Please let us know if you would like us to reserve this vehicle for you.\n\n"
                "Warm regards,\nBusiness Limousine Dispatch"
            ),
            body_html=None,
            from_addr="contact@business-limousine.com",
            to_addr="marcus.vance@vance-holdings.com",
            message_id="<demo-reply-202@business-limousine.com>",
            in_reply_to="<demo-msg-201@vance-holdings.com>",
            ai_category="DISCUSSION",
            ai_confidence=0.92,
            received_at="2026-08-15T15:00:00Z",
        )
        models.add_message(
            conn,
            conversation_id=cid2,
            direction="inbound",
            subject="Re: Full-day roadshow chauffeur inquiry",
            body_text=(
                "Thank you for the quick quote. Could we also add onboard Wi-Fi and still water bottles for 4 passengers?"
            ),
            body_html=None,
            from_addr="marcus.vance@vance-holdings.com",
            to_addr="contact@business-limousine.com",
            message_id="<demo-msg-203@vance-holdings.com>",
            in_reply_to="<demo-reply-202@business-limousine.com>",
            ai_category="DISCUSSION",
            ai_confidence=0.97,
            received_at="2026-08-15T16:30:00Z",
        )

        # 3. Confirmed Booking (CONFIRMED)
        cid3, _ = models.get_or_create_conversation(
            conn,
            client_email="elena.rossi@milan-design.it",
            client_name="Elena Rossi",
            status="CONFIRMED",
        )
        models.update_conversation_fields(
            conn,
            cid3,
            client_phone="+39 02 8899 1122",
            trip_date="Aug 21, 2026 at 09:00",
            origin="Zurich HB",
            destination="Klosters / Davos",
        )
        models.add_message(
            conn,
            conversation_id=cid3,
            direction="inbound",
            subject="Booking Confirmation: Zurich to Klosters",
            body_text=(
                "Hi Dispatch,\n\n"
                "We accept the quote of CHF 950 for the Zurich HB to Klosters transfer on Aug 21 at 09:00.\n"
                "Please assign our usual driver if available.\n\n"
                "Best,\nElena"
            ),
            body_html=None,
            from_addr="elena.rossi@milan-design.it",
            to_addr="contact@business-limousine.com",
            message_id="<demo-msg-301@milan-design.it>",
            ai_category="DISCUSSION",
            ai_confidence=0.99,
            received_at="2026-08-15T11:00:00Z",
        )
        models.add_note(
            conn,
            cid3,
            note_text="Assigned driver: Jean-Pierre (Vehicle: S-Class 4Matic). Payment guaranteed on corporate card.",
            author="Dispatch Admin",
        )

    print("Demo seed completed successfully!")

if __name__ == "__main__":
    seed()
