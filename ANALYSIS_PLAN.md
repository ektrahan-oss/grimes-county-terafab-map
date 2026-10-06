# Grimes County Terafab Map: Analysis Plan

Read this whole file before doing anything. The last line is "END OF PLAN". If you don't see it, stop and tell me the file is cut off.

Earlier versions of this plan got cut off when pasted, so Parts 1 to 3 were never actually done. Ignore any earlier partial instructions. This file replaces them. Start at Part 0.

## Goal

Turn the data we've built into clear, sourced findings for four audiences:
1. Commercial developers and investors
2. Residents and landowners
3. Contractors and local businesses
4. Local officials, civic groups and journalists

This is analysis, not map building. Don't change index.html or the map layers except in the Site Update step below. Work ONE PART AT A TIME. After each part, show me the key numbers, how you calculated them, and anything surprising, then wait for my OK.

## Rules for the whole plan

### Git and privacy
- Run git pull --rebase on main before starting, since the GitHub Action commits data updates daily.
- Commit after each part I approve, one commit per part. Don't push until I say so, and run git pull --rebase before every push. Never force push.
- Create analysis/ for scripts, analysis/outputs/ for public-safe results, and analysis/private/ for anything parcel-level or sensitive.
- Add analysis/private/ to .gitignore BEFORE creating anything in it. Confirm with git check-ignore that it's ignored.
- Ask before installing packages.

### Ownership and privacy policy
- Never show names of individual people, families or personal trusts as owners anywhere.
- Ownership may be shown only for companies connected to the Terafab project.
- Public outputs have no individual parcel values. Any aggregated value data needs at least 5 parcels per area; merge or drop smaller areas.

### Every finding
- Every number needs: the method, the source dataset and its date, and a confidence level (high, medium or low).
- Plain language. No em dashes, no hype words. Forward-looking points are "signals to watch," not predictions.
- Where an estimate needs assumptions, put them in a clearly labeled settings block at the top of the script and run low, middle and high scenarios.
- Save each part's key numbers to analysis/outputs/key_numbers.json (section, metric, value, unit, source, date, confidence).
- Save charts as PNG to analysis/outputs/charts/, sized to read well on a phone.

## Part 0: Confirm the public map is current

Check and report on each item. Don't fix anything yet; tell me what you find and recommend fixes.
1. Run git fetch and git status. Is local main in sync with origin/main? List any unpushed commits or uncommitted changes.
2. Check the GitHub Actions refresh workflow (GitHub CLI if it's set up; if not, tell me and I'll check the Actions tab). When did it last run, did it succeed, and did the daily runs happen over the last 7 days? Show any errors.
3. Check data/meta.json. List each layer with its last updated date and flag anything older than its schedule (daily items from today or yesterday, monthly items from within the last month).
4. Check the newest dates in data/news.json and data/changes.json, and the date of the most recent timeline entry. Flag anything that looks stuck.
5. Fetch the live site at https://grimesgrowthwatch.com and confirm it loads over HTTPS, the www version works, and the live data/meta.json matches origin/main.
6. Give me a short status table: item, status (current, stale or error), last updated, and the recommended fix.

Wait for my OK.

## Site update (do right after Part 0 is approved)

1. In the About tab, point the contact button to mailto:hello@grimesgrowthwatch.com with the subject line "Grimes County map question".
2. Point the newsletter signup button to https://grimesgrowthwatch.beehiiv.com/subscribe, open it in a new tab, label it "Get updates by email", and make sure it's visible.
3. Add a small "Grimes Growth Watch" name above the "About me and this map" section if it isn't there already.
4. Switch any hardcoded links to ektrahan-oss.github.io/grimes-county-terafab-map to https://grimesgrowthwatch.com.
5. Keep it mobile friendly and change nothing else. Commit as "Add custom domain, contact and newsletter links", show me the change, and push when I say so.

Then set up the analysis folders and .gitignore from the Git and privacy rules, and confirm analysis/private/ is ignored.

## Part 1: Buildable land

- Start with parcel lines. Remove: the reinvestment zone, project-linked holdings, floodway and 100-year floodplain, a buffer around pipelines (default 50 ft each side) and transmission lines (default 100 ft each side). Buffer widths go in the settings block.
- Summarize remaining land by drive time ring (15, 30, 60 min), by parcel size class (under 5, 5-20, 20-100, 100+ acres), and by whether the parcel touches a state highway or FM road.
- Public output: totals by ring and size class only. No parcel IDs.
- Show how much floodplain and pipeline constraints remove in each ring.

## Part 2: Infrastructure access

- For buildable land from Part 1, summarize how much is within 1, 3 and 5 miles of: a natural gas transmission pipeline, a 138 kV or 345 kV line, and a state highway.
- Note which gas transmission lines and operators are closest to the site, since the planned on-site power plants will need gas supply.

## Part 3: Traffic and roads

- For traffic stations along the likely routes and within the 30 minute ring, show the 2021-2025 trend and the change versus the 2023-2025 baseline.
- Rank the corridors most likely to absorb construction and commuter traffic, and note where the planned SH 30 widening falls.
- Chart: traffic trend for the 5 to 8 most relevant stations.

## Part 4: Workforce and housing

- Using the population and housing layer, summarize housing units, vacancy, median rent and median home value for tracts in the 30 and 60 minute rings.
- Build a housing demand scenario from the project's publicly reported job figures (find them in the timeline or news data and cite the source). Settings: share of workers relocating, household size, share renting. Run low, middle and high.
- Compare scenario demand with current vacant units in each ring.

## Part 5: Schools

- Show enrollment trends for the five districts.
- Using the Part 4 scenarios, estimate added students per district under low, middle and high, with assumptions in the settings block (for example children per relocating household, and share of households in each district by drive time).

## Part 6: Water

- Stream gauges: current and historical Navasota River flow, including low-flow years and how often flow drops below key thresholds.
- Surface water rights in the watersheds around the site: total permitted acre-feet and main uses.
- Wells within 5 and 10 miles of the zone: counts, typical depth and aquifer.
- Wastewater outfalls downstream of the site, existing and pending.
- Frame it as the water loop: river, reservoir, plant, discharge, downstream. Conclusions stay as signals to watch.

## Part 7: Land values

- Using the aggregated land value data, show median value per acre by drive time ring and by distance band from the zone (0-2, 2-5, 5-10, 10+ miles).
- If there's more than one year of data, show the change. If not, set this up as the baseline and say so.

## Part 8: Timing signals

- From the permit watch, entity watch, county documents, timeline and change log, build a dated list of milestones so far and signals still missing (for example the air permit for the on-site power plants).
- Output analysis/outputs/timing_signals.md.

## Part 9: Audience briefs

Write one short brief per audience in analysis/outputs/briefs/:
- developers.md: buildable land, infrastructure access, workforce housing demand, land value gradient, timing signals
- residents_landowners.md: floodplain and pipeline context, traffic changes, water wells and river flow, land values, schools
- contractors_businesses.md: timing signals, routes, worker housing and services demand
- officials_civic_media.md: roads, schools, water, housing, and what to watch next

Each brief: top 5 findings, each 2 to 3 sentences with the key number and source, then "signals to watch" and a short methods and caveats section.

## Part 10: Private landowner memo (PRIVATE ONLY)

- Write to analysis/private/ only. Confirm the folder is gitignored before writing anything.
- I'll give you my parcel property IDs at this step. Don't ask for them before Part 10.
- For those parcels: distance to the zone and site, drive time ring, road frontage, floodplain and pipeline constraints, nearby infrastructure, and how they compare to the land value areas around them.
- Compare fit for: contractor laydown yard, RV or workforce housing, holding, and selling. Facts and tradeoffs only, not recommendations.

## Wrap-up

Give me a one-page summary: the 3 strongest findings for each audience, which numbers are low confidence, and what data would most improve the weak spots.

END OF PLAN
