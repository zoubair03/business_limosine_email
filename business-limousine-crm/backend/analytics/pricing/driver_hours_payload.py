"""Builds the `driver_hours` block: mission-level rows for the chauffeur hours report.

Two things the office needs to know up front, both measured rather than assumed:

1. Only 43% of shifts have any recorded end time. That sounds fatal for an hours report,
   but the "minimum 6 hours" rule rescues it: a shift with at least one mission bills at
   least the minimum whether or not the clock was filled in. So every shift is countable;
   what's uncertain is only whether it should have been a 12-hour tranche instead of a 6.
   Untimed shifts are flagged so they can be confirmed rather than silently under-billed.

2. Driver names are entered inconsistently. The same person appears as "Surname Firstname"
   and "Firstname Surname", splitting hundreds of rides across two spellings. Pairs whose
   tokens match exactly are merged automatically; near-misses are reported for a human to
   decide, never merged silently. (Real names are deliberately not quoted here — this
   repository is public.)
"""
import sys, os, re, json, difflib, warnings
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")
import pandas as pd, numpy as np
from prep import load, PROJECT, CSV

DATA = os.path.join(PROJECT, "dashboard_data.json")
FROM_YEAR = 2024          # how far back to ship mission rows
SALARIED_HINTS = ["tarek", "bechir", "bayzi", "bayazi"]   # the fixed-salary chauffeurs


def norm_key(n):
    return " ".join(sorted(re.sub(r"[^a-zà-ÿ ]", " ", n.lower()).split()))


def main():
    raw = pd.read_csv(CSV, sep=";", engine="python", on_bad_lines="skip", quoting=0, dtype=str)
    raw.columns = [c.strip().replace("\n", " ").strip() for c in raw.columns]
    df = load(keep_unpriced=True)   # an unpriced leg is still a shift somebody drove

    def col(i, c):
        # NB: a float NaN is truthy, so `v or ""` does NOT catch a missing cell — it
        # yields the string "nan", which then bills hours to a chauffeur called "nan nan".
        if i >= len(raw):
            return ""
        v = raw[c].iloc[i]
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return ""
        s = str(v).strip()
        return "" if s.lower() in ("nan", "none", "nat") else s

    df["driver"] = [re.sub(r"\s+", " ", (col(i, "Prénom chauffeur") + " " + col(i, "Nom chauffeur")).strip())
                    for i in df.index]
    # a mission with no chauffeur recorded belongs to nobody and must not be billed
    df = df[(df["driver"].str.len() > 2) & df["dt"].notna()].copy()

    # ---- merge identical-token spellings, flag the near-misses -----------------------
    counts = df["driver"].value_counts()
    groups = {}
    for name, n in counts.items():
        groups.setdefault(norm_key(name), []).append((name, int(n)))
    canon, merged = {}, []
    for key, variants in groups.items():
        variants.sort(key=lambda t: -t[1])
        keep = variants[0][0]
        for name, _ in variants:
            canon[name] = keep
        if len(variants) > 1:
            merged.append({"kept": keep, "dropped": [v[0] for v in variants[1:]],
                           "rides": sum(v[1] for v in variants)})
    df["driver"] = df["driver"].map(canon)

    names = df["driver"].value_counts()
    names = names[names >= 5]
    keys = {n: norm_key(n) for n in names.index}
    dupes, seen = [], set()
    for a in keys:
        for b in keys:
            if a >= b or (a, b) in seen:
                continue
            seen.add((a, b))
            r = difflib.SequenceMatcher(None, keys[a], keys[b]).ratio()
            if 0.86 <= r < 1.0:
                dupes.append({"a": a, "na": int(names[a]), "b": b, "nb": int(names[b]),
                              "score": round(r, 2)})
    dupes.sort(key=lambda d: -d["score"])

    # ---- mission rows ---------------------------------------------------------------
    df = df[df["dt"].dt.year >= FROM_YEAR].copy()
    df["veh"] = df["veh"].fillna("Unspecified")
    df["svc"] = df["svc"].fillna("Unspecified")

    drivers = sorted(df["driver"].unique())
    vehicles = sorted(df["veh"].unique())
    services = sorted(df["svc"].unique())
    di = {n: i for i, n in enumerate(drivers)}
    vi = {n: i for i, n in enumerate(vehicles)}
    si = {n: i for i, n in enumerate(services)}

    rows = []
    for _, r in df.iterrows():
        st = r["h_start"]
        hrs = r["hours"]
        rows.append([
            di[r["driver"]],
            r["dt"].strftime("%Y-%m-%d"),
            None if pd.isna(st) else round(float(st), 2),
            None if pd.isna(hrs) else round(float(hrs), 2),
            vi[r["veh"]], si[r["svc"]],
        ])

    salaried = [i for n, i in di.items()
                if any(h in n.lower() for h in SALARIED_HINTS)]

    # ---- coverage, measured on shifts not missions -----------------------------------
    sh = df.groupby(["driver", df["dt"].dt.date])["hours"].agg(n="size", timed=lambda s: s.notna().sum())
    cov = dict(
        shifts=int(len(sh)),
        shifts_timed=int((sh["timed"] > 0).sum()),
        pct_timed=round(float((sh["timed"] > 0).mean() * 100), 1),
        missions=int(len(df)),
        drivers=int(len(drivers)),
        from_year=FROM_YEAR,
        date_min=str(df["dt"].min().date()),
        date_max=str(df["dt"].max().date()),
    )

    # ---- data health: what the office would have to fix at source --------------------
    st = raw["Statut mission"].fillna("").str.strip()
    live = ~st.isin(["Annulé", "Devis en cours", "Devis envoyé", "Vérification"])
    drv_raw = (raw["Prénom chauffeur"].fillna("") + " " + raw["Nom chauffeur"].fillna("")).str.strip()
    rdt = pd.to_datetime(raw["Date"], errors="coerce", dayfirst=True)
    dossiers = pd.to_numeric(raw["Dossier"], errors="coerce")

    def empty_pct(c):
        v = raw[c].fillna("").astype(str).str.strip()
        return round(float((v.isin(["", "0", "0.0", "0.00", "nan"])).mean() * 100), 1)

    health = dict(
        export_file=os.path.basename(CSV),
        export_pulled=pd.Timestamp(os.path.getmtime(CSV), unit="s").strftime("%Y-%m-%d %H:%M"),
        last_dossier=int(dossiers.max()),
        last_mission_date=str(rdt.max().date()),
        live_missions=int(live.sum()),
        no_driver=int((live & (drv_raw.str.len() <= 2)).sum()),
        no_end_time=int((live & (drv_raw.str.len() > 2) &
                         raw["Heure fin mission"].fillna("").str.strip().eq("")).sum()),
        with_driver=int((live & (drv_raw.str.len() > 2)).sum()),
        hours_fields={c: empty_pct(c) for c in
                      ["Heures réelles chauffeur", "Stand by", "Forfait net chauffeur"]},
        unpriced_kept=int((df["price"] <= 0).sum()),
    )

    payload = dict(drivers=drivers, vehicles=vehicles, services=services, rows=rows,
                   salaried_default=salaried, merged=merged, duplicates=dupes[:12],
                   coverage=cov, health=health)

    data = json.load(open(DATA, encoding="utf-8"))
    data["driver_hours"] = payload
    json.dump(data, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    print("coverage:", json.dumps(cov, indent=1))
    print("mission rows:", len(rows), " drivers:", len(drivers))
    print("auto-merged spellings:", len(merged))
    for m in merged[:6]:
        print(f"   {m['kept']}  <-  {m['dropped']}  ({m['rides']} rides)")
    print("flagged near-duplicates:", len(dupes))
    for d in dupes[:6]:
        print(f"   {d['score']}  {d['a']} ({d['na']})  ~  {d['b']} ({d['nb']})")
    print("salaried by default:", [drivers[i] for i in salaried])
    print("payload bytes:", len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))


if __name__ == "__main__":
    main()
