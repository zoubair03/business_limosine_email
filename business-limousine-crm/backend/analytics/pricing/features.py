"""Turn cleaned bookings into a modelling frame: distance, hours, service family, vehicle."""
import pandas as pd, numpy as np, re
from prep import load
import geo

AIR = re.compile(r"a[eé]roport|airport|zaventem|schiphol|luchthaven|\(bru\)|\(crl\)|\(ams\)|\(anr\)|\(cdg\)", re.I)
STA = re.compile(r"\bgare\b|\bstation\b|midi|zuid|centraal|noord\b|guillemins", re.I)


def build():
    df = load().reset_index(drop=True)

    fr, to = [], []
    for t in df["pickup"]:
        fr.append(geo.geocode(t))
    for t in df["dest"]:
        to.append(geo.geocode(t))

    df["from_ll"] = fr
    df["to_ll"] = to
    df["geo_ok"] = df["from_ll"].notna() & df["to_ll"].notna()

    km = []
    for a, b, ok in zip(df["from_ll"], df["to_ll"], df["geo_ok"]):
        km.append(geo.road_km((a[0], a[1]), (b[0], b[1])) if ok else np.nan)
    df["km_oneway"] = km
    df["how_from"] = [x[2] if x else None for x in df["from_ll"]]
    df["how_to"] = [x[2] if x else None for x in df["to_ll"]]
    df["exact_geo"] = df["how_from"].isin(["airport", "station", "postcode", "city"]) & \
                      df["how_to"].isin(["airport", "station", "postcode", "city"])

    # same start & end point (a there-and-back / disposal job)
    df["same_point"] = df["geo_ok"] & (df["km_oneway"] < 3)

    df["touch_air"] = df["pickup"].str.contains(AIR) | df["dest"].str.contains(AIR)
    df["touch_sta"] = df["pickup"].str.contains(STA) | df["dest"].str.contains(STA)

    # ---- service family -------------------------------------------------
    def fam(r):
        if r["svc"] == "Hourly disposal":
            return "disposal"
        if r["svc"] == "Excursion / itinerary":
            return "disposal"
        if r["svc"] in ("Airport transfer", "City transfer", "Station transfer"):
            return "transfer"
        # inferred
        if r["same_point"]:
            return "disposal"
        if pd.notna(r["hours"]) and r["hours"] >= 4:
            return "disposal"
        if r["touch_air"] or r["touch_sta"]:
            return "transfer"
        if pd.notna(r["km_oneway"]):
            return "transfer"
        return "unknown"

    df["family"] = df.apply(fam, axis=1)

    def sub(r):
        if r["family"] != "transfer":
            return None
        if r["svc"] == "Airport transfer" or r["touch_air"]:
            return "airport"
        if r["svc"] == "Station transfer" or r["touch_sta"]:
            return "station"
        return "point"

    df["sub"] = df.apply(sub, axis=1)
    return df


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    df = build()
    print("rows", len(df))
    print("geo resolvable both ends: %.1f%%   exact (no prefix fallback): %.1f%%" % (
        df["geo_ok"].mean()*100, df["exact_geo"].mean()*100))
    print()
    print(df["family"].value_counts())
    print()
    print(df["sub"].value_counts())
    print()
    print("km_oneway distribution (transfers):")
    print(df[df["family"] == "transfer"]["km_oneway"].describe(percentiles=[.1, .25, .5, .75, .9, .99]).round(1))
    print()
    print("hours known on disposal rows: %d / %d" % (
        df[df["family"] == "disposal"]["hours"].notna().sum(), (df["family"] == "disposal").sum()))
    df.to_pickle("feat.pkl")
    print("saved feat.pkl")
