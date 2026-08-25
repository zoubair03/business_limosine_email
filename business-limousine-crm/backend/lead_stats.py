"""Lead analytics over the mailbox.

Distinct from backend/analytics, which reports on *executed* bookings from the
Waynium export. This reports on the stage before that: enquiries arriving in the
inbox, how fast they are answered, and how many turn into confirmed work.

Definitions used throughout, stated because they decide what the numbers mean:

* A **lead** is one conversation. The router in `models` opens a new conversation
  per NEW_REQUEST, so one enquiry is one row rather than one thread per client.
* **First response** is the first outbound message at or after the first inbound
  one. Outbound mail that predates any inbound message is us starting the
  conversation and is not a response to anything.
* **Awaiting reply** means the newest message is inbound. That is what makes a
  lead actionable right now, and it is deliberately not the same as "no outbound
  message ever" — a client who replied after our answer is waiting again.
* Archived conversations are excluded everywhere. `OTHER`, `DOCCLE` and `EBOX`
  are utility mail, not enquiries, and are excluded from lead counts.
"""

LEAD_STATUSES = ("NEW_REQUEST", "DISCUSSION", "CONFIRMED", "CLOSED")

# One expression reused everywhere, so "which conversations count as leads" is
# defined once. Drift between the KPI and the chart underneath it is the classic
# way a dashboard starts lying.
_IS_LEAD = "c.is_archived = 0 AND c.status IN ('NEW_REQUEST','DISCUSSION','CONFIRMED','CLOSED')"

# Per-conversation message timings, used by several of the queries below.
_TIMINGS = """
WITH t AS (
  SELECT c.id,
         c.status,
         c.created_at,
         c.client_email,
         c.origin,
         c.destination,
         (SELECT MIN(m.received_at) FROM messages m
           WHERE m.conversation_id = c.id AND m.direction = 'inbound')  AS first_in,
         (SELECT MAX(m.received_at) FROM messages m
           WHERE m.conversation_id = c.id)                              AS last_at,
         (SELECT m.direction FROM messages m
           WHERE m.conversation_id = c.id
           ORDER BY m.received_at DESC, m.id DESC LIMIT 1)              AS last_dir
    FROM conversations c
   WHERE {is_lead}
),
r AS (
  SELECT t.*,
         (SELECT MIN(m.received_at) FROM messages m
           WHERE m.conversation_id = t.id
             AND m.direction = 'outbound'
             AND (t.first_in IS NULL OR m.received_at >= t.first_in))   AS first_out
    FROM t
)
""".replace("{is_lead}", _IS_LEAD)


def _window(days):
    """SQL fragment limiting to the last `days` days, or nothing for all-time."""
    if not days:
        return ""
    return f" AND c.created_at >= datetime('now', '-{int(days)} days')"


def lead_stats(conn, days=90):
    win = _window(days)

    # ---- headline numbers -------------------------------------------------
    kpi = conn.execute(f"""
        {_TIMINGS}
        SELECT
          COUNT(*)                                                        AS leads,
          SUM(CASE WHEN status = 'CONFIRMED' THEN 1 ELSE 0 END)           AS confirmed,
          SUM(CASE WHEN status IN ('NEW_REQUEST','DISCUSSION') THEN 1 ELSE 0 END) AS open_leads,
          SUM(CASE WHEN last_dir = 'inbound' THEN 1 ELSE 0 END)           AS awaiting_reply,
          SUM(CASE WHEN first_out IS NULL AND first_in IS NOT NULL THEN 1 ELSE 0 END) AS never_answered
        FROM r
    """.replace("WHERE " + _IS_LEAD, "WHERE " + _IS_LEAD + win)).fetchone()

    # Median rather than mean: one enquiry answered three weeks late would drag a
    # mean far away from what the office actually experiences day to day.
    resp = conn.execute(f"""
        {_TIMINGS}
        SELECT (julianday(first_out) - julianday(first_in)) * 24 AS hours
          FROM r
         WHERE first_in IS NOT NULL AND first_out IS NOT NULL
           AND julianday(first_out) >= julianday(first_in)
         ORDER BY hours
    """.replace("WHERE " + _IS_LEAD, "WHERE " + _IS_LEAD + win)).fetchall()
    hours = [row["hours"] for row in resp if row["hours"] is not None]
    median_response = _median(hours)

    leads = kpi["leads"] or 0
    confirmed = kpi["confirmed"] or 0

    kpis = {
        "leads": leads,
        "confirmed": confirmed,
        "open_leads": kpi["open_leads"] or 0,
        "awaiting_reply": kpi["awaiting_reply"] or 0,
        "never_answered": kpi["never_answered"] or 0,
        "conversion_pct": round(100.0 * confirmed / leads, 1) if leads else None,
        "median_response_hours": round(median_response, 1) if median_response is not None else None,
        "answered_count": len(hours),
    }

    return {
        "window_days": days,
        "kpis": kpis,
        "funnel": _funnel(conn, win),
        "volume": _volume(conn, win),
        "response_buckets": _response_buckets(hours),
        "by_hour": _by_hour(conn, win),
        "by_weekday": _by_weekday(conn, win),
        "top_routes": _top_routes(conn, win),
        "top_accounts": _top_accounts(conn, win),
        "ageing": _ageing(conn),
        "needs_attention": _needs_attention(conn),
    }


def _median(sorted_values):
    n = len(sorted_values)
    if not n:
        return None
    mid = n // 2
    if n % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def _funnel(conn, win):
    rows = conn.execute(f"""
        SELECT c.status, COUNT(*) AS n FROM conversations c
         WHERE {_IS_LEAD}{win}
         GROUP BY c.status
    """).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    return [{"status": s, "count": counts.get(s, 0)} for s in LEAD_STATUSES]


def _volume(conn, win):
    """New leads per ISO week, oldest first."""
    rows = conn.execute(f"""
        SELECT strftime('%Y-%W', c.created_at) AS week,
               MIN(date(c.created_at))         AS week_start,
               COUNT(*)                        AS n,
               SUM(CASE WHEN c.status = 'CONFIRMED' THEN 1 ELSE 0 END) AS confirmed
          FROM conversations c
         WHERE {_IS_LEAD}{win}
         GROUP BY week ORDER BY week
    """).fetchall()
    return [dict(r) for r in rows]


def _response_buckets(hours):
    """How fast we replied, in bands the office can act on."""
    bands = [("< 1h", 0, 1), ("1–4h", 1, 4), ("4–24h", 4, 24),
             ("1–3 days", 24, 72), ("> 3 days", 72, None)]
    out = []
    for label, lo, hi in bands:
        n = sum(1 for h in hours if h >= lo and (hi is None or h < hi))
        out.append({"band": label, "count": n})
    return out


def _by_hour(conn, win):
    rows = conn.execute(f"""
        SELECT CAST(strftime('%H', c.created_at) AS INTEGER) AS hour, COUNT(*) AS n
          FROM conversations c
         WHERE {_IS_LEAD}{win}
         GROUP BY hour
    """).fetchall()
    counts = {r["hour"]: r["n"] for r in rows}
    return [{"hour": h, "count": counts.get(h, 0)} for h in range(24)]


def _by_weekday(conn, win):
    # strftime('%w') is 0=Sunday; re-ordered to Monday-first for a working week.
    rows = conn.execute(f"""
        SELECT CAST(strftime('%w', c.created_at) AS INTEGER) AS dow, COUNT(*) AS n
          FROM conversations c
         WHERE {_IS_LEAD}{win}
         GROUP BY dow
    """).fetchall()
    counts = {r["dow"]: r["n"] for r in rows}
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    order = [1, 2, 3, 4, 5, 6, 0]
    return [{"day": names[i], "count": counts.get(d, 0)} for i, d in enumerate(order)]


def _top_routes(conn, win):
    rows = conn.execute(f"""
        SELECT c.origin, c.destination, COUNT(*) AS n,
               SUM(CASE WHEN c.status = 'CONFIRMED' THEN 1 ELSE 0 END) AS confirmed
          FROM conversations c
         WHERE {_IS_LEAD}{win}
           AND c.origin IS NOT NULL AND c.origin != ''
           AND c.destination IS NOT NULL AND c.destination != ''
         GROUP BY lower(c.origin), lower(c.destination)
         ORDER BY n DESC LIMIT 12
    """).fetchall()
    return [dict(r) for r in rows]


def _top_accounts(conn, win):
    """Ranked by enquiry volume, with how many each actually converted."""
    rows = conn.execute(f"""
        SELECT lower(c.client_email) AS email,
               MAX(c.client_name)    AS name,
               COUNT(*)              AS leads,
               SUM(CASE WHEN c.status = 'CONFIRMED' THEN 1 ELSE 0 END) AS confirmed,
               MAX(c.last_message_at) AS last_seen
          FROM conversations c
         WHERE {_IS_LEAD}{win} AND c.client_email IS NOT NULL AND c.client_email != ''
         GROUP BY email ORDER BY leads DESC, confirmed DESC LIMIT 12
    """).fetchall()
    return [dict(r) for r in rows]


def _ageing(conn):
    """How long open leads have been sitting. Deliberately ignores the window —
    a lead from four months ago that is still open is exactly the one to see."""
    rows = conn.execute(f"""
        SELECT CAST(julianday('now') - julianday(c.created_at) AS INTEGER) AS age_days
          FROM conversations c
         WHERE c.is_archived = 0 AND c.status IN ('NEW_REQUEST','DISCUSSION')
    """).fetchall()
    ages = [r["age_days"] or 0 for r in rows]
    bands = [("Today", 0, 1), ("1–3 days", 1, 4), ("4–7 days", 4, 8),
             ("1–4 weeks", 8, 29), ("> 1 month", 29, None)]
    return [{"band": label,
             "count": sum(1 for a in ages if a >= lo and (hi is None or a < hi))}
            for label, lo, hi in bands]


def _needs_attention(conn, limit=15):
    """Open leads whose newest message is inbound, longest-waiting first."""
    rows = conn.execute(f"""
        {_TIMINGS}
        SELECT id, status, client_email, origin, destination, last_at,
               ROUND((julianday('now') - julianday(last_at)) * 24, 1) AS waiting_hours
          FROM r
         WHERE last_dir = 'inbound'
           AND status IN ('NEW_REQUEST','DISCUSSION')
         ORDER BY last_at ASC
         LIMIT {int(limit)}
    """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["client_name"] = conn.execute(
            "SELECT client_name FROM conversations WHERE id = ?", (r["id"],)
        ).fetchone()["client_name"]
        out.append(d)
    return out
