# Pricing pipeline

Refits the quote calculator on a fresh Waynium export. Five scripts, run in order,
**from inside this folder** (they import each other by name and write working files here).

```bash
cd pricing
python features.py             # 1. clean + geocode every booking  -> feat.pkl
python engine.py               # 2. fit the price model            -> engine.json
python export_payload.py       # 3. merge pricing into the data    -> ../dashboard_data.json
python reviews_payload.py      # 4. review-request ride list       -> ../dashboard_data.json
python driver_hours_payload.py # 5. chauffeur hours + shifts       -> ../dashboard_data.json
python inject_data.py          # 6. embed the data in the page     -> ../dashboard.html
```

Steps 4 and 5 each add one independent block to `dashboard_data.json`; run them in any
order, but always before `inject_data.py`.

`prep.py` and `geo.py` are libraries — the scripts above import them, you don't run them
directly. (Both do print a self-check if you run them anyway: `geo.py` reports the
distance calibration, `prep.py` the row counts.)

Requires `pandas` and `numpy`.

## Updating with a new export

Drop the new `export_*.csv` into the dashboard folder (one level up) and run the four
steps. `prep.py` picks the most recently modified `export_*.csv` automatically — no paths
to edit. Then republish `dashboard.html` as the artifact to push it live.

## What each piece does

| File | Role |
|---|---|
| `prep.py` | Loads the export, drops cancellations/quotes/zero-price rows, maps vehicle and service codes, derives job duration from start/end times. |
| `geo.py` | Offline geocoder — Belgian postal codes, airports, stations, and city names to coordinates, plus the great-circle→road-distance calibration. No API calls. |
| `features.py` | Turns bookings into modelling rows: one-way road km, hours, service family (transfer vs at-disposal). Writes `feat.pkl`. |
| `engine.py` | Fits the model and cross-validates it. Writes `engine.json`. |
| `export_payload.py` | Adds destination distances, real comparable jobs, and the validation table; merges into `dashboard_data.json`. |
| `reviews_payload.py` | Recent own-fleet rides for the Google review composer, with passenger and chauffeur names. Strips passenger phone numbers. |
| `driver_hours_payload.py` | Mission-level rows for the chauffeur hours report, plus driver-name de-duplication. |
| `inject_data.py` | Embeds the JSON into the single-file dashboard. |

## Chauffeur hours — the two things to know

**Only 43% of shifts have a recorded end time.** The minimum-hours rule is what makes the
report usable regardless: a shift that happened bills at least the minimum whether or not
the clock was filled in. What's uncertain is only whether an unclocked shift should have
been a 12-hour tranche rather than a 6, so those are counted in a separate **To confirm**
column rather than quietly rounded down. Where the clock *is* complete, shifts split ~32%
at six hours or under, ~50% between six and twelve, ~19% over twelve.

**Chauffeur names are entered inconsistently.** `driver_hours_payload.py` merges spellings
whose words match exactly in a different order — "Surname Firstname" against "Firstname
Surname", which in this book is over 200 rides by a single person split in two. Anything
less certain is reported in the dashboard for a human to judge and is never merged,
because merging two real people is worse than leaving one person split.

Watch for this in particular: **two different chauffeurs share a first name** and are
distinguished only by surname. Check which one you mean before marking anyone salaried —
the Drivers screen lists the ambiguous pairs. (Names are not quoted in this file because
the repository is public; the app itself shows them.)

## How the model works

**Prices are restated in 2026 euros before fitting.** Like-for-like rates rose ~20% between
2022 and 2026, so fitting on raw multi-year history quotes about 12% light. `engine.py`
measures that drift itself from stable km × vehicle cells and rebases everything — if you
add another year of data, the index extends automatically.

**Transfers** are priced from a monotone piecewise-linear curve over one-way road km, fitted
per vehicle on band medians (isotonic / pool-adjacent-violators, so the curve never dips as
distance grows). Round trips are ×2 — verified on 379 two-leg dossiers where both legs price
identically.

**At disposal / excursion** is `base + €/hour + €/km`. The hours curve is fitted on the
standard-tier population; the €/km term is fitted on the full population because jobs that
genuinely range out of town are scarce.

**Tiers.** Clients are profiled against the list curve and split into partner-net (<0.85×),
standard, and premium (>1.20×). This matters: 65–76% of the price spread on any given trip
is *which client it is*, not the trip.

## Accuracy, as measured

Five-fold cross-validation on held-out real bookings:

| Segment | Within €50 | Median miss |
|---|---|---|
| Sedan transfers | 91% | €11 |
| Van transfers | 84% | €8 |
| Minibus transfers | 58% | €39 |
| At disposal / excursion | 43–55% | €75 |

Against the eight executed quotes supplied by the office: **6.3% average gap**.

## Known limits

- No live routing API. Distances come from the offline geocoder, validated to within 5.8%
  against 25 known routes. Anything unusual should be checked on Maps.
- ~10% of bookings have an address that won't geocode and are excluded from the fit.
- Luxury Sedan at disposal (n=122) and Coach work (n=41) have too little data to be
  reliable — the dashboard flags both.
- Night and weekend surcharges were tested and **do not exist** in the historical book
  (1.00–1.04× and 0.96–1.02×). Don't add one back without evidence.
