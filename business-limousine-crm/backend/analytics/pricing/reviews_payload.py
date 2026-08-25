"""Builds the `reviews` block for dashboard_data.json.

There are no email addresses anywhere in the Waynium export (6 matches in 6 MB, 2 distinct,
both operational rather than customer contacts) or in the workbook. So this cannot mail-merge
a recipient list. What it can do is take a real completed ride and pre-write the whole
request around it, leaving only the address to paste in.

Deliberately NOT exported: passenger phone numbers, which sit in the same free-text field as
the names.
"""
import sys, os, re, json, warnings
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")
import pandas as pd, numpy as np
from prep import load, PROJECT, CSV

DATA = os.path.join(PROJECT, "dashboard_data.json")

PHONE = re.compile(r"[\+\(]?\d[\d\s\.\-\(\)]{6,}")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
NOISE = re.compile(r"\b(passenger|passagers?|mobile|tel|gsm|phone|pax|mini\s*van|minibus|van|sedan|car|driver)\b[:\s]*", re.I)


def clean_name(s):
    """A displayable passenger name — phone numbers and labels stripped out."""
    s = str(s or "")
    s = EMAIL.sub("", s)
    s = PHONE.sub("", s)
    s = NOISE.sub("", s)
    s = re.sub(r"[\(\)\+]", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;/-")
    if len(s) < 3 or len(s) > 42 or not re.search(r"[A-Za-zÀ-ÿ]{2}", s):
        return ""
    parts = s.split()
    return " ".join(parts[:3])


def main():
    raw = pd.read_csv(CSV, sep=";", engine="python", on_bad_lines="skip", quoting=0, dtype=str)
    raw.columns = [c.strip().replace("\n", " ").strip() for c in raw.columns]

    df = load()
    df = df[df["dt"].notna()].copy()
    # attach passenger text by row position (load() preserves the source order after filtering)
    pax_txt = raw["Passagers"].fillna("")
    df["pax_name"] = [clean_name(pax_txt.iloc[i]) if i < len(pax_txt) else ""
                      for i in df.index]

    # ---- who is actually ours to ask -------------------------------------------------
    # "Interne" in Partenaire = our own chauffeur drove it. A named partner = subcontracted
    # out, so the customer met someone else's driver. Blank = unassigned/legacy record.
    part = raw["Partenaire"].fillna("").str.strip()
    df["operator"] = [part.iloc[i] if i < len(part) else "" for i in df.index]
    df["own_fleet"] = df["operator"].str.lower() == "interne"
    df["subcontracted"] = (~df["own_fleet"]) & (df["operator"] != "")
    direct = df[df["own_fleet"]]

    drv = raw["Prénom chauffeur"].fillna("").astype(str)

    def driver_of(i):
        s = drv.iloc[i].strip() if i < len(drv) else ""
        s = re.sub(r"[^A-Za-zÀ-ÿ' \-]", "", s).strip()
        return s.title() if len(s) >= 2 else ""

    latest = df["dt"].max()
    last30 = df[df["dt"] >= latest - pd.Timedelta(days=30)]
    last30_direct = last30[last30["own_fleet"]]

    # repeat cadence: how often does a typical account ride? (guards against over-asking)
    acct = df.groupby("client")["dt"].agg(n="size", first="min", last="max")
    acct = acct[acct["n"] >= 6]
    span_days = (acct["last"] - acct["first"]).dt.days.clip(lower=1)
    cadence = float((span_days / acct["n"]).median())

    stats = dict(
        total_rides=int(len(df)),
        direct_rides=int(len(direct)),
        subcontracted_rides=int(df["subcontracted"].sum()),
        rides_last_30=int(len(last30)),
        askable_last_30=int(len(last30_direct)),
        accounts=int(df["client"].nunique()),
        median_days_between_rides=round(cadence, 1),
        emails_in_export=2,
        latest_ride=str(latest.date()),
    )

    # ---- the ride picker: recent completed jobs ------------------------------------
    def short(s, n=38):
        s = re.sub(r"\s*,\s*(belgique|belgië|belgium)\s*$", "", str(s or "").strip(), flags=re.I)
        s = re.sub(r"\s*,\s*n[°ºo]\s*vol.*$", "", s, flags=re.I)
        s = re.sub(r"\s+", " ", s).split(",")[0].strip()
        return (s[:n - 1] + "…") if len(s) > n else (s or "—")

    recent = df[df["own_fleet"]].sort_values("dt", ascending=False).head(60)
    rides = []
    for i, r in recent.iterrows():
        rides.append(dict(
            date=str(r["dt"].date()),
            client=short(r["client"], 40),
            passenger=r["pax_name"],
            vehicle=str(r["veh"]) if pd.notna(r["veh"]) else "",
            route=f"{short(r['pickup'], 30)} → {short(r['dest'], 30)}",
            driver=driver_of(i),
        ))

    payload = dict(stats=stats, rides=rides,
                   review_url="https://g.page/r/CXK-YMSbiRNfEAE/review")

    data = json.load(open(DATA, encoding="utf-8"))
    data["reviews"] = payload
    json.dump(data, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    print("stats:", json.dumps(stats, indent=1))
    print("rides exported:", len(rides))
    print("  with a passenger name:", sum(1 for r in rides if r["passenger"]))
    print("  with a driver name:", sum(1 for r in rides if r["driver"]))
    print("sample:", json.dumps(rides[0], ensure_ascii=False))


if __name__ == "__main__":
    main()
