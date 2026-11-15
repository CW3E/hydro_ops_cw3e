# CDEC full-natural-flow station metadata

## Daily station web publication (SWE and FNF)

`config/web_stations.json` enables separate incremental web followers for CDEC
FNF and SWE. Existing daily `bin/submit_station_updates.py` cron commands submit
a two-CPU, one-hour web job after acquisition. No additional cron installation
is needed. Acquisition is scheduled at 19:00 UTC (FNF) and 19:15 UTC (SWE);
publication follows acquisition and queueing. USGS publication is not included.

The dependency is `afterany`: a completed partial acquisition can publish
verified successful stations while retaining old public histories for failures.
Each follower reads the exact job-specific
`observations/work/station-cycles/<job>/acceptance.json`, not a mutable latest
report. Missing/incomplete receipts cannot authorize publication. Limited and
representative acquisition tests do not spawn public updates.

Source SHA-256 pins, current station catalog entries, and per-station acquisition
verdicts determine changes. Only changed, accepted station histories are read
and exported. Unchanged gzip CSVs are hard-linked into immutable local snapshots;
revisions replace links atomically. No-change cycles make no transfers. Provider
flags, fixed-PST labels, native reporting intervals and display rules are retained.
Failed stations retain old bytes with `updateStatus: stale-retained` and
collection-level `publication.staleSeries` metadata. Membership changes require
explicit review rather than silently adding/removing stations.

Globus transfers changed files only: data first, collection metadata second,
root catalog third, then explicitly inventoried obsolete files. Changed public
files pass HTTPS checksum verification; unchanged files rely on the previous
accepted deployment, avoiding full-history HTTP downloads every day. There is
no remote directory mirroring or historical release duplication. Public roots
remain `observations/cdec/swe/` and `observations/cdec/fnf/`. Globus email remains
failure-only.

Operational paths:

- `web/publications/observations/<selection>-<release>/`: immutable local snapshot.
- `web/status/stations/<selection>/accepted.json`: accepted release and source pins.
- `web/status/stations/<selection>/pending.json`: frozen resumable deployment.
- `web/status/stations/<selection>/latest.json`: latest cycle result.
- `web/status/stations/submissions/<selection>/<acquisition-job>/submission.json`:
  deduplicated follower submission receipt.
- `web/work/deployments/<id>/`: transfer plan and phase/task receipts.
- `observations/logs/web-station-cycle-<job>.out`: scheduler log.

Set `enabled: false` to stop new followers and live advances. `mode: plan-only`
allows local preparation without transfer; `mode: publish` also requires an
enabled product and explicit `--publish`. This scoped product gate overrides
the generic observations transport's disabled flag only for its own destination.
Endpoints/path mappings stay in `config/web_globus_observations.json` and
credentials remain outside the repository. Do not edit a pending plan's config.

Resume without redownloading observations:

```bash
source bin/project_environment.sh
sbatch slurm/update_web_stations.sh --selection cdec-fnf \
  --report observations/work/station-cycles/ACQUISITION_JOB/acceptance.json \
  --publish --retry-failed
```

Use `cdec-swe` and its own receipt for SWE. A pending deployment finishes first
without rebuilding; a newer acquisition is picked up on the next invocation.
Retain pending snapshots/receipts. Submission uncertainty requires inspecting
recorded jobs before retrying. Product locks and durable destination ownership
prevent duplicate writers. The publisher never modifies station archives.

Commissioning: no-change jobs 5325865/5325866 completed in 2/12 seconds.
Jobs 5325868/5325869 forced AMF FNF and BCH SWE re-exports in about 2/10 seconds
of processing; all 47 FNF and 391 SWE gzip assets matched accepted bytes. These
tests made no remote or source writes.

Live metadata-only commissioning jobs 5325870 (FNF) and 5325871 (SWE) published
successfully in 43/51 seconds. Each changed nine metadata/catalog files, reused
all CSV assets, and required no remote deletions. Transfer phases and public
HTTPS checks passed. Both daily followers are enabled; no crontab was edited or
installed as part of this change.

## Current public FNF snapshot

Following user acceptance of the v2 web-map pilot, the collection was promoted
to `https://cw3e.ucsd.edu/hydro/hydro_ops/observations/cdec/fnf/catalog.json`.
Local release: `web/publications/observations/cdec-fnf-20261115-r1/`.
It retains all 47 gzip CSVs byte-for-byte from v2, with production metadata,
release identifiers and `hydro-ops-observation-index-v1` catalog schema.
Daily and monthly layer paths remain `daily/collection.json` and
`monthly/collection.json`; scientific semantics and stable collection IDs are
unchanged. This is the bootstrap release for the daily cycle above; consult its
accepted receipt for the latest release rather than assuming this dated path.

Receipts: `web/work/observations/publish-cdec-fnf-20261115-r1/`.
All three transfer phases succeeded (assets, collections, catalog). Independent
HTTPS verification passed for all 60 files, their SHA-256/byte counts, CORS,
and gzip CSV decoding (47 assets / 267,026 records).
After that gate, an explicit 60-file cleanup plan removed only the remote
`observations/pilots/cdec-fnf-full-history-pilot-20261115-v2/` files. Empty pilot
directories may remain. The local pilot and receipts are retained for recovery;
SWE and other collections were untouched. Globus notifications are failure-only.

After the [common station collection](station_collection_operations.md)
activation gate passes, `bin/submit_cdec_fnf.py` routes updates to that collection.
Legacy CSV/downloader descriptions below document retained bootstrap data;
direct legacy writes are disabled. Metadata and provider time semantics remain
unchanged. Cron installation is separate.

For the separate, station-wide sensor-3/82 SWE inventory, see
[CDEC SWE observations](cdec_swe_observations.md).

## Full-history web publication pilot

`bin/build_web_fnf_pilot.py` exports the current accepted station archive into
two independent layers: `daily/` (sensor 8, native CFS converted to m³/s) and
`monthly/` (sensor 65, native acre-feet converted to m³). Monthly volume is
not recomputed from daily flow. All 24 configured stations must be represented
monthly, and all except TNL daily; sensor 290 is excluded.

```bash
source bin/project_environment.sh
sbatch slurm/build_web_fnf_pilot.sh --output \
  web/publications/observations/<new-fnf-release>
```

Each station/layer has one full-history `history.csv.gz`, deterministic gzip
level 6. `catalog.json` links the two `collection.json` manifests; each layer
also includes station GeoJSON, provider historical notes, reference records,
and an acceptance inventory with compressed-byte sizes/checksums. Decompress
before CSV parsing. Raw columns and flags remain unchanged; plot
`fnf_display_si` against `timestamp_pst`. Preserve fixed-PST reporting dates and
months rather than converting to browser-local or NWM UTC-day intervals.
Missing/sentinel/nonnumeric values remain blank. Negative naturalized estimates
are **retained**, not subject to the SWE nonnegative display rule. Provider
flags are retained without exclusion; no maximum cutoff or interpolation is
applied. FNF is calculated naturalized flow, not measured regulated discharge.

The build is read-only: pin catalog entries, verify each source checksum and
identity, read back every compressed CSV exactly, and recheck all FNF entries
and hashes before accepting. Unrelated USGS catalog updates do not invalidate
the snapshot; any FNF change fails acceptance. It does not acquire/recover
the updater's transaction lock or modify source data. Local releases are never
overwritten. A partial failed folder without top-level passed `acceptance.json`
is not publishable.

First full pilot: job **5324270**, output
`web/publications/observations/cdec-fnf-full-history-pilot-20261115-v1/`.
It completed in **19 seconds on two allocated CPUs** with passed local acceptance:
23 daily assets / 233,297 records and 24 monthly assets / 33,729 records.
Independent post-job checking verified all 59 inventoried files and every gzip
asset's record counts and feature joins. Data assets total **8.55 MB** compressed
(61.11 MB plain CSV, including display columns); the complete package is
**8.85 MB**. Daily negative estimates (6,304) and the one monthly negative estimate
remain visible with review status; missing rows remain missing. Focused tests:
26 passed; Ruff and diff checks passed. Browser, HTTPS,
and scientific acceptance are separate. No Globus transfer, remote cleanup, or
routine publication is enabled by this command. FNF-specific reuse terms remain
unverified; do not inherit the snow dataset's licensing statement.

### FNF pilot v2 metadata refinements

V2 uses `source.kind=derived_estimate`, distinguishing daily rates and monthly
volumes as provider products rather than instruments. Secondary timestamps are
labeled **Provider observation timestamp**, with unverified meaning; only
reporting dates/months drive plots. Values, negative-estimate retention, units,
and gzip data layout are unchanged.

`acceptance.json` distinguishes `source_catalog_sha256` (the internal station
catalog snapshot, with its path) from `publication_catalog_sha256` (the published
catalog). The latter must match both the actual bytes and its `files[]` entry.
Bootstrap ingestion receipts are explicitly historical; current coverage/counts
are in `publishedSnapshot`. Supply `--update-report <passed-FNF-cycle/acceptance.json>`
to attach verified subsequent refresh receipts matching every current source
checksum. These identify retrieval time, request bounds and source hashes, not
a complete per-record correction history. Publication time remains separate.

The case-sensitive flag dictionary was checked against
[CDEC FlagList](https://cdec.water.ca.gov/reportapp/javareports?name=FlagList)
on November 15, 2026. Blank, e, r, A, s, N and t have generic provider definitions;
X is unresolved. Precipitation-specific definitions for A/t are not reinterpreted
as FNF-specific meanings. N denotes a provider error flag, but the existing
retain-without-accuracy-assurance policy remains in effect for this pilot.
No flag-based exclusion is silently introduced.

`reference-records.json` now includes absent-period cases with surrounding rows
and expected presentation line breaks (including KGF 1996-06-13), separately
from explicit missing-value records. Consumers must detect skipped fixed-PST
daily/monthly labels and insert presentation-only null gaps: `connectgaps=false`
alone cannot break a line across an absent record. Published CSVs are not filled
or modified, and monthly FNF does not inherit seasonal snow-course assumptions.

V2 build: **5325304**, output
`web/publications/observations/cdec-fnf-full-history-pilot-20261115-v2/`, using
the passed FNF cycle `observations/work/station-cycles/5322182/acceptance.json`.
Require the job-specific local acceptance before transfer. No upload or routine
publication is enabled by the build.

Job 5325304 completed in 18 seconds with passed local acceptance. Independent
review (`web/work/observations/cdec-fnf-v2-review/acceptance.json`) verified all
59 inventoried files, all 47 matched refresh receipts, the KGF absent-day fixture,
and byte-for-byte equality of all 47 gzip data assets with v1. Focused tests:
28 passed; Ruff passed. V1 is retained as historical review evidence; use v2
for the next web-map integration review.

V2 was subsequently uploaded at the user's request to the isolated public pilot:
`https://cw3e.ucsd.edu/hydro/hydro_ops/observations/pilots/cdec-fnf-full-history-pilot-20261115-v2/catalog.json`.
All three ordered Globus phases (assets, collections, catalog) succeeded.
`web/work/observations/publish-cdec-fnf-pilot-20261115-v2/` contains the frozen
plan, task receipts and passed HTTPS verification: all **60 files** matched
local bytes/SHA-256, **47 gzip CSVs / 267,026 records** decoded successfully,
and all responses allowed cross-origin access. Gzip assets use
`application/x-gzip` without HTTP Content-Encoding (client decompression required).
Existing SWE publications were untouched; no remote cleanup or routine FNF
publication was enabled. Browser integration remains a separate web-map test.

The observations subproject starts with metadata for the 24 station IDs in
`config/cdec_fnf_stations.json`. Reproduce the snapshot with:

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/collect_cdec_station_metadata.py
```

This is a small metadata-only request (24 official station pages), not a flow
time-series download. The collector uses bounded HTTP retries and sequential,
paced requests. Timestamped directories are never overwritten. A missing or
mismatched station, malformed sensor table, or invalid coordinate fails the
inventory; failed snapshots remain distinguishable by their report status.

## Layout and current snapshot

- `observations/inputs/cdec/station_metadata/<UTC timestamp>/`: original HTML.
- `observations/static/stations/cdec/<UTC timestamp>/stations.json`: complete
  parsed metadata, all sensors, historical notes, source URLs and SHA-256.
- `stations.csv`: station name, coordinates, raw elevation, basin, county,
  hydrologic area, nearby city, operator, maintenance and provenance.
- `sensors.csv`: every sensor listed at the requested stations.
- `fnf_sensors.csv`: only full-natural-flow sensors, including accumulated FNF.
- `stations.geojson`: station points for GIS review; provider coordinates are
  preserved without an inferred datum transformation.

Snapshot **20261028T071314Z** passed: all 24 requested stations and 281 sensor
records. Earlier snapshot `20261028T071300Z` failed during parser development;
its failed report and duplicate raw pages were removed after verifying the
successful snapshot. The parser regression now covers CDEC's unterminated numeric
degree entity, station ID validation, sensor identifiers and historical notes.

Downloaded/generated observations are ignored by Git. Code, configuration and
this documentation remain version-controlled. Future time series should live
under provider-specific `observations/inputs/` and `observations/processed/`;
domain mappings belong under `observations/static/domains/`, with no duplicate
station series per model domain. Model comparisons belong under
`nwm/validation/<domain>/<stream>/`.

## Sensor semantics and cautions

All 24 stations advertise **sensor 65, monthly FLOW, FULL NATURAL, AF**.
Twenty-three advertise **sensor 8, daily FULL NATURAL FLOW, CFS**; **TNL**
does not list daily FNF. Some also advertise **sensor 290, daily accumulated
FNF, AF**. Do not confuse sensor codes with the distinct internal `sensor_no`
plot identifiers; both are preserved. Other flow and forecast sensors are
retained in the full inventory but not classified as FNF observations.

The advertised start/end dates are sensor metadata, not a verified inventory of
nonmissing observations. “Present” does not establish freshness. Timezone and
daily interval conventions still need verification before ingesting time
series. Raw elevation values, including AMF's `0 ft`, are retained as reported,
not treated as surveyed/validated terrain elevations. Station coordinates
need reach-matching review before assigning a model outlet.

FNF is a calculated/naturalized flow product, not automatically equivalent to
measured regulated discharge. Preserve estimated/provisional flags and
calculation changes when evaluating model output. Notable source notes:

- [AMF](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=AMF): daily FNF
  calculations changed in WY2026 to match monthly calculations.
- [FTO](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=FTO): daily
  calculation moved downstream of the Thermalito Complex starting WY2024.
- [MRC](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=MRC): daily
  calculation moved to Merced Falls starting WY2024.
- [PSH](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=PSH): August 2025
  notes describe historical monthly FNF revisions excluding SQS contributions,
  with different methods for different historical periods.
- [TRF](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=TRF): historical
  source changes and estimated daily values later replaced with administrator
  data; estimated values omit certain reservoir adjustments.
- [YRS](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=YRS): October
  2024 notes distinguish FNF/forecast location from sensors relocated to YRE.

All provider notes are retained verbatim in the local JSON/raw HTML for review.
No USGS IDs, drainage areas, sensor coverage or reach associations are inferred
where the metadata does not provide them.

## Daily/monthly FNF records and recurring updates

`bin/submit_cdec_fnf.py` submits the initial backfill and every subsequent
refresh using `slurm/update_cdec_fnf.sh`. The job requests four CPUs (about
8 GB under the site's per-CPU memory policy), two download workers, and two
hours on ordinary shared resources. The submitter checks for active jobs under
an exclusive submission lock; the worker also holds an exclusive dataset lock.
The scheduler and compute worker use `bin/project_environment.sh` rather than
interactive Conda activation. No NRTRES is requested.

```bash
# Show the 47 planned series without downloading or submitting.
bash bin/run_cron.sh bin/update_cdec_fnf.py --plan-only
# Test scheduler wiring without submitting.
bash bin/run_cron.sh bin/submit_cdec_fnf.py --dry-run
# Start or refresh all series; duplicate active work is skipped.
bash bin/run_cron.sh bin/submit_cdec_fnf.py
```

Acquisition covers **23 daily sensor-8 series and 24 monthly sensor-65 series**.
Sensor 290 is an accumulated product and is excluded. TNL daily is explicitly
absent, not synthesized from monthly data. The initial metadata inventory pins
each sensor's historical start date. Requests always set an explicit Start and
End: a live probe found that a **blank Start silently returned only 24 months**
despite the web form's beginning-of-record wording. End is today's date in fixed
PST. A full refresh uses each sensor's entire advertised history, so older
revisions (including the PSH historical corrections) can be captured. If new
metadata extends a sensor's start, the next refresh uses the latest passed
inventory. Metadata collection itself is an explicit separate command.

The [official CDEC web-service form](https://cdec.water.ca.gov/dynamicapp/wsSensorData)
states that dates/times/output use **Pacific Standard Time**. Raw date and
observation-date strings are preserved; normalized timestamps use fixed UTC−8,
with UTC equivalents eight hours later, including summer dates. These labels
are not asserted to be interval-end stamps, and no 01–00 UTC or NWM-specific
aggregation is applied. Monthly date labels (usually the first of the month)
remain month labels. Provider interval semantics need separate confirmation
before daily model/observation comparisons.

Storage is provider/station based, shared across model domains:

```text
observations/inputs/cdec/fnf/<daily|monthly>/<station>/<response-sha256>.json.gz
observations/processed/cdec/fnf/<daily|monthly>/<station>/YYYY.csv
observations/processed/cdec/fnf/<daily|monthly>/<station>/manifest.json
observations/status/cdec/fnf/job-JOB/{plan,status}.json
observations/status/cdec/fnf/{latest,submission}.json
observations/logs/cdec-fnf-JOB.out
```

Raw responses are content-addressed and compressed; unchanged payloads are
reused. CSVs preserve raw values, units, flags, original timestamps and provider
period labels; SI values are additional columns. Daily CFS converts to m³/s
using 0.028316846592; monthly acre-feet converts to m³ using 1233.48183754752.
Monthly volume is not mislabeled as a discharge rate. No smoothing, imputation,
clipping of negative flows, or quality-flag filtering is performed. Observed
provider missing sentinel `-9999` becomes a blank numeric/SI value with an
explicit status; its original raw value is retained. Other negative numeric
FNF estimates remain numeric and are identified as negative for review. Provider
flags, including `e`, `r` and blanks, remain unchanged rather than assigning
unverified meanings.

Each series validates station/sensor/duration/units, date range, duplicate
timestamps and finite numeric conversion. Missing calendar periods, explicit
missing rows, negative values and latest numeric period are reported separately.
History loss is rejected if a refresh omits any timestamp previously published;
the previous series remains in place for review. Explicit returned missing
values can replace earlier numeric values. Annual CSV publication is atomic,
unchanged CSV bytes are not rewritten, and the series manifest is published
after all annual files. The run report separately records each accepted/failed
series; a failed run can leave successful series updated, and is never reported
as complete. Consumers should read accepted series manifests and avoid reading
in the middle of a refresh if they need a consistent multi-year snapshot.

Initial backfill **5042761** was submitted October 28, 2026 UTC. Availability
ranges and numeric coverage must be checked in its job-specific final report;
submission alone is not a completed data inventory.

That first run identified five daily series with occasional missing `obsDate`
fields (KGF, MRC, PSH, SCC, WWR). The parser now retains these dated flow records
with blank observation-date fields; it does not fabricate a timestamp. Rerun
**5042776** repairs those series and rechecks the accepted series. Audit job
**5042790** depends on its success and rebuilds every annual CSV from retained
raw JSON, checking exact bytes and all 47 series. Routine refresh jobs now
include that same audit after acquisition. `audit.json` beside the job-specific
status report must pass separately from acquisition status.

Refresh 5042776 completed successfully in **2 minutes 11 seconds**: 47 accepted
series, zero failures, 232,883 daily rows (650 annual files) and 33,729 monthly
rows (2,832 annual files). Earliest daily data is April 21, 1985; earliest
monthly data is October 1900. Individual stations have their own start dates.
Latest numeric daily dates range October 22–26, 2026; all monthly series reach
September 2026. Recent requested rows can be present with missing values and
must not be confused with numeric coverage.

KGF daily June 13, 1996 is absent: targeted JSON and CSV requests for June
12–14 both omit it. Other expected dates are represented, sometimes by explicit
missing values. Thirty daily records omit `obsDate`; these remain usable based
on `date` with blank observation-date fields. Some `date` and `obsDate` values
differ even when both are present: both are preserved and no alignment is
silently changed. Negative naturalized estimates are retained and need
application-specific review, not unconditional conversion to missing/zero.

Audit 5042790 passed: **all 47 series, 266,612 rows and 3,482 annual CSV files**
matched normalization of the checksum-verified retained raw responses exactly.
The full regression suite passed (544 passed, two skipped); focused parser,
unit/revision, and cron tests passed again after handling absent `obsDate`.
The clean-environment cron submission dry run, Ruff, shell syntax and diff
checks also passed. Acquisition and verification are complete; the saved cron
addition still requires the user's manual installation on login1.

The saved crontab and relocation template submit updates at **19:00 UTC daily
(11:00 PST)**, a practical daytime check rather than a guaranteed CDEC release
time. Installation is separate: reinstall the reviewed saved schedule manually
on login1 only, preserving unrelated entries and retaining no duplicate cron
on login2. The entry logs under `observations/logs/`, and the renderer supports
`@OBS_LOG_ROOT@` when relocating. Cron files were prepared but not installed.
