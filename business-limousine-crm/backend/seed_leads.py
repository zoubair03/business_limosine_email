"""Generate a realistic lead history, for demoing and testing the lead dashboard.

The three seeded demo conversations are not enough to tell whether the funnel,
response-time and ageing figures are right. This produces a few months of
plausible enquiry traffic with known properties, so the dashboard can be checked
against numbers computed independently.

    python seed_leads.py            # create ~180 leads over 5 months
    python seed_leads.py clean      # remove them again

Everything it writes is marked with the `@leadseed.example` domain, so cleanup
never touches real mail.

Note: the frontend inbox tests assume the three seeded demo conversations, so run
`seed_leads.py clean` before them — 180 extra rows changes every count they
assert on.
"""
import random
import sys
from datetime import datetime, timedelta, timezone

from database import db_session
import models

sys.stdout.reconfigure(encoding="utf-8")

MARK = "leadseed.example"
N_LEADS = 180
MONTHS_BACK = 5
rng = random.Random(11)

COMPANIES = ["Meridian Group", "Northgate Partners", "Bluecrest bvba", "Orbis Pharma",
             "Kestrel Events", "Aldermont Capital", "Vantage Media", "Silverline srl",
             "Ravel Consulting", "Ironwood Logistics", "Castellan Trading", "Hallmark Labs"]
FIRST = ["Alex", "Bilal", "Chloe", "Dario", "Elena", "Farid", "Greta", "Hugo", "Ines", "Jonas"]
LAST = ["Aerts", "Bogaert", "Claes", "Declercq", "Fontaine", "Janssens", "Maes", "Peeters"]

# A pool of ~28 contacts across the companies: enough repetition that the
# per-account aggregation has something real to add up.
_r = random.Random(5)
ACCOUNTS = [(c, f"{_r.choice(FIRST)} {_r.choice(LAST)}")
            for c in COMPANIES for _ in range(_r.randrange(2, 4))]

ROUTES = [
    ("Brussels Airport (BRU)", "Brussels city centre", 0.30),
    ("Brussels Midi station", "Brussels Airport (BRU)", 0.14),
    ("Brussels city centre", "Antwerp", 0.12),
    ("Brussels Airport (BRU)", "Ghent", 0.09),
    ("Charleroi Airport (CRL)", "Brussels city centre", 0.08),
    ("Brussels city centre", "Amsterdam", 0.07),
    ("Brussels city centre", "Paris", 0.06),
    ("Brussels Airport (BRU)", "Leuven", 0.06),
    ("Brussels city centre", "Luxembourg", 0.04),
    ("Brussels Airport (BRU)", "Bruges", 0.04),
]

SUBJECTS = ["Airport transfer request", "Quote for a roadshow", "Booking enquiry",
            "Transfer for 4 passengers", "Chauffeur for a company visit",
            "VIP transfer request", "Request for a day at disposal"]


def _pick_route():
    r, acc = rng.random(), 0.0
    for o, d, w in ROUTES:
        acc += w
        if r <= acc:
            return o, d
    return ROUTES[0][0], ROUTES[0][1]


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def seed():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=MONTHS_BACK * 30)
    created = 0
    stats = {"CONFIRMED": 0, "CLOSED": 0, "NEW_REQUEST": 0, "DISCUSSION": 0,
             "answered": 0, "awaiting": 0}

    with db_session() as conn:
        for i in range(N_LEADS):
            # Enquiries cluster in office hours on weekdays, with a slow drift
            # upward over the period so the volume chart has a real shape.
            day_offset = int((i / N_LEADS) ** 0.85 * MONTHS_BACK * 30)
            when = start + timedelta(days=day_offset)
            # Weekends are quiet, but pushing every weekend arrival to Monday
            # piled ~40% of all traffic onto one bar. Spread the overflow across
            # the following week instead.
            if when.weekday() >= 5 and rng.random() < 0.75:
                when += timedelta(days=(7 - when.weekday()) + rng.randrange(0, 5))
            hour = rng.choices(range(24),
                               weights=[1,1,1,1,1,2,4,9,14,16,15,12,10,12,14,13,11,8,5,3,2,2,1,1])[0]
            when = when.replace(hour=hour, minute=rng.randrange(60), second=0, microsecond=0)
            if when > now:
                when = now - timedelta(hours=rng.randrange(1, 48))

            # Draw from a fixed pool of contacts so accounts repeat, as a real
            # book does — a unique address per enquiry would make the
            # "most active accounts" table meaningless.
            company, contact = rng.choice(ACCOUNTS)
            email = f"{contact.lower().replace(' ', '.')}@{MARK}"
            name = contact
            origin, dest = _pick_route()

            # Outcome mix: most enquiries are answered, a good share convert.
            roll = rng.random()
            if roll < 0.34:
                status = "CONFIRMED"
            elif roll < 0.52:
                status = "CLOSED"
            elif roll < 0.80:
                status = "DISCUSSION"
            else:
                status = "NEW_REQUEST"

            cid = models.create_conversation(conn, email, client_name=name, status=status)
            models.update_conversation_fields(
                conn, cid, client_phone=f"+32 4{rng.randrange(10,99)} {rng.randrange(100000,999999)}",
                origin=origin, destination=dest,
                trip_date=_iso(when + timedelta(days=rng.randrange(2, 30))))
            conn.execute("UPDATE conversations SET created_at = ? WHERE id = ?", (_iso(when), cid))

            subject = f"{rng.choice(SUBJECTS)} — {company}"
            models.add_message(
                conn, cid, "inbound",
                subject=subject,
                body_text=f"Hello,\n\nWe need a transfer from {origin} to {dest}.\n\nBest regards,\n{name}\n{company}",
                body_html=None, from_addr=email, to_addr="contact@business-limousine.com",
                message_id=f"<seed-{i}-in@{MARK}>", received_at=_iso(when))

            # Most get a reply; a minority are still waiting, which is what the
            # "needs attention" list is meant to surface.
            answered = rng.random() < 0.82
            if answered:
                lag = rng.choices([0.4, 2.0, 8.0, 30.0, 90.0],
                                  weights=[34, 30, 20, 11, 5])[0] * rng.uniform(0.6, 1.5)
                reply_at = when + timedelta(hours=lag)
                if reply_at < now:
                    models.add_message(
                        conn, cid, "outbound",
                        # A real reply keeps the thread subject. Picking a fresh
                        # random one produced mail no client would ever send, and
                        # made the thread view show a subject line on every reply.
                        subject=f"Re: {subject}",
                        body_text="Thank you for your enquiry. Please find our quote attached.",
                        body_html=None, from_addr="contact@business-limousine.com", to_addr=email,
                        message_id=f"<seed-{i}-out@{MARK}>", received_at=_iso(reply_at))
                    stats["answered"] += 1
                    # A client follow-up after our reply puts it back in the queue.
                    if rng.random() < 0.22:
                        follow = reply_at + timedelta(hours=rng.uniform(1, 60))
                        if follow < now:
                            models.add_message(
                                conn, cid, "inbound", subject=f"Re: {subject}",
                                body_text="Thanks — could you confirm the vehicle type?",
                                body_html=None, from_addr=email, to_addr="contact@business-limousine.com",
                                message_id=f"<seed-{i}-in2@{MARK}>", received_at=_iso(follow))

            conn.execute("UPDATE conversations SET status = ? WHERE id = ?", (status, cid))
            stats[status] += 1
            created += 1

    print(f"seeded {created} leads across {MONTHS_BACK} months")
    print("  status mix:", {k: v for k, v in stats.items() if k in
                            ("NEW_REQUEST", "DISCUSSION", "CONFIRMED", "CLOSED")})
    print(f"  answered at least once: {stats['answered']}")


def clean():
    with db_session() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM conversations WHERE client_email LIKE ?",
                         (f"%@{MARK}",)).fetchone()["c"]
        conn.execute("DELETE FROM messages WHERE conversation_id IN "
                     "(SELECT id FROM conversations WHERE client_email LIKE ?)", (f"%@{MARK}",))
        conn.execute("DELETE FROM conversations WHERE client_email LIKE ?", (f"%@{MARK}",))
    print(f"removed {n} seeded leads")


if __name__ == "__main__":
    clean() if sys.argv[1:] == ["clean"] else seed()
