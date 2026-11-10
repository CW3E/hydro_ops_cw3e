# Forcing, NWM and observation project layout

For moving the entire project to another filesystem location or cluster, see
[project relocation](project_relocation.md). That is separate from the internal
temporal-resolution directory migration described below.

The repository operates two coupled production systems. `forcing` owns acquisition and production
of meteorological forcing; `nwm` owns WRF-Hydro/NWM domains, runs, state, and model products. A
forcing product is immutable input to an NWM run and is referenced rather than copied.
`observations/` owns provider observations and station metadata shared across
all domains; model-specific comparisons live under `nwm/validation/`.

For the current station collection layout, activation and update commands, use
[station collection operations](station_collection_operations.md). The station
planning, pilot and staged-migration sections below retain the development
history; they do not override the active collection's routing switch or authorize
repeating migration/cutover commands.

```text
forcing/
  inputs/                         external NLDAS-2, HRRR, MRMS, Stage-IV, PRISM, and GFS archives
  static/                         source grids, elevations, and reusable remapping weights
  outputs/
    conus/{baseline,nrt,retro}/hourly/  calendar-day collections of hourly LDASIN
    conus/{nrt,retro}/{daily,monthly}/  summary products (not model input)
    validation/                   forcing validation products
  work/                           manifests, task lists, locks, and temporary coordination state
  logs/                           forcing acquisition and production logs
  status/                         machine-readable operational status

nwm/
  static/                         versioned domains, hydrofabric, and parameters
  inputs/                         model-specific dynamic inputs; shared observations are referenced
  runs/<domain>/                  experiment and cycle workspaces; large transient work uses scratch
  outputs/<domain>/<stream>/      published model history products
  restarts/<domain>/<stream>/YYYY/MM/
                                  paired, verified 00 UTC land and routing checkpoints
  logs/                           WRF-Hydro/NWM logs
  status/                         domain inventories and model-operation status

observations/
  static/stations/<provider>/     station and sensor metadata, source notes
  static/domains/                 station-to-domain/reach mappings when validated
  inputs/<provider>/              preserved source responses and revisions
  processed/<provider>/           standardized records, units and quality flags
  status/                         acquisition plans, coverage and update reports
  logs/                           acquisition/submission logs
  work/                           temporary observation processing state
```

For CDEC full-natural-flow records and daily cron acquisition, see
[CDEC observations](cdec_observations.md). Store station records once by provider,
resolution, station and year rather than duplicating them per model domain.
The [USGS streamflow inventory](usgs_streamflow_observations.md) similarly keeps
one CONUS station/series snapshot and derives reporting-region lists for the
four subdomains without duplicating observation records.

### CNRFC model publication

The [expanded-grid promotion](cnrfc_grid_promotion.md) makes the validated HU2-18
grid canonical `cnrfc`, with flat resolution/year/month publication directories.
Run/status campaigns retain their names; published forcing/output/restart paths
do not require a `cnrfc_hu18_v1` alias or temporary campaign nesting.

Validated CNRFC retrospective model products use these canonical destinations:

```text
nwm/outputs/cnrfc/retro/hourly/YYYY/MM/YYYYMMDD.CHRTOUT_DOMAIN1
nwm/outputs/cnrfc/retro/daily/YYYY/MM/YYYYMMDD.CHRTOUT_DOMAIN1.daily
nwm/outputs/cnrfc/retro/daily/YYYY/MM/YYYYMMDD.LDASOUT_DOMAIN1.daily
nwm/restarts/cnrfc/retro/YYYY/MM/
```

`recovery_record_stack_v1` is a recovery campaign identifier, not a permanent
publication level. Run records and logs retain that identifier for provenance.
The promotion audit/journal is `nwm/status/cnrfc/retro-promotion/`; its
`promotion.json` must say `complete` before the recovery runner uses these
canonical destinations. Earlier campaign/test directories are historical, not
the current published record. CONUS remains in its active recovery campaign
layout until it is separately audited and promoted.

The forcing domain name describes the output grid, not the source-product domains. The current
full NWM grid is `conus`; future subset products use stable lowercase identifiers such as `croton`
or `mid_atlantic`. `nrt` and `retro` remain separate in both systems. NWM run manifests record the
forcing domain, stream, date range, static-domain version, executable version, namelist checksums,
and exact input/output restart pairs.

Historical JSON and JSONL manifests were rewritten to canonical paths after migration. The former
compatibility symlinks below `data/`, `outputs/`, `work`, and `logs` have been removed; these names
must not be used as production inputs or destinations.

Configuration, command-line defaults, Python entry points, SLURM wrappers, cron, and generated
manifests use canonical paths directly. `bin/migrate_project_layout.py` still names the former roots
as one-time migration sources, but compatibility-link creation is disabled unless explicitly
requested with `--create-compatibility-links`. `bin/rewrite_manifest_paths.py` provides an
idempotent, atomic dry-run/execute migration for structured historical manifests.

## Shared station-observation framework

The permanent collection and routine update routing are documented in
[station collection operations](station_collection_operations.md). After its
activation gate passes, use `observations/processed/stations/v1/catalog.json`.
Older provider-specific CSV archives become retained bootstrap/rollback evidence.
Pilot and migration sections below describe historical validation stages, not
alternate operational archives. Cutover does not delete old data.

At the initial planning stage, only an isolated storage pilot existed and the
provider-specific readers, downloaders, paths and cron jobs were unchanged.
That stage has since been superseded by the collection operations linked above.
The shared implementation lives under `src/hydro_ops/observations/`, with
provider adapters retaining acquisition and interpretation rules. SNOTEL is a
future adapter; its service, station identifiers and time semantics must be
verified before implementation. No SNOTEL acquisition is configured by this plan.

### Identity, records and storage

- Identify a station by provider plus the provider's original station identifier
  (stored as text, preserving leading zeros). Identify a series additionally by
  product, sensor/parameter, duration and provider series identifier. Do not
  merge distinct series merely because their station and dates match.
- Store records once, shared across domains. Keep domain selections and NWM
  reach associations in separate, versioned mapping tables rather than making
  copies of station histories for each model domain.
- Target one compressed NetCDF file per station/product/sensor/resolution for
  daily and monthly histories. Multiple provider series require explicit series
  identities, not an arbitrary winner. Retain annual partitions for higher-rate
  histories initially; benchmark updates before changing that choice.
- Preserve original values, units, flags, provider date labels, observation and
  revision timestamps where supplied, retrieval time, source identity and missing
  reasons. Store normalized numeric values and SI units alongside originals.
  Distinguish a missing record, an explicit missing value and numeric zero.
- Represent time-zone and interval semantics explicitly. Unknown bounds remain
  unknown: never infer UTC-day means from date labels. Keep CDEC fixed-PST labels,
  FNF monthly volume, SWE sampled state and USGS series qualifiers distinct.
- Embed series metadata and compact completeness diagnostics in the data file.
  Use collection-wide catalogs for discovery, coverage, file identities, schema
  versions and mappings. Coverage must distinguish requested, returned and
  nonmissing observations; station presence does not imply current reporting.
- Bundle immutable raw responses and acquisition provenance with a member index
  and checksums. Bundle rotation and random access must be tested before replacing
  loose raw files; retain exact response bytes and source-to-record traceability.

### Shared lifecycle and safety

The common lifecycle is discover/plan, acquire, normalize, validate, publish and
report. Adapters own service pagination, pacing/retries, revision lookbacks and
provider-specific semantics. Shared code owns schema validation, storage,
checksums, atomic publication, catalog generation and coverage reporting.

Keep operational locks and run reports outside the scientific archive. Stable
lock identities must be preserved while writers may be active; moving or deleting
a lock during a run can bypass duplicate-writer protection. Avoid one mutable
catalog written concurrently by every worker: collect worker receipts and publish
the collection catalog through a serialized finalizer.

Publish validated replacement station files atomically, then update the catalog.
Readers must verify catalog/file identities; a retry must reconcile a crash
between those steps. Preserve existing history-loss checks, explicit revision
handling, unchanged-data no-op behavior and bounded retries. A stale catalog or
failed acquisition must not be reported as a complete collection.

### Implementation and migration gates

1. Define the versioned schema and adapter/storage interfaces, with synthetic
   tests for multiple series, leading-zero IDs, missing values, flags, irregular
   observations and ambiguous time bounds.
2. Pilot CDEC FNF and a small USGS cohort into a separate staging destination.
   Compare every source record and metadata field, including raw provenance.
3. Test changed/unchanged refreshes, historical revisions, history-loss rejection,
   interrupted publication/retry and concurrent writer exclusion. Benchmark file
   counts, storage size, read speed and update cost.
4. Adapt readers/status and update commands before migrating accepted archives.
   Keep old files until replacements pass an explicit inventory and audit; deletion
   requires a separately approved plan. Do not install or enable cron as part of
   storage migration.
5. Extend the proven framework to CDEC SWE and subsequently SNOTEL. The common
   interface must not impose flow-specific assumptions on snow observations.

### Initial station-storage pilot

`hydro_ops.observations.station_store` defines `StationTable`, `write_station`
and `read_station`. Schema `hydro_ops_station_table_v1` stores record-indexed
`value_si`, series IDs, provider period labels and value-status fields. All
original CSV columns are also retained exactly as strings, including whitespace,
flags, raw missing sentinels and original numeric text. Compressed UTF-8 byte
arrays with explicit lengths avoid truncating Unicode or embedded NULs.
The supplied reader decodes these arrays; this pilot does not yet claim CF
time-series compliance or a drop-in replacement for existing CSV consumers.

Compressed embedded JSON retains provider metadata, diagnostics, acquisition
receipts and source-file checksums. USGS dates remain date labels; no UTC-midnight
timestamp or unverified time bounds are invented. CDEC daily FNF is discharge
in m3/s and monthly FNF is volume in m3. SWE and SNOTEL adapters are not implemented.
Existing raw responses remain external and are not deleted or bundled by this pilot.

Writes are full station snapshots: duplicate series/period keys, changed station
semantics, dropped source columns and loss of existing keys are rejected. Explicit
missing values may replace older numeric values. A stable per-target lock in a
caller-supplied operational directory excludes concurrent writers. The writer
checksums the canonical payload, reopens and compares all records and metadata,
then atomically replaces the target. An identical snapshot is a no-op. Temporary
files left by an uncatchable process termination are not authoritative; only the
final filename is read. Date selection uses inclusive provider labels and does
not recompute embedded full-history diagnostics.

Read-only CSV adapters are in `hydro_ops.observations.station_adapters`. CDEC
conversion requires an explicit passed station-inventory snapshot; USGS conversion
checks available acceptance checksums. Both check accepted row counts and detect
source changes during assembly. These are processed-archive preservation audits,
not independent revalidation against provider APIs or raw-response contents.

`slurm/pilot_station_storage.sh` runs `bin/pilot_station_storage.py` over AMF daily
FNF, PSH daily FNF, TNL monthly FNF and USGS-02102500 daily discharge. It creates
four files and a collection-level `acceptance.json` under
`observations/work/station-storage-pilot/JOB/`; locks remain under that workspace.
No downloads, production-archive replacement, catalog migration or cron changes
are performed. The report records exact round-trip and unchanged-repeat checks,
input/output sizes, file counts and elapsed conversion/audit time. The input
size includes embedded metadata and the shared CDEC inventory, so it is not a
pure time-series compression-ratio benchmark.

Pilot **5211170** passed in 11 seconds (four CPUs): AMF daily 14,366 rows;
TNL monthly 1,381 rows; PSH daily 1,610 rows; USGS-02102500 daily 17,467 rows.
All exact round trips and unchanged repeats passed. Output sizes were respectively
706,226, 207,839, 210,740 and 821,276 bytes. The USGS file is larger than its
697,310-byte compressed-history/metadata input set, so consolidation is not a
universal storage-size saving. The TNL pilot replaces 116 annual data files plus
embedded manifest/station information with one NetCDF; the originals remain
untouched. Job-specific results are under the workspace described above.

```python
from hydro_ops.observations.station_store import read_station

table = read_station("station.nc", start="2020-01-01", end="2020-12-31")
records = table.normalized()  # common series_id, period_label, value_si, value_status
original_records = table.rows
metadata = table.metadata
```

Before any production migration, extend testing to additional real USGS series,
implement catalog/version discovery and update adapters, measure read/update
performance, and audit raw provenance. The prototype currently reads a complete
station history into memory; its design target is daily/monthly histories, not
unbounded high-frequency telemetry.

### Catalog and processed-provider update pilot

`hydro_ops.observations.station_catalog` adds a versioned JSON collection catalog.
An entry identifies provider, station, product, sensor and resolution, and stores
the relative NetCDF path, file and payload checksums, units, time semantics,
series count, returned/numeric record counts, status counts and first/last
returned/numeric period labels. Coverage refers only to observed records; no
completeness denominator or active-station claim is inferred. Multiple provider
series remain distinct within a file. Catalogs cover explicit member lists, not
all known provider stations.

The serialized finalizer holds the same per-target locks as station writers,
validates members, rejects duplicate identities and omitted existing members,
and atomically publishes the catalog. Unchanged finalization is a no-op.
`read_member` verifies member checksums before and after reading. If a station
update succeeds but the catalog finalizer is interrupted, old catalog readers
fail closed for that member until finalization is retried. This is recoverable
publication, not a single atomic transaction across the whole collection.

`station_adapters.sync_archive` reads a **complete accepted processed-provider
snapshot**, applies the shared station update checks, and restricts its target
to `observations/work`. It does not fetch APIs or merge partial download windows.
Synthetic CDEC-format update tests cover extension, historical revision, explicit
missing replacement, unchanged refresh and rejected history loss. Synthetic USGS
tests cover changed values/flags, additional series and rejection of mismatched
source acceptance checksums. These tests do not modify real provider archives.

Submit the expanded pilot with `slurm/pilot_station_storage.sh --extended`.
Its 13 histories add AMF/PSH monthly and KGF/SCC daily FNF, plus USGS stations
10254970 (multiple series), 09024000 (missing values), 09014050 (negative values),
09132095 (later record start), and 09074000 (ended record), selected from the
existing completeness inventory. Each adapter is executed twice to verify an
unchanged refresh. The finalizer builds and verifies `catalog.json`, repeats
without rewriting it, then tests catalog-based date selection. Original archives
remain untouched; neither acquisition nor cron schedules change.

Human-readable and JSON reports are available through:

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/report_station_catalog.py observations/work/station-storage-pilot/JOB/catalog.json
"$HYDRO_OPS_PYTHON" bin/report_station_catalog.py observations/work/station-storage-pilot/JOB/catalog.json --verify --json
```

Without `--verify`, this prints a catalog snapshot and explicitly does not claim
to have rechecked current member files. Relative member paths allow relocating
the collection as a unit. Large-collection checksum verification should run in
SLURM rather than on login nodes. Production adoption still needs partial-window
acquisition integration, raw-provenance auditing and a separately approved
migration/retirement plan; SWE and SNOTEL are future adapters.

Expanded pilot **5211281** passed in 42 seconds with four CPUs: 13 histories,
122,456 records, exact round trips, unchanged adapter refreshes, catalog checksum
verification and unchanged finalization. USGS-10254970 retained two series.
Catalog-based 2020 date selections across all members took 2.47 seconds, including
full-member checksum checks and reads (not indexed partial I/O). Results and the
catalog are in `observations/work/station-storage-pilot/5211281/`. The full test
suite passed 779 tests with four skips; Ruff, shell syntax and diff checks passed.

### Incremental acquisition pilot

`station_updates.merge_window` merges inclusive **provider date labels**, not UTC
model intervals. It preserves history outside the refresh window, accepts explicit
missing-value revisions, rejects omitted known series/date keys and unexpected
series/schema/identity changes, and refuses stale concurrent writes. No-op data
refreshes leave the NetCDF untouched. An empty response is not evidence that a
station has closed: it cannot erase known observations or establish completeness.

`bin/pilot_station_incremental.py` seeds isolated AMF daily FNF and
USGS-02102500 daily discharge histories, then downloads a short window using the
existing provider parsers. Raw responses, checksums, acquisition receipts and
publication signatures remain under `observations/work/station-incremental-pilot/JOB/`.
The USGS request uses the existing shared provider rate budget. Metadata inherited
from the original archive is explicitly bootstrap evidence, not current diagnostics.
The catalog supplies current record counts and coverage; acquisition receipts are
separate from the station payload so timestamps alone do not force rewrites.

The pilot checks preserved outside-window records, repeat-publication no-ops,
stale-catalog rejection and successful catalog rebuild. Job **5211400** passed
for October 20–November 7, 2026 with four CPUs in 16 seconds (13.53 seconds inside
the pilot). Both histories gained 11 records; AMF now contains 14,377 rows and
USGS-02102500 17,478. Both returned 19 records in the refresh window, preserved
outside-window history, passed catalog recovery and repeated without rewriting.
AMF's latest numeric label is November 6; USGS's is November 7. Missing values
are retained, not interpreted as completed numeric coverage. The job-specific
`acceptance.json` records passed status; a separate catalog checksum verification
also passed. The full suite passed **788 tests with four skips**; Ruff, shell
syntax and diff checks passed.
That initial stage tested live **daily** acquisition; the extension below adds
monthly FNF and native SWE. SNOTEL, operational retry orchestration and migration
remain follow-up work.
Production station archives and cron schedules are unchanged.

### Monthly FNF and SWE extension

`station_updates.acquire_window` adds monthly FNF (sensor 65, monthly volume in
m3) and SWE (sensors 3/82, mm), using the existing provider normalization code.
Monthly requests cover complete calendar months and are limited to 367 days;
other requests are limited to 32 days. Requests do not infer completeness from
the number of returned samples. `acquire_daily_window` retains its daily-only
contract for existing callers.

`station_swe.sync_swe_archive` stages SWE histories only after checking station
metadata, sensor/duration/units, accepted receipts' current file identities and
source stability during conversion. Each source file must match an accepted
receipt. A recent partial-update receipt alone is not sufficient evidence for a
complete historical conversion; yearly receipts are matched individually.

SWE uses `label_kind=fixed_pst_timestamp`: exact native timestamps, including
multiple samples within a month, are the observation keys. Fixed PST is UTC−08
year-round. The provider's monthly snow-course label **does not imply a monthly
mean or total**. Refresh bounds for SWE are inclusive PST dates; FNF monthly
merge bounds remain `YYYY-MM`. Source dates, observation dates, flags, missing
values and zero SWE are retained. No interpolation or resampling occurs.
Old bootstrap diagnostics are not presented as current completeness statistics.

The shared table validator accepts fixed-PST timestamp labels and includes the
label kind in replacement/merge semantic checks. Hourly/event timestamp storage
uses the same exact-timestamp keys; see the native-resolution pilot below.
Existing period-label FNF/USGS tables remain unchanged.

Live job **5211578** passed in **14 seconds** using four CPUs, testing AMF monthly FNF
(September–October 2026), 3LK monthly snow-course SWE (March–May 2026), and ADM
daily snow-pillow SWE (October 20–November 7). Its isolated report is
`observations/work/station-cdec-products-pilot/5211578/acceptance.json`.
AMF's two returned monthly records refreshed the existing 1,513-row history;
3LK's three returned samples matched its existing 396-row history without a
rewrite; ADM gained 10 records (15,642 to 15,652 rows). Lossless archive
conversion, preserved outside-window observations, unchanged repeat refreshes
and final catalog verification all passed. These are three representative
histories, not a provider-wide validation. Production archives and cron remain
untouched.

### Hourly/event SWE pilot

The SWE adapter now supports hourly and event archives. CDEC requests for these
series were split into yearly chunks; the cursor's last receipt may describe an
older year that happened to finish last. Conversion scans only the selected
station/sensor/duration receipt directory and requires **every current source
file** to match an accepted receipt's inode, size and modification time. Selected
receipts and source CSVs are SHA-256 recorded and rechecked during conversion.
The latest cursor alone is never treated as proof of full-history acceptance.
Files with no current matching receipt fail closed. Empty archives do not become
successful data conversions or completeness claims.

Run `slurm/pilot_station_cdec_products.sh --native` for ADM sensor-3 hourly and
AGP sensor-3 event histories, refreshing October 25–27, 2026 in isolation. All
native samples are retained; events are not averaged into hourly bins. The
pilot verifies exact archive readback, preservation outside the refresh dates,
repeat-publication no-ops and final catalog checksums. It records per-station
timing and NetCDF size to assess the cost of full-history station updates.
Job **5211645** passed in **2 minutes 24 seconds**, with eight CPUs allocated
and approximately 1.15 GiB peak resident memory. Its report is
`observations/work/station-cdec-products-pilot/5211645/acceptance.json`.
ADM preserved 239,543 hourly records; its 72 fetched records matched exactly
(70.34 seconds for conversion and refresh checks; 5,252,159-byte NetCDF).
AGP preserved 210,691 event records; its 196 fetched records matched exactly
(59.09 seconds; 3,491,983-byte NetCDF). Both no-op refreshes and the combined
catalog verification passed. These timings include several full-history reads
for pilot audits, not just API acquisition; the eight allocated CPUs are not
eight concurrent station workers. The inspected sensor-82 event directories contain
no CSV histories, so they are not counted as live conversion tests.

Synthetic tests cover separate yearly receipts, a cursor pointing to an older
year, subhourly timestamp preservation, and rejection when a year's evidence is
missing. No production archive migration, deletion or cron changes occur here.
The full suite passed 796 tests with four skips; Ruff, shell syntax and diff
checks passed. Live acceptance is recorded separately from these code checks.

### Migration inventory and bounded conversion design

`slurm/plan_station_migration.sh` runs `bin/plan_station_migration.py` on a compute
node. It discovers existing USGS discharge, CDEC FNF and CDEC SWE archive
directories and writes a job-specific JSON inventory under
`observations/work/station-migration-inventory/JOB/inventory.json`. It does not
query providers, convert files, delete data, or modify cron. Discovery does not
prove that every intended provider station has been downloaded.

The report includes source file counts/bytes, receipt evidence, stable task IDs,
and source inode/size/mtime fingerprints. Statuses are `candidate`, `blocked`
and `empty_or_missing`. Candidates have only passed **metadata screening**;
source payload checksums, row identities, units and exact round trips must still
be checked by the validated adapters. SWE candidates require current matching
acceptance identities for every source chunk. FNF/USGS checks are preliminary;
their source content is not read by this inventory. Missing or malformed evidence
is reported per history rather than preventing all other histories from listing.

The proposed pilot selects at most 32 histories, round-robin across provider,
product, sensor and resolution, with at most eight workers. The inventory has
**no execute mode**. The separate staged converter follows this design:

1. Freeze the reviewed inventory and explicit station metadata snapshots into a
   campaign manifest. Keep existing archives authoritative; write only under
   `observations/work/station-migration/<campaign>/`.
2. Before each conversion, recheck every planned source/evidence fingerprint.
   Changed inputs need replanning, not an automatic overwrite. Run the full
   adapter validation; a metadata candidate is not an accepted replacement.
3. Give each task a deterministic target and per-target lock. Write atomically,
   reopen and compare every source cell, then save an acceptance receipt binding
   the campaign/task, source hashes and output payload/file checksums.
4. Resume by rechecking both source and output evidence. Skip only valid accepted
   tasks. Recover an output published before its receipt by auditing it again;
   do not treat a filename or a completed SLURM task as acceptance. Limit retries
   to transient failures; quarantine semantic/history-loss failures for review.
5. Finalize the catalog from an explicit list after all required tasks pass.
   Use bounded station concurrency, not concurrent writers to one catalog.
   Capture throughput, peak memory, file count and bytes before scaling up.
6. Separately approve production-reader/update integration and cutover. Migration
   does not authorize deletion of old data. Retire originals only after an
   audited replacement plan, successful update/recovery tests and user approval.

The initial design retains one history file per station/product/sensor/resolution;
it does not concatenate unrelated series or assume conversion reduces bytes.
Raw provider responses and acquisition evidence remain separate until a dedicated
retention/compaction policy is validated.

Inventory job **5211958** was submitted with four CPUs and a one-hour limit;
results belong to its job-specific inventory above, not a mutable latest report.
All 33 station-framework tests passed, along with Ruff, shell syntax and diff
checks. The inventory must finish and be reviewed before selecting conversion
tasks; no bulk conversion or production cutover has been submitted.

### Bounded resumable conversion runner

`bin/convert_station_pilot.py` is the separate SLURM-only executor for at most
128 reviewed inventory candidates and eight concurrent worker processes. It
requires explicit `--inventory` and `--campaign` paths. The campaign must be
under `observations/work/station-migration/`; existing provider archives remain
authoritative. The runner pins the selected entries, inventory checksum and
explicit CDEC metadata snapshot checksums in `campaign.json`. Resume with the
same arguments and campaign directory; different inputs require a new campaign.

An exclusive campaign lock prevents duplicate runners. Each task re-screens
source/evidence identities (including added year files), checks metadata, runs
the provider adapter and writes a deterministic NetCDF target with the shared
atomic writer. Exact source-table/readback comparison and source checksum
rechecks precede its acceptance receipt. Receipts bind the task, campaign,
source hashes, NetCDF file checksum and logical payload checksum. An unreceipted
file is recoverable only if it exactly matches the validated source table;
different orphaned or corrupted accepted outputs fail closed for review.

On resume, accepted tasks still recheck sources, output checksums and NetCDF
payload integrity before reporting `skipped_verified`. The runner uses spawned
processes rather than threads for NetCDF isolation. Independent failures are
recorded while other tasks finish; it does not repeatedly retry semantic errors.
Progress is saved after every task, and a catalog is finalized only when all
selected histories pass. `--verify-resume` immediately performs a second pass
and requires every task to be verified without rewriting its station file.

Job **5212912** passed for the 32 candidates from inventory **5211958** in
**3 minutes 28 seconds**, with eight workers and 32 allocated CPUs for memory
headroom (peak resident memory approximately 4.34 GiB).
The campaign is `observations/work/station-migration/pilot-5211958/`; inspect
`progress.json` while running and `acceptance.json` for the final result.
All 32 histories passed exact conversion; the second pass reported 32
`skipped_verified` results, and final catalog validation passed. The sample
contains **2,015,619 records**, consolidating **1,015 source data files into 32
NetCDF histories**. Source data files total 40,345,396 bytes; NetCDF histories
total 51,546,300 bytes, excluding separate campaign evidence on both sides.
This demonstrates file-count reduction, not byte savings for this sample.
Existing raw/processed data, update scripts and
cron schedules are unchanged. The executor is still a staging pilot, not a
production migration or deletion tool.
The full test suite passed **805 tests with four skips**. Converter-specific
tests cover verified resume, orphan recovery without rewriting, source/metadata
drift, added source-year detection, corrupted outputs and conflicting campaigns.

#### Expanded staged batches

Use `--batch-size 128` to select the next balanced batch from the frozen inventory.
Repeat `--exclude-campaign PATH` for earlier completed batches. Exclusions require
a passed campaign with a complete accepted task list and the same inventory hash;
this is selection evidence, not a fresh audit of earlier payloads. Blocked/empty
entries are never selected. Selection is deterministic and balanced across
provider/product/sensor/resolution groups. Resume with exactly the same arguments;
the frozen campaign manifest rejects changed selections or metadata snapshots.

Job **5216548** was submitted for 128 additional histories from inventory 5211958,
excluding the accepted 32-history pilot. It uses eight workers and 32 allocated
CPUs, with `--verify-resume`. The campaign is
`observations/work/station-migration/batch02-5211958/`; completion and acceptance
must be checked there. No automatic all-archive expansion, production cutover or
deletion is authorized by this staged batch. Originals and cron remain unchanged.
Batch-selection/exclusion tests passed; the full suite passed **807 tests with
four skips**, with Ruff and diff checks clean. Live batch acceptance remains a
separate gate.

Batch job **5216548** subsequently passed in **10m14s**: 128 accepted conversions,
128 verified skips on resume, and successful final catalog validation. The batch
contains 6,949,730 records, consolidating 4,157 source data files (149,632,687
bytes) into 128 station histories (172,380,403 bytes). Peak resident memory was
approximately 6.84 GiB. Together with the first pilot, 160 histories are staged;
these are still not production replacements.

#### Incremental updates of the converted collection

`slurm/test_converted_station_updates.sh` runs
`bin/test_converted_station_updates.py --campaign PATH --end YYYY-MM-DD`.
It requires a passed conversion campaign and selects one history per
provider/product/sensor/resolution group from its catalog. Selected files are
checksum-verified and copied into a new job-specific workspace under
`observations/work/converted-station-update-test/JOB/`. No accepted conversion
or original provider archive is updated in place.

Daily/hourly/event series request the 19 provider calendar dates ending at the
explicit date; monthly series request the two preceding complete calendar
months. Monthly SWE additionally tests March–May of the most recent completed
spring. Native timestamp and fixed-PST semantics remain unchanged. Requests are
sequential and use existing provider retry/rate-limit behavior.

Each test checks exact incoming rows against published rows, unchanged records
outside the window, repeat-publication inode/mtime stability, stale-catalog
rejection when a file changes, catalog recovery, and original converted-file
checksum preservation. Reports distinguish added records, revised records,
returned value-status counts, and empty/no-op windows. A successful request
does not imply complete data coverage. Independent test failures are retained
while remaining groups are exercised; no failure is silently accepted.

Job **5216876** was submitted for nine representative groups from batch02-5211958,
ending November 7, 2026, with eight CPUs for memory headroom and a one-hour
limit. See its `progress.json`, then `acceptance.json` in the job-specific
workspace. Submission is not validation. The 22 focused station/update/catalog
tests, Ruff, and shell syntax checks passed. Cron and production archives remain
unchanged.

#### Consolidated staged station collection

Array **5216931** completed all 68 batches in 50m22s: 8,703 accepted
histories and 8,703 verified resume skips, covering 141,984,906 records.
Together with the two accepted pilot campaigns this covers all 8,863 eligible
histories in inventory 5211958; nine empty/missing SWE histories remain excluded.

`bin/finalize_station_collection.py`, launched through
`slurm/finalize_station_collection.sh`, checks exact inventory coverage with no
duplicate tasks, locks the explicit campaigns, verifies batch catalog file
checksums against conversion receipts and identities, then builds one catalog
with full station payload readback. It neither moves data nor switches cron.
The consolidated catalog references existing immutable staged station files;
use `read_member` or `bin/report_station_catalog.py` with that catalog as the
single reader entry point. This is a snapshot of accepted conversions, **not**
a fresh check of whether provider archives have subsequently changed.

Finalization job **5217086** requests eight CPUs and two hours. On success it
publishes `observations/work/station-migration/collection-5216931.json` and
`collection-5216931.acceptance.json`. Logs are
`observations/logs/station-collection-5217086.out`. Submission alone is not
acceptance. Production-reader/update cutover and live incremental handoff remain
subsequent gates; original provider archives remain authoritative and must not
be deleted based on this catalog.

Finalization **5217086 passed** in 1h03m53s, with eight allocated CPUs and
about 1.4 GiB peak resident memory. The catalog includes 150,950,255 records
across 7,495 USGS discharge, 1,321 CDEC SWE and 47 CDEC FNF histories.

The next handoff test, **5217167**, uses
`bin/test_converted_station_updates.py --catalog
observations/work/station-migration/collection-5216931.json --end 2026-11-07`.
The test requires the consolidated acceptance receipt and matching catalog hash,
selects one history per provider/product/sensor/resolution group, and checks
`read_member` against direct payload reads. It refreshes isolated copies with
live provider responses, verifies outside-window preservation, revised records,
no-op repeat updates, stale-catalog rejection, and refreshed catalog-reader
equivalence. Original converted members and the source catalog must remain
unchanged. Monthly SWE includes a spring window as well as recent complete
months. Provider omissions fail closed rather than deleting historic records.
This tests the reader/update interfaces on representative histories, not a
provider-wide refresh, a new scientific validation, or a production cutover.
Raw responses and the job-specific acceptance report are under
`observations/work/converted-station-update-test/5217167/`; the job requests
eight CPUs and one hour. Old archives and cron remain unchanged.

#### Full staged station migration

After the converted-collection update test **5216876** passed all nine groups
and 11 refresh windows in 3m47s, the full staged migration was authorized.
`bin/submit_station_migration.py` partitions the frozen inventory into disjoint,
balanced batches of at most 128 histories. The default invocation is a read-only
plan; `--submit` freezes `plan.json` and records the SLURM submission under an
exclusive lock. An existing submission receipt prevents accidental duplicate
arrays, including an uncertain submission interrupted before recording its job ID.

Array **5216931** covers **8,703 remaining histories in 68 batches**, excluding
the 160 accepted pilot histories. Nine empty/missing SWE histories remain
excluded, not silently marked migrated. The campaign root is
`observations/work/station-migration/full-5211958/`. Its plan pins the source
inventory and shared station metadata hashes. Each array task calls the existing
converter with eight workers and a deterministic `batch-NNN` workspace, followed
by full resume verification and catalog validation. Concurrency is capped at two
batches: 16 conversion workers and 64 requested CPUs total. Each task has a
two-hour limit. Logs are `observations/logs/station-migration-5216931_TASK.out`.

Array submission/execution does not establish migration acceptance. Inspect each
batch's `acceptance.json`; failed batches retain accepted work and original data.
Retry only reviewed array indices using the same plan path/hash and batch roots;
the converter rechecks accepted outputs rather than rewriting them. Source drift
requires a new reviewed plan, not bypassing validation. After all batches pass,
review collection-wide coverage and exceptions before production reader/update
integration. No canonical path cutover or deletion is performed by this array.

## Temporal-resolution level — activated September 23, 2026

Producers and NWM readers now use `<domain>/<stream>/hourly/YYYY/MM/`.
The previous year directories were renamed without rewriting NetCDF data.
The layout is:

```text
forcing/outputs/<domain>/
  baseline/hourly/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1
  nrt/
    hourly/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1
    daily/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1.daily
    monthly/YYYY/YYYYMM.LDASIN_DOMAIN1.monthly
  retro/
    hourly/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1
    daily/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1.daily
    monthly/YYYY/YYYYMM.LDASIN_DOMAIN1.monthly
```

Resolution names describe the data, not file duration. Hourly records remain
packed into calendar-day files (00–23 timestamps); daily summaries use completed
01–00 endpoint samples with 00–00 bounds. Monthly summaries cover whole calendar
months. Temperature monthly min/max fields are means of daily extrema. See
[summary semantics](forcing_temporal_summaries.md). The same domain → stream →
resolution principle applies to NWM history products, but this migration does
not relocate NWM outputs, restart files, or external source archives.

Only independently produced domain baselines need storage. Routine baseline
daily/monthly summary products are not planned. NRT and retro remain separate.

### Preparation and cutover gates

Completed acceptance results and the reader/receipt worklist are recorded in
[the hourly cutover checklist](forcing_hourly_cutover_checklist.md). Gate 4627957
passed and released NWM 1986 after the NRT no-op and CONUS model tests passed.
The migration renamed 97 directories containing 35,752 files and rebound 17,777
JSON sidecars; data-file identities and existing summary freshness were preserved.
The pre-cutover operational repair/no-op test 4627624 passed. The exact inventory,
durable rename/metadata journal and original sidecar backups are under
`forcing/status/layout-migration/cutover-20260923/`. Code checkpoint: `d620802`.
Historical completed task files, logs, cleanup journals and content-audit evidence
remain historical; current dated paths in published JSON sidecars are rebound.
No compatibility symlinks were created. NRT's private `.prism-window-cache`
remains at the stream-container level and is not a published hourly product.

Run this read-only inventory while jobs continue:

```bash
conda activate hydro-ops
python bin/plan_forcing_resolution_layout.py
```

Use `--inventory-files --output PATH.json` to save a per-file identity snapshot
alongside the directory plan. This remains read-only for all forcing files and
has no execute mode. Save a fresh snapshot at the actual maintenance window.

It emits JSON with proposed year-directory renames, destination conflicts, all
current user SLURM jobs, and source/configuration/documentation files requiring
path review. It deliberately has **no execute mode** and never declares the
system safe solely because the queue is empty. It does not recursively scan the
archive unless `--inventory-files` is selected, or validate NetCDF contents.
Run it again immediately before cutover; this is a live plan, not a frozen job list.

The following sequence was used for this cutover and remains the checklist for
future coordinated migrations:

1. Let forcing chains finish and verify final coverage/audit reports.
   Completion estimates are not safety gates; retries and descendants must finish.
2. Arrange a pause at an NWM restart/checkpoint boundary. Review running and
   pending NWM jobs, including their frozen submission environments and namelists.
   Stop automatic chain advancement/launches during the maintenance window;
   do not cancel or hold jobs merely by running the planner. Check interactive
   readers, cron, status scans, and other users as well.
3. Prepare and review path edits for producers, PRISM controllers, baseline
   cleanup, repair scripts, source replacement, status discovery, subset tools,
   summary scripts, and NWM `INDIR`/forcing roots. Audit dynamically assembled
   paths too: textual matches are a review aid, not a complete dependency graph.
   Pending jobs with captured old paths must be updated or resubmitted deliberately.
4. Save the pre-migration inventory and rollback journal. Move only four-digit
   year directories beneath `hourly/`, retaining filenames and sidecars. Use
   same-filesystem renames; no NetCDF rewriting or recompression is needed.
   Never merge conflicting destinations. Leave `daily/`, `monthly/`, experiments,
   and unrelated files alone. No backward-compatibility symlinks are planned.
5. Rewrite operational manifest/task-list paths using exact, idempotent mappings
   that distinguish stream roots from already-qualified resolution roots. Preserve
   historical provenance and source/content checksums; do not blindly replace all
   occurrences of a stream prefix (which would nest daily/monthly under hourly).
   The older `rewrite_manifest_paths.py` does NOT yet implement this new mapping.
6. Validate counts, sizes, sidecars, and representative NetCDF reads against the
   saved inventory. Test year-boundary hourly lookup, daily/monthly aggregation,
   status reporting, subset extraction, NRT no-op planning, and an NWM forcing-read
   smoke test before releasing jobs. A migration must not trigger scientific
   reprocessing solely because a path changed.
7. Release producers/readers only after all gates pass. Before release, rollback
   can reverse recorded renames and restore saved configuration/manifests. After
   new production writes begin, rollback requires another coordinated pause and
   reconciliation—never overwrite newly published data.

`bin/migrate_forcing_hourly_layout.py` is the separate, explicitly gated executor.
It journals exact renames, backs up changed sidecars, and verifies all data-file
identities. `bin/verify_forcing_hourly_layout.py` checks representative archives,
NWM year-boundary lookup, unchanged daily/monthly summaries, status discovery,
and the CNRFC grid crop. Its checks do not release held jobs.
