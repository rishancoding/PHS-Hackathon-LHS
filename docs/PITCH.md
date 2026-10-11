# PhotoGrid pitch and demo plan

## One-line pitch

PhotoGrid shows what solar fits on every roof in a neighborhood, using real panels you can buy, within your budget, and what it would save, in under a minute.

## Why it matters

- Most homeowners never get past "is my roof any good for solar?" Quotes take days and cover one house.
- PhotoGrid answers it for a whole area at once: every roof, existing panels, a real layout, cost, savings and CO₂.
- Average U.S. home: about 10,400 kWh a year (EIA). The default 10,000 kWh target is roughly one home.

## How it differs from Google Project Sunroof

| | Project Sunroof | PhotoGrid |
| --- | --- | --- |
| Scope | One building | Every roof in a radius up to 1 km |
| Panels | Generic | 14 real catalog models with datasheets and prices |
| Budget | Not a constraint | Layout and ranking fit your budget and energy need |
| Existing panels | Not flagged | Flagged and avoided |
| Your knowledge | None | Draw no-panel zones; re-plan instantly |
| Output | Web page | PDF, CSV, GeoJSON, JSON; runs locally |

## Numbers you can quote (measured in testing)

- First live search: about 40 s, about 93% of it Google's download. Repeat search with saved imagery: about 2–3 s.
- Comparing all 14 models with sunlight-aware placement: under 0.3 s. Re-planning after a no-panel zone: instant, no new requests.
- On the Google campus test site, filling the sunniest spots first instead of using roof averages raised the same 61-panel layout's estimated output by 24%.
- Residential panels change the answer: with a $25,000 budget and 10,000 kWh target, the top three (Silfab, Aptos, REC) meet the target with about 16 panels at roughly $3.50/W installed; the old off-grid panels all fell short at about $4/W.
- Sample area (synthetic, California prices): about $3,100 a year in bill savings, about an 8-year simple payback, about 6 t CO₂ avoided per year.
- Existing-panel detection on 48 hand-labeled roofs from 7 sites: 73% correct (68% precision, 65% recall). Say the labels were made by eye.
- 83 automated tests run offline; API keys are excluded from saved results, reports and logs; the local key lives in an ignored `.env` file.

## Three-minute demo script

1. **Search (30 s):** type a residential address you already searched the night before, so imagery is reused. Point out the search time in the header.
2. **Map (45 s):** roofs in green, existing panels in orange, new panels in blue. Toggle **Show sunlight** to show why panels went where they did.
3. **No-panel zone (30 s):** click **Draw no-panel zone**, drag a box over rooftop equipment or a chimney, **Apply & re-plan**. Panels move instantly with no new download.
4. **Panels (30 s):** scroll the cards; open **Compare two models** and point at the ✓ Better marks.
5. **Cost & energy (20 s):** installation cost vs budget, then the Impact card: bill savings, payback, CO₂.
6. **PDF (10 s):** **Download PDF**.
7. **Close (15 s):** one honest limit (it doesn't detect rooftop equipment or slope, which is why zones exist), the accuracy number, and "runs locally; 83 offline tests".

Show a shortfall case on purpose if time allows: lower the budget on the Search tab and click **Update plan**.

## Honest limits (have answers ready)

- **Equipment and slope aren't detected.** Areas are horizontal footprint, which is conservative on pitched roofs. Sunny equipment tops can attract panels; that's what no-panel zones are for.
- **Costs are a screening model**, not a quote: NREL 2024 benchmark with illustrative regional multipliers and an assumed $0.35/W module allowance.
- **Savings assume** every kWh offsets one at the state's average price; no incentives (the federal 25D credit ended after 2025), net metering or degradation.
- **Detection is a color rule**, not machine learning; it struggles with dark panels and blue-tinted imagery.
- **Google coverage varies**; some addresses only have medium-quality imagery, and the app says so under the map.

## Likely questions

- *Why not machine learning?* We needed something explainable and measurable in a weekend; we measured it (73%) and know where it fails. A trained model is the next step.
- *Where do the numbers come from?* Every source is listed on the Cost & energy tab under Methods & assumptions.
- *Does it cost money?* Each live search costs a few Google API calls; repeat searches reuse saved imagery.
- *Is my data safe?* It runs on this laptop; nothing leaves except the Google requests, and the key is excluded from saved results and reports.

## Night-before checklist

- [ ] Run each demo address once on the demo laptop so imagery is saved (repeat searches then take seconds).
- [ ] Pick at least one residential street where a model meets the target, plus one shortfall case.
- [ ] Clear old duplicate folders from `outputs/` (keep the demo searches).
- [ ] Download a PDF for each demo address as a backup.
- [ ] Record a 60–90 second screen capture of the full demo as a last resort.
- [ ] In Google Cloud: restrict the key to Geocoding, Solar and Places; set a daily quota and a budget alert.
- [ ] Don't type the API key on a projected screen; keep it in `.env`.
- [ ] Check the venue Wi-Fi. Without it, saved searches and the sample area still work.
