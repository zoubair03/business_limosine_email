"""Business Limousine pricing engine v3.

Fits a 2026-euro list price from 8,032 executed bookings:
  * every historical price is restated in 2026 euros via a like-for-like price index
  * transfers  -> isotonic piecewise-linear curve over one-way road km, per vehicle
  * disposal   -> base + EUR/hour (+ EUR/km for touring), per vehicle
  * commercial tier multipliers (partner-net / standard / premium) measured against the list price
"""
import sys, json, pandas as pd, numpy as np
sys.stdout.reconfigure(encoding="utf-8")
pd.set_option("display.width", 240)
rng = np.random.default_rng(2024)

VEHS = ["Sedan (E-Class)", "Van (V-Class)", "Luxury Sedan (S-Class)", "Minibus"]
KB = [(0,5),(5,10),(10,15),(15,20),(20,30),(30,45),(45,60),(60,80),(80,110),(110,150),(150,220),(220,350),(350,520)]
HB = [(1,2),(2,3),(3,4),(4,5),(5,6),(6,7),(7,8),(8,9),(9,10),(10,12),(12,15),(15,20)]


def isotonic(y, w):
    """Weighted pool-adjacent-violators -> nearest non-decreasing fit."""
    val, wt, size = list(map(float, y)), list(map(float, w)), [1] * len(y)
    i = 0
    while i < len(val) - 1:
        if val[i] > val[i + 1] + 1e-9:
            nw = wt[i] + wt[i + 1]
            val[i] = (val[i] * wt[i] + val[i + 1] * wt[i + 1]) / nw
            wt[i] = nw; size[i] += size[i + 1]
            del val[i + 1], wt[i + 1], size[i + 1]
            if i:
                i -= 1
        else:
            i += 1
    out = []
    for v, s in zip(val, size):
        out += [v] * s
    return out[:len(y)]


def price_index(tr):
    """Like-for-like median price by year, indexed to 2022, from stable km x vehicle cells."""
    idx = {}
    for y in sorted(tr["year"].dropna().unique()):
        fac = []
        for veh in ["Sedan (E-Class)", "Van (V-Class)", "Minibus"]:
            for lo, hi in [(8, 20), (20, 45), (45, 80)]:
                a = tr[(tr.year == y) & (tr.veh == veh) & (tr.km_oneway >= lo) & (tr.km_oneway < hi)]["price"]
                b = tr[(tr.year == 2022) & (tr.veh == veh) & (tr.km_oneway >= lo) & (tr.km_oneway < hi)]["price"]
                if len(a) >= 12 and len(b) >= 12:
                    fac.append(a.median() / b.median())
        if fac:
            idx[int(y)] = float(np.median(fac))
    # enforce a non-decreasing index: a rate card does not go backwards
    ys = sorted(idx)
    vals = isotonic([idx[y] for y in ys], [1] * len(ys))
    return {y: round(v, 3) for y, v in zip(ys, vals)}


def fit_transfer(v, minn=8):
    xs, ys, ns = [], [], []
    for lo, hi in KB:
        s = v[(v.km_oneway >= lo) & (v.km_oneway < hi)]
        if len(s) >= minn:
            xs.append(round(float(s.km_oneway.median()), 1))
            ys.append(float(s.p26.median()))
            ns.append(len(s))
    if len(xs) < 3:
        return None
    return [xs, [round(x, 1) for x in isotonic(ys, ns)]]


def pred_transfer(c, km):
    xs, ys = (c["km"], c["price"]) if isinstance(c, dict) else c
    if km <= xs[0]:
        return float(ys[0])
    if km >= xs[-1]:
        m = (ys[-1] - ys[-2]) / max(xs[-1] - xs[-2], 1e-6)
        return float(ys[-1] + m * (km - xs[-1]))
    return float(np.interp(km, xs, ys))


def fit_disposal(v, km_pool=None):
    """Hours curve from `v` (the list-price population); the touring EUR/km term is
    estimated on `km_pool` because jobs that actually range far are scarce."""
    xs, ys, ns = [], [], []
    for lo, hi in HB:
        s = v[(v.hours >= lo) & (v.hours < hi)]
        if len(s) >= 6:
            xs.append(float(s.hours.median())); ys.append(float(s.p26.median())); ns.append(len(s))
    if len(xs) < 3:
        return None
    xs, ys, w = np.array(xs), np.array(isotonic(ys, ns)), np.sqrt(ns)
    A = np.vstack([xs, np.ones_like(xs)]).T * w[:, None]
    (m, c), *_ = np.linalg.lstsq(A, ys * w, rcond=None)
    km_rate = 0.0
    pool = v if km_pool is None else km_pool
    far = pool[pool.km_oneway.notna() & (pool.km_oneway > 25)]
    if len(far) >= 20:
        resid = far.p26.values - (c + m * far.hours.values)
        km_rate = float(np.clip(np.median(resid / np.maximum(far.km_oneway.values, 1)), 0, 4))
    return dict(base=float(c), per_hour=float(m), per_km=float(km_rate),
                min_hours=float(min(xs)), min_charge=float(c + m * min(xs)))


def pred_disposal(f, hours, km=0):
    return f["base"] + f["per_hour"] * max(hours, f["min_hours"]) + f["per_km"] * (km or 0)


def ok(p, a):
    return np.abs(p - a) <= np.maximum(50.0, 0.10 * a)


def main():
    df = pd.read_pickle("feat.pkl")
    tr = df[(df.family == "transfer") & df.km_oneway.notna() & df.veh.notna() & df.exact_geo]
    tr = tr[(tr.price >= 30) & (tr.price <= 4000)].copy()
    dp = df[(df.family == "disposal") & df.veh.notna() & df.hours.notna()]
    dp = dp[(dp.price >= 60) & (dp.price <= 6000) & (dp.hours >= 1)].copy()

    IDX = price_index(tr)
    latest = max(IDX)
    print("price index (like-for-like, 2022 = 1.00):", IDX, f"-> restating everything to {latest}")
    for d in (tr, dp):
        d["p26"] = d["price"] * d["year"].map(lambda y: IDX[latest] / IDX.get(int(y), IDX[latest])
                                              if pd.notna(y) else 1.0)

    # tier assignment from a provisional curve
    prov = {v: fit_transfer(tr[tr.veh == v]) for v in VEHS}
    prov = {k: v for k, v in prov.items() if v}
    tmp = tr[tr.veh.isin(prov)].copy()
    tmp["fac"] = tmp.p26 / [pred_transfer(prov[v], k) for v, k in zip(tmp.veh, tmp.km_oneway)]
    cf = tmp.groupby("client")["fac"].agg(n="size", f="median")
    prof = cf[cf.n >= 8]
    net, prem = set(prof[prof.f < 0.85].index), set(prof[prof.f > 1.20].index)
    for d in (tr, dp):
        d["tier"] = np.where(d.client.isin(net), "net", np.where(d.client.isin(prem), "premium", "standard"))
    print(f"clients profiled {len(prof)}: net {len(net)} | premium {len(prem)} | standard {len(prof)-len(net)-len(prem)}")

    tr_s, dp_s = tr[tr.tier == "standard"], dp[dp.tier == "standard"]

    def cv(v, fitf, predf, kind):
        folds = np.array_split(rng.permutation(len(v)), 5)
        e, o = [], []
        for f in folds:
            te, trn = v.iloc[f], v.drop(v.index[f])
            m = fitf(trn)
            if m is None:
                continue
            p = (np.array([predf(m, k) for k in te.km_oneway]) if kind == "t"
                 else np.array([predf(m, h, k) for h, k in zip(te.hours, te.km_oneway.fillna(0))]))
            e.append(np.abs(p - te.p26.values)); o.append(ok(p, te.p26.values))
        e, o = np.concatenate(e), np.concatenate(o)
        return dict(n=int(len(v)), mae=round(float(e.mean())), med_ae=round(float(np.median(e))),
                    w50=round(float(100 * (e <= 50).mean()), 1), wok=round(float(100 * o.mean()), 1))

    print()
    print(f"{'TRANSFERS (2026 EUR)':<26}{'n':>6}{'MAE':>7}{'medAE':>7}{'±50':>9}{'±50 or ±10%':>14}")
    tcurves, tdiag = {}, {}
    for veh in VEHS:
        v = tr_s[tr_s.veh == veh].reset_index(drop=True)
        if len(v) < 60:
            continue
        tcurves[veh] = fit_transfer(v)
        tdiag[veh] = cv(v, fit_transfer, pred_transfer, "t")
        d = tdiag[veh]
        print(f"{veh:<26}{d['n']:6d}{d['mae']:7d}{d['med_ae']:7d}{d['w50']:8.1f}%{d['wok']:13.1f}%")

    print()
    print(f"{'DISPOSAL (2026 EUR)':<26}{'n':>6}{'MAE':>7}{'medAE':>7}{'±50':>9}{'±50 or ±10%':>14}")
    dfits, ddiag = {}, {}
    for veh in VEHS:
        v = dp_s[dp_s.veh == veh].reset_index(drop=True)
        if len(v) < 60:
            continue
        pool = dp[dp.veh == veh]
        dfits[veh] = fit_disposal(v, km_pool=pool)
        ddiag[veh] = cv(v, lambda x, p=pool: fit_disposal(x, km_pool=p), pred_disposal, "d")
        d = ddiag[veh]
        print(f"{veh:<26}{d['n']:6d}{d['mae']:7d}{d['med_ae']:7d}{d['w50']:8.1f}%{d['wok']:13.1f}%")

    t2 = tr[tr.veh.isin(tcurves)].copy()
    t2["fac"] = t2.p26 / [pred_transfer(tcurves[v], k) for v, k in zip(t2.veh, t2.km_oneway)]
    tiers = {}
    print()
    for lab in ["net", "standard", "premium"]:
        s = t2[t2.tier == lab]["fac"]
        tiers[lab] = round(float(s.median()), 2)
        print(f"  tier {lab:9s} n={len(s):5d}  {s.median():.2f}x  (p25 {s.quantile(.25):.2f} / p75 {s.quantile(.75):.2f})")

    bands = {}
    print()
    for lab, data, fits, kind in [("transfer", tr_s, tcurves, "t"), ("disposal", dp_s, dfits, "d")]:
        for veh, f in fits.items():
            v = data[data.veh == veh]
            p = (np.array([pred_transfer(f, k) for k in v.km_oneway]) if kind == "t"
                 else np.array([pred_disposal(f, h, k) for h, k in zip(v.hours, v.km_oneway.fillna(0))]))
            q = np.quantile(v.p26.values / np.maximum(p, 1), [.25, .75])
            bands[f"{lab}|{veh}"] = [round(float(q[0]), 2), round(float(q[1]), 2)]
            print(f"  band {lab:9s} {veh:24s} {q[0]:.2f}x - {q[1]:.2f}x")

    out = dict(
        price_index=IDX, index_base_year=int(latest),
        transfer_curves={k: {"km": v[0], "price": [round(x) for x in v[1]]} for k, v in tcurves.items()},
        disposal_fits={k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in dfits.items()},
        transfer_accuracy=tdiag, disposal_accuracy=ddiag, quote_bands=bands, tier_multipliers=tiers,
        road_factor={"slope": 1.1736, "intercept": 0.87, "mape_pct": 5.8, "n_routes": 25},
        coverage=dict(clean_rows=int(len(df)), transfer_rows=int(len(tr)), disposal_rows=int(len(dp)),
                      transfer_std=int(len(tr_s)), disposal_std=int(len(dp_s)),
                      geo_pct=round(float(df.geo_ok.mean() * 100), 1), clients_profiled=int(len(prof))),
    )
    json.dump(out, open("engine.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nwrote engine.json")


if __name__ == "__main__":
    main()
