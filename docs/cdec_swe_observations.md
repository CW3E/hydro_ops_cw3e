# CDEC SWE station and sensor metadata

## Current collection policy — CDEC clarification, November 15, 2026

Unsupported SWE combinations are retired from the active station collection by
`bin/archive_uninterpreted_swe.py` (SLURM entry point
`slurm/archive_uninterpreted_swe.sh`) into
`observations/archive/stations/cdec-swe-uninterpreted/v1/`. The offline archive
retains original NetCDF bytes, a separate catalog, the pre-retirement active
catalog and a resumable retirement manifest. It is not a publication source.
The procedure takes the station operation lock, recovers pending updates,
copies/checksums all retiring files, atomically removes their active catalog
entries, and only then removes checksum-matching originals. Retrying resumes
this sequence. Restore only under the same lock after scientific review: copy
back the verified files and merge their entries into the then-current catalog;
never replace the current catalog with its historical backup.

This retirement targets only combinations other than sensor 3/monthly and
sensor 82/daily. Valid-combination stations outside the approved lists remain
active but are still ineligible for update/publication. Other providers and
CDEC FNF are unaffected. Old pilot publications are historical test evidence,
not part of the active station collection and not removed by this operation.

The user's direct consultation with CDEC and supplied station lists supersede
the earlier instrument-classification assumptions below. Eligibility requires
all three: station membership, sensor code and duration:

| Instrument | Sensor | Duration | Listed stations |
| --- | --- | --- | ---: |
| Snow Course | 3 | monthly (M) | 260 eligible (261 supplied) |
| Snow Pillow | 82 | daily (D) | 131 |

There are 44 eligible shared station IDs (45 in the supplied lists before the
BKL course exclusion); these remain two distinct series. Other SWE
sensor/duration combinations are excluded from future routine/legacy submission
selection and new publication, and retained in the separate offline archive. The raw
`Sensors` column is not an instrument classifier. Reviewed source lists are
copied verbatim to `config/cdec_swe_stations/` from the user's
`/cw3e/mead/projects/cnt107/nrt_hydro/obs/cdec/site_list_snow_{course,pillow}.csv`.
`swe_policy.py` records their hashes in cycle reports and rejects resumes against
a different policy. No live job was running when this policy was changed.

Initial reconciliation found 391 of 392 eligible series in the canonical
catalog: all 131 pillows and 260 courses. BKL sensor 3/monthly is missing;
the archived BKL station metadata advertises only daily/hourly/event sensor 3.
The user verified on November 15 that no BKL sensor 3/monthly data exist.
Policy revision `cdec-confirmed-20261115-r2` therefore excludes that exact series
while retaining the original supplied list as provenance. BKL sensor 82/daily
remains eligible. All 391 remaining series are archived; the revised inventory
is `observations/status/cdec/swe/policy-20261115-r2.json`. The original
`policy-20261115.json` is historical evidence, not an outstanding backfill task.

The historical local Snow Pillow pilot is
`web/publications/observations/cdec-swe-pilot-20261115-v4/`, using sensor 82/daily
only. V1–V3 are historical test packages, not the current classification policy;
the hosted V3 is not changed by this local rebuild. Monthly course publication
requires a separate native-monthly product, not averaging daily pillow records.
Provider daily calculation/timezone uncertainties remain unchanged.

### Two-layer publication pilot

Uninterpreted sensor/duration combinations remain in the canonical archive, but
are not eligible for publication. The local V5 package is
`web/publications/observations/cdec-swe-two-layers-20261115-v5/`.
Its `catalog.json` is a small pilot index pointing to two independent
`collection.json` documents, not a finalized generic web-map catalog schema:

- `snow_pillow/`: sensor 82/daily, BCH/CAP/BCB, 2,193 records in six CSVs.
- `snow_course/`: sensor 3/monthly, 3LK/CAP/BCB, 36 records in six CSVs.

Both use WY2024–WY2025. CAP and BCB occur independently in both layers; station
IDs may match, but series IDs include sensor/duration and records are never
merged across instruments. Each layer owns its metadata, GeoJSON, assets and
acceptance report. Monthly records are native course survey/report values, not
monthly means, sums or reductions of daily pillow records. Missing months are
not zero-filled; monthly coverage reports observed months rather than a daily
completeness metric. Reporting labels remain the plotting coordinate, with
actual observation labels retained separately and no inferred interval bounds.

Build with `sbatch slurm/build_web_station_pilot.sh --two-layers --output
web/publications/observations/<new-release>`. Job 5322179 completed the V5 pilot;
CSV readback, package checksums and instrument eligibility checks passed. This
does not publish the package remotely or enable routine observation publication.
Old local/hosted pilots are historical tests, not permission to publish other
sensor/duration combinations operationally.

V6 (`web/publications/observations/cdec-swe-two-layers-20261115-v6/`) addresses
the V5 web review without changing CSV records: the top-level `releaseId` is
propagated to both collections, and each `revision`/`geometry.datasetVersion`
is `<releaseId>:<instrument-layer>`. Collection IDs remain stable. Thus releases
are distinguishable without requiring historical remote files. Layer READMEs
describe only their respective daily pillow or monthly course semantics.
The range contract remains `minimum`/`maximum` (not `min`/`max`); a null maximum
means no upper cutoff. The web reader should use those published field names.
Values remain already converted/masked, so no second inches-to-mm decoding is
needed. This repository change does not modify the separate web-map reader.

### Full-scale publication preparation

`bin/build_web_station_pilot.py --two-layers --full-history --output <new-release>`
extends the V6 contract to all 131 pillows and 260 courses. Run on SLURM, not a
login node. It takes the station operation lock and recovers pending transactions
before reading, preventing mixed update snapshots. Full-history mode now writes
one `data/<station>/sensor-<code>/history.csv.gz` per station per layer, including
partial first/latest years (bounded pilots still use water-year files). This
reduces 27,232 annual CSVs to 391 station CSVs. Partition bounds describe selected
reporting labels, not measurement intervals. Changed stations can be replaced
individually in future updates. Per-layer station
and record totals must match the eligible active catalog. Unknown coordinates,
empty histories or missing listed series fail closed for review rather than
silently dropping stations. Scientific limitations and display rules are unchanged.

Initial job 5322430 builds
`web/publications/observations/cdec-swe-full-history-20261115-v1/`, following
successful offline retirement job 5322414. Require top-level acceptance before
transfer and HTTPS byte verification afterward. Submission alone is not accepted
publication. No cron publication or automatic removal of old hosted pilots is
enabled by this build.

Consolidated full-history release job 5322520 writes
`web/publications/observations/cdec-swe-full-history-20261115-v2/`. The annual-file
V1 remains unchanged pending acceptance of this replacement. Asset inventory,
checksums, reference-case paths and per-layer acceptance are rebuilt together;
CSV readback must match original archive rows, and total records must match the
eligible canonical catalog. No aggregation or scientific value changes occur.

Both instruments now use deterministic gzip CSV encoding (level 6, `mtime=0`).
Assets retain decoded `mediaType: text/csv` and declare `fileCompression: gzip`;
their bytes/SHA-256 refer to compressed content. Reference records point to the
`.csv.gz` asset. JSON, GeoJSON and small evidence CSVs remain uncompressed.
This is file-level compression, not HTTP Content-Encoding: the web reader must
decompress before CSV parsing. Routine builders use the same encoding.

`bin/gzip_web_station_release.py` can repackage an accepted plain-CSV release
without rereading NetCDF or changing records. It verifies source checksums and
exact decompressed-byte equality, then rebuilds inventories and acceptance.
Job 5323061 produces `web/publications/observations/cdec-swe-full-history-20261115-v3-gzip/`
from V2, preserving the uncompressed release. This local operation does not
transfer the package or enable cron publication.

The smaller-metadata V4-gzip release
(`web/publications/observations/cdec-swe-full-history-20261115-v4-gzip/`, job
5323098) omits per-asset `reportedMonths` arrays. First/last reporting timestamps,
record counts, status counts and seasonal-sampling caveats remain; readers can
obtain exact month availability from the CSV itself. New builds use this policy.
Gzip encoding and CSV values are unchanged. Web-map integration must accept
`fileCompression: gzip` and preserve `.csv.gz` when offering compressed downloads;
only explicitly decompressed downloads should be named `.csv`. These reader
changes belong to the separate web-map project, not this exporter.

### First full public snapshot

The user-authorized production snapshot is
`web/publications/observations/cdec-swe-20261115-r1/`, promoted from V4-gzip
without changing CSV bytes. Its public entry point is
`https://cw3e.ucsd.edu/hydro/hydro_ops/observations/cdec/swe/catalog.json`.
The destination is the corresponding `cdec/swe/` folder under the configured
Globus observations root (filesystem `wrf_hydro`, HTTPS alias `hydro`). No `pilots/`
component is used. The index schema is `hydro-ops-observation-index-v1`;
collection IDs and the two-layer asset structure are preserved. Local releases
are immutable, while the remote paths hold one current snapshot, updated
data-first, collection metadata next, catalog last.

Transfer receipts and HTTPS checks are saved in
`web/work/observations/publish-cdec-swe-20261115-r1/`. Require successful transfer
states and the HTTPS acceptance report before treating the snapshot as deployed.
The initial upload does not activate scheduled publication, delete historical
pilots, or change model raster publication. Globus notifications are failure-only.

Future full-history exports use the same non-pilot index schema and
`hydro-ops-cdec-swe-v1-gzip6` processing version automatically. The historical
command name `bin/build_web_station_pilot.py` remains for compatibility: use
`--two-layers --full-history --output web/publications/observations/<new-release>`
for a complete local publication. Without `--full-history`, it is a bounded
pilot. Run full exports through SLURM. No manual metadata promotion is needed;
transfer and public HTTPS verification remain separate acceptance steps.

After the [common station collection](station_collection_operations.md)
activation gate passes, `bin/submit_cdec_swe.py` routes routine updates to it.
Legacy acquisition/backfill descriptions below document retained bootstrap data;
direct legacy writes are disabled. Metadata discovery and sensor/time semantics
are retained as history below. No cron installation is implied by cutover.

This inventory discovers **all stations exposed by CDEC for sensor codes 3 and
82**, across reporting durations and station status, before acquiring any SWE
time series. There is no model-domain or geographic filter. The source is the
[official CDEC station search](https://cdec.water.ca.gov/dynamicapp/staSearch)
and each result's `staMeta?station_id=...` page.

## Sensor codes versus measurement method

CDEC labels sensor **3** `SNOW, WATER CONTENT` (search abbreviation `SNOW WC`),
and sensor **82** `SNOW, WATER CONTENT(REVISED)` (search abbreviation `SNO ADJ`).
These codes do **not** distinguish snow course from snow pillow. For example,
3LK lists both codes as monthly manual-entry records, while BKL lists code 3
as event, hourly and daily and code 82 as daily. Provider descriptions,
durations, collection methods, station names and historical notes are retained
to support a later instrument classification. Do not silently rename the
sensor-code lists as course/pillow inventories.

### Web publication classification and time conventions

For the observations web pilot, distinguish the measurement instrument from the
provider's product code. Code 3 is SWE and code 82 is revised SWE. Both can occur
at one station; retain separate series identities. DWR's
[current station metadata](https://data.ca.gov/@california-department-of-water-resources/california-snow-data/r/3f3bc279-1265-44fa-9fd4-0e85b1f5f37f)
lists automated snow-sensor stations, while the
[historical list](https://cdec.water.ca.gov/misc/SnowSensors.html) is dated July 18,
2019. Neither listing alone certifies pillow hardware throughout a station's
history. Missing membership is not proof of a snow course or invalid observations.

`bin/audit_cdec_swe_sensor_types.py` cross-references the active daily SWE catalog
against both lists and retains provider downloads with checksums. Direct pillow
references in archived station notes are recorded separately. The small reviewed
note mapping is intentionally conservative and not an exhaustive classifier;
comparisons with other stations' pillows do not establish local instrument type.
The November 14 inventory has 284 daily series at 142 stations: 129 match the
current list and 130 the historical list. Six have reviewed direct pillow
references: BCH, BFL, FRW, GKS, HUL and MHP. Historical equipment/failure notes
establish an instrument association, not current serviceability or data quality.
Use these as pilot candidates only after checking coverage and flags.

Declare fixed PST (UTC-08:00, no daylight-saving changes) explicitly, retaining
both provider timestamp and UTC equivalent plus observation timestamp where
different. Do not label a daily value as a UTC-day mean or assign daily interval
bounds merely from its date. Our archive preserves provider sampling labels;
the exact daily calculation should remain unspecified where not documented.
The web application chooses plotting/formatting without changing the declared
scientific time interpretation. No observation times are shifted to NWM daily
boundaries by this export.

Observations publication is a sibling of model rasters:
`https://cw3e.ucsd.edu/hydro/hydro_ops/observations/`, mapped to Globus destination
`/projects/website/mirror/htdocs/wrf_hydro/hydro_ops/observations`.
The server's `hydro` alias corresponds to `wrf_hydro` in its filesystem, not a
second directory. `config/web_globus_observations.json` records this
mapping with production transfers disabled. Use `cdec/swe/` below that root and keep the
canonical station NetCDF archive under `observations/processed/stations/v1/`.
Local publishable releases belong under `web/publications/observations/`;
audit/provenance work belongs under `web/work/observations/`. A station-specific
inventory builder is required; do not pass a station collection to the raster
inventory builder or combine its cleanup ownership with `nwm/`.
The isolated two-file Globus probe passed transfer and public HTTPS SHA-256
verification on November 14, 2026. Its plan is
`web/work/globus-tests/20261114T232448Z-169dd7ce57b5/plan.json`; only the isolated
`_hydro_ops_transfer_test/` probe was transferred, not station data. No observation
publication cron hook is enabled.

### Local SWE pilot

`bin/build_web_station_pilot.py --output web/publications/observations/<new-release>`
exports BCH, GKS and MHP, sensors 3 and 82 separately, for water years 2024–2025.
Run via `slurm/build_web_station_pilot.sh` to keep full-history archive reads off
the login node. This deliberately scoped pilot requires the reviewed November 14
sensor audit, pins source checksums against the catalog, validates NetCDF records,
and verifies CSV roundtrip equality. It does not refresh the canonical archive.

The accepted local release is
`web/publications/observations/cdec-swe-pilot-20261114-v1/`: job 5310893 completed
with 12 CSV assets and 4,386 records, about 1 MB including metadata. All expected
date labels exist; this is not a claim that all values are nonmissing or good.
`collection.json` declares time, units, flags and asset coverage;
`features.geojson` supplies station points; `reference-records.json` supplies
sample records; `acceptance.json` pins exported file checksums.

Original fields remain unchanged. `swe_display_mm` masks missing/sentinel and
negative SWE, preserves zero, and applies no upper cutoff. Provider flags remain
verbatim and uninterpreted, not assumed reviewed. Coordinates are provider
coordinates without a datum transformation; historic relocations are unresolved.
The format follows the web project's draft feature-time-series guidance, not a
claim of an already implemented browser adapter. Local acceptance establishes
faithful export only; scientific review, browser integration and station-data
HTTPS publication remain separate gates.

The web-review refinements are in
`web/publications/observations/cdec-swe-pilot-20261114-v2-ready/`.
This is the v2 handoff package; v1 remains unchanged. CSV values and timestamps
are unchanged. V2 explicitly selects reporting time for plotting (not an
acquisition-time claim), supplies identity packing for already-converted display
SWE, structured producer validation statuses, stable station/sensor series IDs,
numeric elevation in provider feet with unknown vertical datum, source-receipt
retrieval times, and a bundled checksum-identifiable sensor audit. README.txt is
the upload guide; upload the complete package preserving relative paths.

CDEC's [flag dictionary](https://cdec.water.ca.gov/reportapp/javareports?name=FlagList),
checked November 14, defines the observed r/e/o flags as revised/estimated/
calibration-offset-applied. Blank (including the literal space token) means no
flag, not necessarily good quality. These flags do not exclude a record;
unknown flags are retained and identified as unrecognized. The legacy producer
status token is retained for compatibility and is explicitly separate from
provider definitions. Reference records now cover each observed flag, differing
reporting/observation dates, leap day and both water-year boundary dates.

Two scientific questions remain open: the exact daily SWE calculation and a
definitive provider no-DST statement for these daily date fields. V2 declares
fixed PST as the existing archive normalization, not newly verified provider
semantics; it does not infer daily interval bounds. The
[DWR dataset description](https://data.ca.gov/@california-department-of-water-resources/california-snow-data)
describes automated sampling and daily products, but does not resolve that daily
calculation. Neither uncertainty is silently converted into a scientific claim.

V3 (`web/publications/observations/cdec-swe-pilot-20261114-v3/`) adds explicit
series-key bindings: provider is the inherited constant `cdec`, duration is `D`
and must match the CSV, and station/sensor fields bind to their respective series
attributes. Corrections create new immutable local releases rather than editing
accepted releases; upload assets before exposing the entry manifest. A future
latest URL would be a mutable pointer, not an immutable release. This does not
require retaining historical releases on the storage-limited web server.

Every reference case now includes expected plot coordinate, reporting calendar
date, mm/inch values, exclusion reason and gap behavior. Readers must preserve
the reporting date instead of shifting it into the browser timezone. Hourly
alignment, acquisition-time interpretation and interval calculations still
require provider evidence. No station publication or cron integration is enabled
by this pilot refinement.

Snow water content/equivalent is also distinct from snow depth. No depth sensor
or other measurement code is included by inference, and no time-series data is
downloaded by this collection step. Units and advertised availability are
recorded exactly as provided; availability is not a verified inventory of
nonmissing SWE observations.

## Discovery and outputs

`bin/collect_cdec_swe_metadata.py` makes unrestricted, active-only,
discontinued-only, event, hourly, daily and monthly searches for each sensor.
It uses the union of these results, preserves every search response, and checks
that each station's metadata actually contains every sensor for which it was
discovered. An empty duration/status search is allowed only when a valid empty
station result table is present; error pages and unexpected pagination fail
closed. Any missing station page or sensor mismatch is reported as a failure,
not silently dropped from a successful list.

Each combined station is fetched once. Raw HTML is compressed and SHA-256
checksummed, with source URLs and retrieval times. Native station attributes,
all sensor rows and historical notes are retained. Missing/invalid coordinates
are explicitly recorded as null with issue fields so historical stations are
not discarded solely for incomplete geolocation; original provider fields
remain available. GeoJSON uses null geometry for such stations.

```text
observations/static/stations/cdec/swe/<UTC timestamp>/
  discovery.json                original station IDs and provenance per search
  stations.json                 complete combined metadata and collection status
  stations.csv                  one row per successfully collected station
  stations.geojson              station points for GIS inspection
  sensor_3_stations.csv          one row per station carrying sensor 3
  sensor_82_stations.csv         one row per station carrying sensor 82
  sensor_3_series.csv            one row per sensor-3 reporting duration
  sensor_82_series.csv           one row per sensor-82 reporting duration
observations/inputs/cdec/swe_station_metadata/<UTC timestamp>/
  search-<sensor>-<filter>.html.gz
  stations/<station>.html.gz
  ...source.json                source URL, retrieval time and checksum
observations/status/cdec/swe-metadata/job-JOB/status.json
```

SWE metadata snapshots are nested separately from FNF snapshots so the FNF
updater cannot accidentally select a SWE inventory as its configured 24-station
metadata. Downloaded/generated inventories remain ignored by Git.

## Execution and acceptance

Job **5042947** was submitted October 28, 2026 via
`slurm/collect_cdec_swe_metadata.sh`: four allocated CPUs, two paced HTTP workers,
four-hour limit and ordinary shared resources. Parser/discovery tests and Ruff
checks passed before submission. Station-wide crawling runs on a compute node.
Other observation and model campaigns are not resubmitted by this command.

The snapshot and job-specific report must say `passed`, with no failures and
collected-station count matching discovery, before treating the lists as
complete. Intermediate CSVs are progress snapshots. Source lists and metadata
are retained even on failures. Resume a failed snapshot after inspecting the
report:

```bash
source bin/project_environment.sh
sbatch slurm/collect_cdec_swe_metadata.sh --resume \
  observations/static/stations/cdec/swe/<UTC timestamp>
```

Resuming reuses checksum-verified cached raw responses and retries absent pages;
an exclusive snapshot lock prevents competing resume writers. Starting without
`--resume` discovers a new independent snapshot. No SWE data backfill or new
cron entry is launched at this metadata stage.

## Completed inventory: October 28, 2026

Job 5042947 passed in **1 minute 41 seconds**. Snapshot
`observations/static/stations/cdec/swe/20261028T075431Z/` contains all **496**
discovered unique stations, with no failures, complete valid coordinates and
330 retained historical notes. All 484 sensor-82 stations are also sensor-3
stations.

| Code | Stations | Monthly series | Daily series | Hourly series | Event series |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3 | 496 | 408 | 142 | 146 | 83 |
| 82 | 484 | 405 | 142 | 0 | 4 |

These counts describe listed station/sensor/duration combinations, not verified
nonmissing SWE observations. All 1,330 listed series use provider units INCHES.
Active and discontinued search memberships can overlap at a station with
different reporting durations or sensor histories; do not sum those searches
as mutually exclusive station classes.

The final local audit checked all 510 raw-response SHA-256 values, exact
agreement between discovery and station metadata sensor membership, and both
station/series CSV row counts. Results are saved in `audit.json` beside the
inventory. The full regression suite passed (546 passed, two skipped), as did
Ruff and diff checks. This snapshot completed the metadata-only stage; the
subsequently authorized SWE acquisition is described below.

## Historical bootstrap acquisition and daily updates (superseded)

The following records the original multi-duration acquisition, not current
operational instructions. Use the current collection policy above and the
[station collection operations](station_collection_operations.md) for updates.
Hourly/event SWE and unapproved sensor/duration pairs are no longer refreshed
or published; their retained records are archived separately.

The user subsequently authorized acquisition and daily updates for both sensor
codes. `bin/submit_cdec_swe.py` plans work from the latest passed SWE inventory,
pins its SHA-256, and submits ordinary-resource jobs through
`slurm/update_cdec_swe.sh`. It suppresses competing campaigns under an exclusive
submission lock. Data workers also lock each station/sensor/duration directory.
The FNF metadata and acquisition workflow remain separate.

All **1,330** station/sensor/duration series are included at their native
sampling: monthly, daily, hourly and event. Codes 3 and 82 are stored separately.
No instrument type is inferred from code or mixed into the other product.
Initial planning produced **6,414 requests**: 813 monthly histories, 284 daily
histories, 4,483 calendar-year hourly chunks and 834 calendar-year event chunks.
The historical metadata end date is honored for discontinued series; open-ended
series run through today's PST date. Five monthly records have no provider
start date. Those use an explicit **1800-01-01 request floor**, identified in
the plan as `explicit_request_floor_missing_provider_start`; this is not an
inferred first observation date. Actual returned coverage is reported separately.

### Requests and time semantics

Every request has explicit bounds. CDEC's blank Start returned only a short
window in the earlier FNF test. Date-only bounds also showed a one-hour summer
shift for hourly/event queries: a request starting October 26 returned a
record labeled October 25 at 23:00. The downloader therefore pads the query on
both sides and crops using the returned timestamps. End padding includes the
last day's complete records and prevents gaps at annual file boundaries.
Unexpected dates outside the padded envelope fail validation.

As documented by the [official CDEC web service](https://cdec.water.ca.gov/dynamicapp/wsSensorData),
output timestamps are treated as fixed PST (UTC−8), retaining provider date and
observation-date strings and adding UTC equivalents. Missing observation dates
remain blank. Monthly course dates and event sampling are retained; no regular
measurement cadence, averaging interval, or NWM UTC day alignment is inferred.

### Storage and processing

```text
observations/inputs/cdec/swe/sensor_<3|82>/<resolution>/<station>/<sha256>.json.gz
observations/processed/cdec/swe/sensor_<3|82>/monthly/<station>/history.csv.gz
observations/processed/cdec/swe/sensor_<3|82>/<daily|hourly|event>/<station>/YYYY.csv.gz
observations/status/cdec/swe/chunks/<station>/<sensor>/<duration>/<request-id>.json
observations/status/cdec/swe/<mode>-<UTC timestamp>/{plan,status,submission}.json
observations/status/cdec/swe/<mode>-<UTC timestamp>/shard-<index>.json
```

Sparse monthly histories use one compressed file each to avoid tens of thousands
of tiny annual files. Higher-frequency records use annual compressed CSVs.
CSV fields preserve station, sensor, duration, source dates, source values and
quality flags, with `value_inches`, `swe_mm` and `value_status` added. Conversion
is **1 inch = 25.4 mm SWE**; monthly SWE is a sampled state, not a monthly sum.
Zeros are retained. Provider `-9999` is recorded as missing in numeric columns
with the original raw value retained. Other negative numeric SWE values remain
present with `negative_swe` status; no clipping, interpolation, replacement of
code 3 with code 82, or quality-flag filtering is performed.

Each chunk validates response identities, units, date bounds, and conflicting
duplicate timestamps. Raw payloads are compressed, reopened and SHA-256 checked.
Processed CSVs are atomically published, reopened, and checked exactly against
normalized source rows. Changed windows merge into existing files without
removing records outside the window. If the server omits previously published
timestamps inside a refreshed interval, publication fails for review; returned
explicit missing values can replace earlier numeric ones. Empty/all-missing
responses are retained and reported, not claimed as numeric observation
coverage. Snow-course series are sparse, so missing calendar dates are not
filled or interpreted as telemetry failures.

Accepted chunk receipts allow retrying the historical backfill without
redownloading unchanged accepted chunks. `--refresh` requests a deliberate
historical recheck. The finalizer verifies task coverage and all referenced
receipts/file identities before publishing `latest-backfill.json` or
`latest-update.json`. Per-chunk verification establishes faithful processing,
not scientific approval of individual observations or their flags.

### Submission, recurrence and gates

```bash
source bin/project_environment.sh
# Seven code/duration samples, before historical acquisition.
bash bin/run_cron.sh bin/submit_cdec_swe.py --mode smoke
# Initial history or restart after failures (accepted chunks are reused).
bash bin/run_cron.sh bin/submit_cdec_swe.py --mode backfill
# Daily update; this is the cron command after initial acceptance.
bash bin/run_cron.sh bin/submit_cdec_swe.py
# Deliberate historical revision scan, at an operator-chosen interval.
bash bin/run_cron.sh bin/submit_cdec_swe.py --mode backfill --refresh
```

Use `--dry-run` to inspect a plan without submission; active campaigns cause a
skip. Matching metadata smoke acceptance is required before backfill. Daily
updates require accepted initial backfill for that inventory. A new station
inventory therefore requires new smoke/backfill acceptance rather than silently
starting unknown histories from a short lookback.

Initial backfill uses eight array tasks with concurrency **two**, four allocated
CPUs and two workers per task: at most **eight allocated CPUs/four request
workers** during acquisition. Tasks request 24 hours; single update jobs request
four hours. All run on ordinary shared resources without NRTRES. A separate
`afterany` finalizer records failure if an array task fails or is incomplete;
it never treats partial execution as accepted history.

The daily update policy is:

- Monthly: re-fetch the full history, capturing older revisions.
- Daily: refresh roughly **400 days**.
- Hourly/event: refresh roughly **seven days**.
- After missed runs: extend the window to the last checked date so no unchecked
  new dates are skipped. Older daily/hourly/event revisions require a deliberate
  full-history refresh, and are not claimed to be captured by the rolling cycle.

The saved cron and template submit updates at **19:15 UTC daily (11:15 PST)**,
15 minutes after FNF acquisition. This is a practical schedule, not a promised
CDEC release time. The cron command skips while smoke/backfill/update/finalizer
jobs are active. Keep installation on login1 only; the saved additions require
manual reinstallation and have **not** changed the installed user crontab.

### Initial jobs and tests

The first smoke job 5043070 exposed the summer-boundary issue; its two monthly
sample CSVs were moved into that failed campaign directory when switching the
monthly layout, preserving the test evidence. Corrected smoke **5043076** passed
all seven code/duration checks, including sparse/empty response handling.
The full regression suite passed (**551 passed, two skipped**); targeted unit
tests also cover exact SI conversion, sentinels/flags, missing observation
dates, next-day trimming, annual chunks, outage catch-up, merged-window
preservation, duplicate/history-loss guards and unchanged-file reuse.

Backfill array **5043120** and finalizer **5043123** were submitted October 28,
2026. Campaign: `observations/status/cdec/swe/backfill-20261028T081734Z/`.
Submission is not completed history: require final `status.json` to pass and
inspect per-series/chunk numeric coverage. Clean-environment submission dry run,
Ruff, shell syntax and diff checks passed before bulk submission.
