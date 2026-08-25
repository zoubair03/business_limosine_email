"""Merge the fitted engine + destination table + real comparables into dashboard_data.json."""
import sys, os, json, re, pandas as pd, numpy as np
sys.stdout.reconfigure(encoding="utf-8")
import geo
from prep import PROJECT

DATA = os.path.join(PROJECT, "dashboard_data.json")
E = json.load(open("engine.json", encoding="utf-8"))
df = pd.read_pickle("feat.pkl")
IDX = E["price_index"]; BASE = E["index_base_year"]

# ---------------- destination table: road km from Brussels centre ----------------
BXL = (50.8467, 4.3525)
DEST = {
    "Brussels city / hotels": (50.8467, 4.3525),
    "Brussels Airport (BRU / Zaventem)": (50.9010, 4.4844),
    "Brussels Midi station": (50.8358, 4.3358),
    "Charleroi Airport (CRL)": (50.4592, 4.4538),
    "Hoeilaart": (50.7667, 4.4667), "Waterloo": (50.7150, 4.4000),
    "La Hulpe": (50.7300, 4.4833), "Mechelen": (51.0259, 4.4776),
    "Leuven": (50.8798, 4.7005), "Nivelles": (50.5978, 4.3272),
    "Wavre": (50.7167, 4.6100), "Aalst": (50.9378, 4.0400),
    "Kruibeke": (51.1697, 4.3097), "Antwerp": (51.2194, 4.4025),
    "Ghent": (51.0543, 3.7174), "Charleroi": (50.4108, 4.4446),
    "Namur": (50.4674, 4.8720), "Floreffe": (50.4333, 4.7500),
    "Mons": (50.4542, 3.9564), "Hasselt": (50.9300, 5.3378),
    "Bruges": (51.2093, 3.2247), "Liège": (50.6326, 5.5797),
    "Tournai": (50.6070, 3.3878), "Ostend": (51.2154, 2.9286),
    "Knokke-Heist": (51.3472, 3.2833), "Marche-en-Famenne": (50.2278, 5.3436),
    "Durbuy": (50.3524, 5.4562), "Redu / Euro Space Center": (50.0058, 5.1500),
    "Spa": (50.4922, 5.8639), "Bastogne": (50.0000, 5.7167),
    "Arlon": (49.6833, 5.8167), "Maastricht (NL)": (50.8514, 5.6910),
    "Lille (FR)": (50.6292, 3.0573), "Aachen (DE)": (50.7753, 6.0839),
    "Rotterdam (NL)": (51.9244, 4.4777), "The Hague (NL)": (52.0705, 4.3007),
    "Eindhoven (NL)": (51.4416, 5.4697), "Luxembourg City": (49.6116, 6.1319),
    "Cologne (DE)": (50.9375, 6.9603), "Düsseldorf (DE)": (51.2277, 6.7735),
    "Amsterdam (NL)": (52.3730, 4.8930), "Schiphol Airport (AMS)": (52.3105, 4.7683),
    "Paris (FR)": (48.8566, 2.3522), "Frankfurt (DE)": (50.1109, 8.6821),
}
dest_km = {k: int(round(geo.road_km(BXL, v))) for k, v in DEST.items()}
dest_km["Brussels city / hotels"] = 8          # a cross-town job, not a zero-length one
dest_km = dict(sorted(dest_km.items(), key=lambda kv: kv[1]))
print("destinations:", len(dest_km))

# ---------------- real comparables ----------------
tr = df[(df.family == "transfer") & df.km_oneway.notna() & df.veh.notna() & df.exact_geo]
tr = tr[(tr.price >= 30) & (tr.price <= 4000)].copy()
dp = df[(df.family == "disposal") & df.veh.notna() & df.hours.notna()]
dp = dp[(dp.price >= 60) & (dp.price <= 6000) & (dp.hours >= 1)].copy()
for d in (tr, dp):
    d["p26"] = d.price * d.year.map(lambda y: IDX[str(BASE)] / IDX.get(str(int(y)), IDX[str(BASE)])
                                    if pd.notna(y) else 1.0)

CLEAN = re.compile(r"\s*,\s*(belgique|belgië|belgium|pays.?bas|nederland|france)\s*$", re.I)


def short(s, n=34):
    s = CLEAN.sub("", (s or "").strip())
    s = re.sub(r"\s*,\s*n[°ºo]\s*vol.*$", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).split(",")[0].strip()
    return (s[:n - 1] + "…") if len(s) > n else (s or "—")


comps = {}
KBAND = [(0, 18), (18, 40), (40, 75), (75, 130), (130, 260), (260, 520)]
for veh in E["transfer_curves"]:
    rows = []
    for lo, hi in KBAND:
        s = tr[(tr.veh == veh) & (tr.km_oneway >= lo) & (tr.km_oneway < hi)]
        if len(s) < 4:
            continue
        s = s[(s.p26 >= s.p26.quantile(.2)) & (s.p26 <= s.p26.quantile(.8))]
        if not len(s):
            continue
        r = s.sort_values("year", ascending=False).iloc[0]
        rows.append(dict(km=int(round(r.km_oneway)), price=int(round(r.p26)), year=int(r.year),
                         route=f"{short(r.pickup)} → {short(r.dest)}"))
    comps[f"transfer|{veh}"] = rows
HBAND = [(3, 5), (5, 8), (8, 10), (10, 13), (13, 20)]
for veh in E["disposal_fits"]:
    rows = []
    for lo, hi in HBAND:
        s = dp[(dp.veh == veh) & (dp.hours >= lo) & (dp.hours < hi)]
        if len(s) < 4:
            continue
        s = s[(s.p26 >= s.p26.quantile(.2)) & (s.p26 <= s.p26.quantile(.8))]
        if not len(s):
            continue
        r = s.sort_values("year", ascending=False).iloc[0]
        rows.append(dict(hours=round(float(r.hours), 1), km=int(round(r.km_oneway)) if pd.notna(r.km_oneway) else 0,
                         price=int(round(r.p26)), year=int(r.year), route=short(r.pickup, 40)))
    comps[f"disposal|{veh}"] = rows

# ---------------- validation table (the client's own executed quotes) ----------------
def pred_t(veh, km):
    c = E["transfer_curves"][veh]; xs, ys = c["km"], c["price"]
    if km <= xs[0]: return float(ys[0])
    if km >= xs[-1]:
        m = (ys[-1] - ys[-2]) / max(xs[-1] - xs[-2], 1e-6)
        return float(ys[-1] + m * (km - xs[-1]))
    return float(np.interp(km, xs, ys))


def pred_d(veh, h, km):
    f = E["disposal_fits"][veh]
    return f["base"] + f["per_hour"] * max(h, f["min_hours"]) + f["per_km"] * (km or 0)


VAL = [("Brussels airport → city hotel", "transfer", "Minibus", None, 13, 245),
       ("Brussels → Nivelles, one way", "transfer", "Minibus", None, 36, 400),
       ("Brussels → Floreffe, per leg", "transfer", "Minibus", None, 62, 450),
       ("Kruibeke day trip, 15 pax", "disposal", "Minibus", 8, 50, 900),
       ("Liège / Angleur day trip, 15 pax", "disposal", "Minibus", 6, 100, 850),
       ("Redu Euro Space Center, 15 pax", "disposal", "Minibus", 8, 125, 900),
       ("Spa ↔ Brussels concert run, 13 pax", "disposal", "Minibus", 10, 140, 1290),
       ("Brussels full-day itinerary, 9 pax", "disposal", "Minibus", 11, 15, 1250)]
val_rows = []
for lab, mode, veh, h, km, act in VAL:
    p = pred_d(veh, h, km) if mode == "disposal" else pred_t(veh, km)
    val_rows.append(dict(label=lab, mode=mode, vehicle=veh, hours=h, km=km,
                         actual=act, model=int(round(p)), diff=int(round(p - act)),
                         pct=round(100 * (p - act) / act, 1)))
print("validation mean |err%|:", round(float(np.mean([abs(r["pct"]) for r in val_rows])), 1))

payload = dict(E)
payload["destinations"] = dest_km
payload["comparables"] = comps
payload["validation"] = val_rows

data = json.load(open(DATA, encoding="utf-8"))
data["quote_engine"] = payload
json.dump(data, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("dashboard_data.json updated; quote_engine keys:", list(payload))
