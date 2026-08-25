"""Shared loader: clean the Waynium export into a pricing-analysis frame."""
import pandas as pd, numpy as np, re, glob, os, warnings
warnings.filterwarnings("ignore")

# The newest Waynium export sitting in the dashboard folder (one level up from this script).
# Drop a fresh export_*.csv there and re-run — nothing else needs editing.
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_exports = sorted(glob.glob(os.path.join(PROJECT, "export_*.csv")), key=os.path.getmtime)
if not _exports:
    raise SystemExit(f"No export_*.csv found in {PROJECT}")
CSV = _exports[-1]

CANCEL = {"Annulé", "Devis en cours", "Devis envoyé", "Vérification"}
VEH = {"MB.E": "Sedan (E-Class)", "MB.V": "Van (V-Class)", "MINI BUS": "Minibus",
       "LS": "Luxury Sedan (S-Class)", "BUS": "Coach / Full-size Bus"}
SERV = {"TA": "Airport transfer", "T": "City transfer", "TG": "Station transfer",
        "M": "Hourly disposal", "E": "Excursion / itinerary"}


def num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False).str.strip(), errors="coerce")


def load(keep_unpriced=False):
    """keep_unpriced=True keeps missions with no sale price. Wrong for pricing — a EUR 0
    row carries no rate — but right for a chauffeur-hours report, where an unpriced leg
    (a second vehicle on the same job, an internal move) is still a shift somebody drove."""
    df = pd.read_csv(CSV, sep=";", engine="python", on_bad_lines="skip", quoting=0, dtype=str)
    df.columns = [c.strip().replace("\n", " ").strip() for c in df.columns]
    df = df.rename(columns={
        "Statut mission": "status", "Type de service": "svc_code", "Date": "date",
        "Heure début mission": "t_start", "Heure fin mission": "t_end",
        "Nom client": "client", "Pax": "pax", "Prise en charge": "pickup",
        "Itinéraire": "itin", "Destination": "dest",
        "Tout type de véhicule": "veh_code", "Modèle de véhicule": "veh_model",
        "Prix de vente HT": "price", "Prix d'achat ht": "cost",
        "Montant HT total des frais additionnels": "extras",
        "Partenaire": "partner", "Libellé": "label",
    })
    df["price"] = num(df["price"])
    df["cost"] = num(df["cost"])
    df["extras"] = num(df["extras"])
    df["pax"] = num(df["pax"]).fillna(0).astype(int)
    df["status"] = df["status"].fillna("").str.strip()
    df = df[~df["status"].isin(CANCEL)]
    if keep_unpriced:
        df["price"] = df["price"].fillna(0)
    else:
        df = df[df["price"].notna() & (df["price"] > 0)]
    df = df[df["price"] < 20000]                       # drop the 100.8k mega-outlier & friends
    df["veh"] = df["veh_code"].astype(str).str.strip().map(VEH)
    df["svc"] = df["svc_code"].astype(str).str.strip().map(SERV)
    df["dt"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
    df["year"] = df["dt"].dt.year

    def hhmm(s):
        s = s.astype(str).str.strip()
        h = pd.to_numeric(s.str.slice(0, 2), errors="coerce")
        m = pd.to_numeric(s.str.slice(3, 5), errors="coerce")
        return h + m / 60.0

    df["h_start"] = hhmm(df["t_start"])
    df["h_end"] = hhmm(df["t_end"])
    d = df["h_end"] - df["h_start"]
    d = d.where(d >= 0, d + 24)
    df["hours"] = d.where((d > 0) & (d <= 20))
    for c in ["pickup", "itin", "dest", "client", "veh_model", "label"]:
        df[c] = df[c].fillna("").astype(str).str.strip()
    df["text"] = (df["pickup"] + " | " + df["itin"] + " | " + df["dest"]).str.lower()
    return df


if __name__ == "__main__":
    df = load()
    print("clean rows:", len(df))
    print(df["veh"].value_counts(dropna=False))
    print(df["svc"].value_counts(dropna=False))
    print("hours known:", df["hours"].notna().sum())
    print("year span:", df["year"].min(), df["year"].max())
    print(df[["price"]].describe())
