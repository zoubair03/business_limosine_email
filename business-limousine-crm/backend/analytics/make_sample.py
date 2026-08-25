"""Build dashboard_data.sample.json — a shape-identical, fully fabricated copy of
dashboard_data.json.

The real file carries named clients with revenue, chauffeur names (some with phone
numbers attached), passenger names and pickup addresses. This repository is public,
so the real file is gitignored and this fabricated stand-in is what ships, letting a
fresh clone start the app and see a populated UI without exposing anybody.

Every identity is replaced deterministically — the same real name always maps to the
same fake one — so cross-references between blocks (a driver in `top_drivers` and the
same driver in `driver_hours.drivers`) stay consistent. Numbers are jittered so the
sample doesn't leak real revenue either.

    python make_sample.py
"""
import json
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "dashboard_data.json")
DST = os.path.join(HERE, "dashboard_data.sample.json")

rng = random.Random(7)

# Fabricated pools. Deliberately obvious inventions — nobody should mistake the
# sample for production data.
FIRST = ["Alex", "Bilal", "Chloe", "Dario", "Elena", "Farid", "Greta", "Hugo", "Ines",
         "Jonas", "Karim", "Lena", "Marco", "Nadia", "Omar", "Petra", "Rachid", "Sofia",
         "Timo", "Vera", "Walid", "Yara", "Zoran", "Anouk", "Bram", "Celine", "Dries"]
LAST = ["Aerts", "Bogaert", "Claes", "Declercq", "Everaert", "Fontaine", "Goossens",
        "Hendrickx", "Ivanov", "Janssens", "Kowalski", "Lemaire", "Maes", "Nowak",
        "Oliveira", "Peeters", "Quintero", "Rousseau", "Segers", "Thys", "Vermeulen",
        "Willems", "Xhonneux", "Yilmaz", "Zegers"]
COMPANY_A = ["Meridian", "Northgate", "Bluecrest", "Orbis", "Kestrel", "Aldermont",
             "Vantage", "Silverline", "Ravel", "Ironwood", "Castellan", "Hallmark",
             "Brightpath", "Concord", "Danube", "Eastwick", "Fairmount", "Grenadier"]
COMPANY_B = ["Group", "Partners", "bvba", "srl", "Consulting", "Industries", "Pharma",
             "Logistics", "Capital", "Events", "Media", "Solutions", "Trading", "Labs"]
STREET = ["Rue des Tilleuls", "Avenue du Parc", "Chaussee de Mons", "Kerkstraat",
          "Boulevard Central", "Lange Gasthuisstraat", "Rue Haute", "Stationsplein",
          "Avenue Louise", "Nieuwstraat", "Rue du Marche", "Bergstraat"]
PLACE = ["BRUSSELS AIRPORT", "GARE DU MIDI", "ANTWERPEN CENTRAAL", "HOTEL METROPOLE",
         "EXPO CENTRE", "CITY CENTRE", "GHENT SINT-PIETERS", "LEUVEN CAMPUS",
         "CHARLEROI AIRPORT", "WATERLOO OFFICE PARK"]

_people, _companies, _routes = {}, {}, {}


def fake_person(real):
    """Stable fake full name for a real one. Blank stays blank — an empty driver or
    passenger field is meaningful (the export genuinely has none) and must survive."""
    if not real or not str(real).strip():
        return real
    key = str(real)
    if key not in _people:
        n = len(_people)
        _people[key] = f"{FIRST[n % len(FIRST)]} {LAST[(n * 7 + 3) % len(LAST)]}"
    return _people[key]


def fake_company(real):
    if not real or not str(real).strip():
        return real
    key = str(real)
    if key not in _companies:
        n = len(_companies)
        _companies[key] = f"{COMPANY_A[n % len(COMPANY_A)]} {COMPANY_B[(n * 5 + 2) % len(COMPANY_B)]}"
    return _companies[key]


def fake_route(real):
    """Routes read 'Pickup address -> DESTINATION'; both halves are real locations."""
    if not real or not str(real).strip():
        return real
    key = str(real)
    if key not in _routes:
        n = len(_routes)
        a = f"{STREET[n % len(STREET)]} {rng.randint(1, 180)}"
        b = PLACE[(n * 3 + 1) % len(PLACE)]
        _routes[key] = f"{a} → {b}"
    return _routes[key]


def jitter(v, pct=0.18):
    """Nudge a number so the sample doesn't carry real revenue, keeping magnitude."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    out = v * (1 + rng.uniform(-pct, pct))
    return int(round(out)) if isinstance(v, int) else round(out, 2)


def collect_identities(real):
    """Every string in the real file that names a person, a company or an address."""
    out = set()
    for row in real.get("top_clients", []):
        out.add(row.get("Client"))
    for row in real.get("top_trips", []):
        out.add(row.get("Client"))
    for row in real.get("top_partners", []):
        out.add(row.get("Partner"))
    for row in real.get("top_drivers", []):
        out.add(row.get("Driver"))
        out.add(row.get("affiliation"))
    out.update(real.get("driver_hours", {}).get("drivers", []))
    for ride in real.get("reviews", {}).get("rides", []):
        for k in ("client", "passenger", "driver", "route"):
            out.add(ride.get(k))
    for rows in (real.get("quote_engine", {}).get("comparables") or {}).values():
        for r in rows:
            out.add(r.get("route"))
    # Short strings produce false positives against ordinary words; identities are longer.
    return {str(s) for s in out if s and len(str(s).strip()) > 3}


def find_leaks(real, sample):
    """Walk the sample and report any real identity that survived, with its path.

    This is the guard that makes the sample safe to commit: if a new field carrying a
    name is ever added upstream, this fails the build instead of quietly publishing it.
    """
    identities = collect_identities(real)
    hits = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            for ident in identities:
                if ident in node:
                    hits.append((path.lstrip("."), node))
                    return

    walk(sample, "")
    return hits


def main():
    if not os.path.exists(SRC):
        raise SystemExit(f"No {SRC} to build a sample from — run the pricing pipeline first.")
    d = json.load(open(SRC, encoding="utf-8"))

    # --- named-entity columns in the ranking tables ---
    for row in d.get("top_clients", []):
        row["Client"] = fake_company(row.get("Client"))
    for row in d.get("top_trips", []):
        row["Client"] = fake_company(row.get("Client"))
        row["Sale Price HT"] = jitter(row.get("Sale Price HT"))
    for row in d.get("top_partners", []):
        row["Partner"] = fake_company(row.get("Partner"))
    for row in d.get("top_drivers", []):
        row["Driver"] = fake_person(row.get("Driver"))
        # affiliation names the partner firm the driver runs under — same namespace as
        # top_partners, so it must map through fake_company to stay consistent.
        if row.get("affiliation"):
            row["affiliation"] = fake_company(row["affiliation"])

    # --- review composer: client, passenger, driver and pickup address ---
    rv = d.get("reviews", {})
    for ride in rv.get("rides", []):
        ride["client"] = fake_company(ride.get("client"))
        ride["passenger"] = fake_person(ride.get("passenger"))
        ride["driver"] = fake_person(ride.get("driver"))
        ride["route"] = fake_route(ride.get("route"))
    if "review_url" in rv:
        rv["review_url"] = "https://g.page/r/EXAMPLE-REVIEW-LINK/review"

    # --- chauffeur hours: the name list, plus the merge/duplicate audit trails that
    #     quote names verbatim. rows[] index into drivers[], so order must not change.
    dh = d.get("driver_hours", {})
    dh["drivers"] = [fake_person(n) for n in dh.get("drivers", [])]
    for m in dh.get("merged", []):
        for k in ("kept", "dropped"):
            if k in m:
                m[k] = fake_person(m[k])
    for dup in dh.get("duplicates", []):
        for k in ("a", "b"):
            if k in dup:
                dup[k] = fake_person(dup[k])
    health = dh.get("health", {})
    if "export_file" in health:
        health["export_file"] = "export_sample.csv"

    # --- comparables inside the quote engine name real routes ---
    eng = d.get("quote_engine", {})
    for key, rows in (eng.get("comparables") or {}).items():
        for r in rows:
            if "route" in r:
                r["route"] = fake_route(r["route"])

    leaks = find_leaks(json.load(open(SRC, encoding="utf-8")), d)
    if leaks:
        print("REFUSING TO WRITE — real identities survived anonymisation:", file=sys.stderr)
        for path, value in leaks[:20]:
            print(f"  {path} = {value!r}", file=sys.stderr)
        raise SystemExit(
            f"\n{len(leaks)} leak(s). Add the offending field to main() and re-run. "
            "The sample is committed to a public repo — it must contain no real identity."
        )

    json.dump(d, open(DST, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(DST)
    print(f"wrote {os.path.basename(DST)}  ({size:,} bytes)")
    print(f"  {len(_people)} people, {len(_companies)} companies, {len(_routes)} routes fabricated")
    print("  leak check: clean")


if __name__ == "__main__":
    main()
