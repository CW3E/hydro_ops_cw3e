# Station collection production operations

## Legacy cleanup audit

Legacy processed CSV archives and original migration-stage NetCDF copies are
not automatically deleted after cutover. Run the read-only audit on SLURM:

```bash
source bin/project_environment.sh
sbatch slurm/audit_station_legacy_cleanup.sh
```

Reports are job-specific under `observations/work/station-cleanup/<job-id>/`.
`report.json` summarizes counts/bytes; `station-audits.json` records comparisons;
`deletion-plan.json` lists exact candidates, checksums, file identities and their
active replacements. A `--limit 12 --workers 4` smoke test is supported.

Eligibility requires the accepted conversion receipt, unchanged source checksums,
and preservation of every historical field/value/flag in the current station
file. Differences, including legitimate provider revisions, remain for review.
Metadata, acquisition evidence and audit reports are never deletion candidates.
The inventory is not deletion approval: review the plan and revalidate targets
and replacements under the station-operation lock before separately authorized
removal. A running update can make the frozen audit stale; retain such files.

## Daily regional / weekly remainder schedule (November 2026)

Observation jobs are independent of forcing/model dependencies and use ordinary
SLURM resources, never NRTRES for `mpan`. The saved cron schedules now specify:

| UTC schedule | Selection | Settings |
| --- | --- | --- |
| Daily 19:00 | CDEC FNF | 4 preparation workers |
| Daily 19:15 | CDEC SWE | Daily/monthly only, 4 preparation workers, optimized coordinator |
| Daily 19:30 | USGS regional | CNRFC/UCRB reporting-region union, keyed paced acquisition, 4 preparation workers |
| Sunday 21:00 | USGS remainder | Complement of the regional union, same request controls |

`bin/submit_station_updates.py --selection usgs --usgs-scope regional` and
`--usgs-scope remainder` are explicit subsets. `all` remains the manual default.
`config/usgs_update_cohorts.json` pins the two inventory files by checksum and
portable project-relative path. Intersecting their union with the current active
catalog yields 706 regional stations (469 CNRFC, 237 UCRB) and 6,789 others.
B120/Yampa reporting stations are already included. Review/rebuild this config
when domain definitions or inventories change; do not silently reassign stations.

Subset status and submission ledgers use `usgs-regional-*` and
`usgs-remainder-*`; they never overwrite the full-CONUS `usgs-latest.json`.
Reports record cohort definitions/counts. Resume checks reject another scope or
changed cohort. Partial commissioning tests do not overwrite routine status.
Duplicate same-scope submissions are skipped; different station jobs remain
serialized via `afterany` dependencies and the shared operation lock, so one
provider failure does not permanently block other selections. A very long weekly
run can still delay the following daily job; watch queue and acceptance reports.

The validated update windows and native provider time semantics are unchanged.
Monthly CDEC series are checked daily; hourly/event SWE remains on demand.
The five-minute SWE result was resumed, not a fresh whole-collection runtime.
USGS estimates at five seconds/station are about one hour regional and 9.5 hours
for the remainder, excluding queue/retry delays. Keep individual-failure reports
and retained-omission warnings visible; job completion alone is not acceptance.

Installed on **login1**, November 12, 2026, after clean-environment submission
checks and scoped live tests **5270813** (regional: 3 published/1 unchanged) and
**5270815** (remainder: 4 published), both passed in 41 seconds with no failures.
The install appended only these four observation entries to the existing live
crontab; forcing/status lines, model pauses and the monthly-retro pause were
preserved. It did not enable the saved Stage-IV monitor entry. Rollback and exact
installed snapshots are `observations/status/stations/cron-20261112-before.crontab`
and `cron-20261112-after.crontab`. No crontab was installed on login2.
Routine subsets have separate latest-status files; the first full regional and
weekly scheduled runs still require their own completion/acceptance review.

## Layout and activation

```text
observations/processed/stations/v1/
  catalog.json
  <provider>/<station_id>/<product>/<sensor>/<resolution>.nc
observations/status/stations/
  active.json
  cutover.json
  cutover-submission.json
  operation.lock
  pending.json
observations/work/station-cycles/<job>/
  acceptance.json
  <index>/raw/                   provider response and acquisition receipt
  <index>/candidate.nc           changed-history transaction candidate
  <index>/publication.json
```

There is one compressed history per station/product/sensor/resolution, shared
across model domains. Source strings, flags, metadata and provider timestamps
are preserved. CDEC fixed-PST labels are not reinterpreted as UTC hydrologic
days. Embedded bootstrap provenance/diagnostics describe the initial archive;
current counts come from the catalog, and subsequent acquisition evidence lives
in the cycle workspace. These workspaces are durable evidence, not disposable
scratch, until a retention policy is adopted.

`active.json` is the routing switch. During copying its state is `preparing`;
routine routing and old writers fail closed. Activation changes to `active`
only after all copies and their catalog verify against the accepted staged
collection. Interrupted preparation can resume with the same catalog; conflicting
destination bytes are never overwritten. The recorded initial catalog checksum
is historical: normal updates legitimately change the catalog.

Original provider archives, raw responses and staged conversions are retained.
After activation, legacy CSV histories are rollback evidence, not current
operational products. No old archive is deleted by cutover.

## Readers and routine updates

```bash
source bin/project_environment.sh
# Default to the active catalog; listing does not scan every station payload.
"$HYDRO_OPS_PYTHON" bin/report_station_catalog.py
"$HYDRO_OPS_PYTHON" bin/report_station_catalog.py --json

# Existing saved cron commands route to the new collection after activation.
bash bin/run_cron.sh bin/submit_cdec_fnf.py --dry-run
bash bin/run_cron.sh bin/submit_cdec_swe.py --dry-run
# New explicit USGS update command; no USGS cron installation is implied.
bash bin/run_cron.sh bin/submit_station_updates.py --selection usgs --dry-run
```

Remove `--dry-run` to submit. Python readers pass `active_catalog(project_root)`
from `hydro_ops.observations.station_operations` to `station_catalog.read_member`.
Reads verify the selected member's checksum. Explicit historical catalogs remain
available for comparisons. Old direct writers/backfill entry points refuse to
modify legacy archives once activation starts; reviewed imports are required to
add stations or backfill the new collection.

The submitter suppresses duplicate same-product jobs and serializes different
station update jobs using `afterany` dependencies. Workers also take a global
nonblocking collection lock. Ordinary shared resources, eight allocated CPUs
for memory and a conservative 24-hour limit are used; this first implementation
processes histories serially. No NRTRES is requested. The existing USGS provider
rate limiter remains in effect.

Current SWE eligibility is now stricter: **listed Snow Courses, sensor 3/monthly;
listed Snow Pillows, sensor 82/daily**. See the
[CDEC-confirmed policy](cdec_swe_observations.md#current-collection-policy--cdec-clarification-november-15-2026).
It currently selects 391 archived series (260 courses and 131 pillows); BKL
monthly was excluded after the user confirmed no data exist. BKL daily pillow
data remain eligible. Old reports cannot be resumed across this policy change. No cron
schedule change is needed. The following counts and commissioning notes are
historical, not current selection instructions.

Previously, CDEC SWE cycles selected **daily and monthly records only** (then
284 daily and 808 monthly histories). Existing hourly/event archives are retained
but no longer refreshed by the routine cron command. The running all-resolution
commissioning cycle 5260662 was subsequently canceled at user request; its accepted
updates are retained. Replacement **5263910** resumes daily/monthly records only,
with four preparation workers and `--optimized-coordinator`. It waits for USGS
**5263574**, which was released by the cancellation. At cancellation the progress
report contained 310 successful daily/monthly records and one failed record;
resume verifies current catalog hashes before reusing successes. The frozen
acquisition endpoint remains 2026-11-10. No cron reinstall is needed: the saved
`submit_cdec_swe.py` command delegates to the updated selection defaults.

Historical on-demand command (now rejected by the SWE policy; do not run):

```bash
bash bin/run_cron.sh bin/submit_station_updates.py --selection cdec-swe \
  --swe-resolutions hourly event --dry-run
```

Do not remove `--dry-run` or submit this obsolete scope. These were recent-window refreshes, not arbitrary
historical backfills. On-demand scopes retain the same collection locks and
duplicate-job protection, and do not replace the routine SWE latest-status report.
Reports record the selected resolutions. Resuming an old all-resolution report
defaults to daily/monthly; explicitly list all four resolutions to resume all of
its original scope. Continuations preserve the requested selection. FNF and USGS
selection policies are unchanged. Coordinator optimization remains opt-in.

Daily updates (and explicit hourly/event updates) request 31 provider-label days ending yesterday in
fixed PST. Monthly updates request the two preceding complete months. This is
bounded revision/extension, not a full historical refresh or automatic gap fill
after a long outage. Discontinued stations may return no new rows. Routine cycles
retain previously known observations omitted from a refresh response, rather than
deleting them, and report their series/date
keys as explicit reconciliation warnings; they are not silently marked as newly
confirmed by the provider. Explicit missing values are legitimate revisions. New series/schema
changes require review; no-op responses do not rewrite history.

## Recovery and rollback

### Performance prototype — not yet enabled in routine updates

The serial updater rereads a station history during publication and validates
the full collection catalog (including resolving every member path) for each
changed station. `station_preparation.py` provides an isolated alternative:
process workers read a history once and prepare fully validated candidates;
a single `PreparedPublisher`, used only while holding `operation_lock`, retains
one validated catalog/index in memory. It checks catalog identity and the old
station hash, retains full candidate readback and checksum checks, and uses the
same recoverable station/catalog journal protocol. Publication failure poisons
the coordinator; recovery is required before further work. No concurrent NetCDF
threads or concurrent catalog writers are introduced.

Benchmark **5253366** uses `bin/benchmark_station_preparation.py` and 16 allocated
CPUs on shared-128. It selects 32 histories across stored record lengths from
successful fresh updates in cycle 5249499. Saved USGS responses are replayed
against the immutable migration histories in isolated mirrors with a full-size
catalog. It compares the old serial reference with 1, 4 and 8 preparation
processes, recording read/merge/write/description/publication timings. Every
result must match the reference's complete source columns, metadata, flags and
values; unrelated catalog entries must remain unchanged. Response checksums are
verified. This benchmark makes **no API requests** and does not modify the live
station collection or its running updater.

Results: `observations/work/station-preparation-benchmark/5253366/report.json`;
logs: `observations/logs/station-preparation-benchmark-5253366.out`. This is a
local-processing benchmark, not a measured end-to-end download speedup; cache
warming can favor later variants. The shared USGS five-second request pacing
is unchanged. A live bounded-acquisition test, timeout/retry diagnostics, and
incremental/resume validation are still needed before operational adoption.

Benchmark 5253366 passed exact equivalence for all 32 stations: reference 401 s,
one preparation worker 66 s, four workers 28 s, eight workers 33 s. Four workers
are the live commissioning choice, not a claim of 14x faster network downloads.

The live updater and submitter now accept `--prepare-workers 4` (default remains
zero, the existing serial path). API acquisition remains serialized and paced;
up to four preparation tasks run in separate spawned processes. One coordinator
publishes under the collection operation lock. Acquisition/preparation failures
are station-local; publication uncertainty stops the collection cycle. Pending
preparations are drained before reporting budget exhaustion. Raw response and
publication receipts remain attached to each station task, with acquisition and
preparation/publication timings. Prepared-mode automatic continuations remain
disabled until separately commissioned.

Live test **5260642** updates the first 32 USGS catalog entries using the normal
31-day incremental acquisition window, four preparation workers, eight allocated
CPUs and a one-hour limit. It uses the normal submission serializer and journal,
and publishes accepted updates to the active collection. Its bounded `--limit 32`
report does **not** overwrite `usgs-latest.json` and cannot serve as a full-cycle
resume report. See `observations/work/station-cycles/5260642/acceptance.json`
(or `progress.json` while running) and `observations/logs/station-cycle-5260642.out`.
Submission is not acceptance; inspect outcomes before enabling routine defaults.

Live test 5260642 passed in 3m31s: 30 stations published, two unchanged, no failed
or omitted-record warnings, through November 10, 2026. Summed acquisition time
was 132 s and serialized publication 14 s. This sample is not a full-collection
runtime forecast.

Follow-up **5260654** repeats the same 32-station live incremental update with
four preparation workers and a one-hour limit. Unchanged provider responses
should yield unchanged histories; genuine provider revisions remain valid
updates, not forced no-ops. **5260655** runs the full USGS selection with four
preparation workers, eight allocated CPUs, a 24-hour SLURM limit and the existing
20-hour durable work budget, only after successful completion of 5260654.
`submit_station_updates.py --after-success <job>` preserves collection-wide
submission serialization while adding an explicit successful-test dependency
and cancellation on an unsatisfiable dependency. Default cron preparation remains
serial pending full-cycle acceptance. These jobs do not increase API concurrency
or remove rate limiting. Job-specific evidence is under
`observations/work/station-cycles/<job>/acceptance.json`; any budget-limited
remainder is reported for an explicit resume, not silently considered complete.

### Keyed request-control commissioning

After configuring `USGS_API_KEY` in ignored `config/site.local.env`, the old
unkeyed full update **5260655** was canceled at the user's request (4h50m).
Its `progress.json` contains **609 accepted station results**, all verified
against current catalog hashes by `resume_successes`; the frozen endpoint is
2026-11-10. Preserve this report and task receipts. Resume using
`--resume-report observations/work/station-cycles/5260655/progress.json` after
the new acquisition path passes commissioning, rather than redownloading those
accepted stations. Startup journal recovery remains required; cancellation is
not proof that no transaction was interrupted. No station histories were deleted.

Benchmark **5263100** (`bin/benchmark_usgs_request_control.py`) tests 32 stations,
including eight previously slow acquisitions, with one then two HTTP workers.
It requests the same 31-day window and writes only isolated raw/normalized data
under `observations/work/usgs-request-benchmark/5263100/`; it does not publish
station histories or acknowledge a full update. Report: `report.json` in that
directory. Four CPUs, shared-128, one hour; no NRTRES. Sequential variant order
may warm server caches, so throughput alone is not a definitive scaling result.

The opt-in `ControlledRequest` callback has no hidden HTTP-adapter retries.
Every attempt shares the existing five-second start-spacing lock; reported
quotas can make that interval longer, never shorter. It records per-attempt
status, pacing, elapsed time, timeout type and quota/Retry-After headers without
logging the key. A 429 installs a shared cooldown and defers the station;
503 Retry-After is also honored. Missing/invalid retry delays on 429 use a
conservative one-hour cooldown. Two attempts maximum, connect/read inactivity
timeouts of 10/30 seconds, and a 120-second admission budget including pacing.
This is not a hard wall-clock transfer deadline: read timeout measures socket
inactivity. Deferral preserves old records and needs a later retry pass.

The callback is used by this benchmark only. Routine download defaults and
production acquisition concurrency are unchanged. A full keyed resume has not
yet been submitted. Compare normalized records between variants; provider
revisions can legitimately change values between fetches. Investigate differences
and missing stations before operational adoption.

References: [USGS API keys](https://api.waterdata.usgs.gov/docs/ogcapi/keys/),
[query efficiency](https://api.waterdata.usgs.gov/docs/ogcapi/efficiency/), and
[api.data.gov rolling quotas](https://api.data.gov/docs/developer-manual/).
Service-specific response quotas take precedence over the generic 1,000/hour
default. Keys belong in `X-Api-Key`, never query strings or reports.

### CDEC SWE coordinator optimization (opt-in)

Timing in live SWE cycle **5260662** showed a different bottleneck from USGS:
the first 25 records used about 28 seconds in acquisition, versus hundreds of
overlapping worker-seconds processing full histories. BFL's hourly history had
358,829 rows; acquisition took 5.6 seconds but worker preparation about 140
seconds. The coordinator also read/validated each entire history before the
worker repeated that read, and collected futures in submission order.

`bin/update_station_collection.py --prepare-workers 4 --optimized-coordinator`
now offers two isolated changes, **off by default**:

- `read_station_header` reads metadata and field definitions for acquisition,
  not observation arrays. This is acquisition context, not an accepted empty
  history. Workers still verify the source digest, read and validate the complete
  history, preserve omitted records, merge, write/read back, describe, and recheck
  source identity before serialized journaled publication.
- `pop_completed` collects a finished preparation instead of waiting for the
  oldest unfinished future. The same four-task bound applies. Report rows retain
  `task_index` so completion order cannot misidentify cached response directories.
  Station-local errors and collection-fatal publication errors retain their
  existing semantics. Acquisition concurrency and retry settings are unchanged.

Benchmark **5263147** compares FIFO/full-context, ready-first/full-context, and
ready-first/header-context with four preparation workers and the 18 accepted SWE
inputs from benchmark 5260658. It uses isolated mirrors, cached incoming data,
full-size catalogs, and exact source-column/metadata/flag/value comparisons;
unrelated catalog entries must remain unchanged. No API requests or canonical
station writes occur. Fixed variant order may favor later filesystem caches.
It allocates 16 CPUs (32 GB requested memory), two hours on shared-128, without
NRTRES; the preparation concurrency remains four, not sixteen.

Report: `observations/work/station-coordinator-benchmark/5263147/report.json`.
Entry point: `slurm/benchmark_station_coordinator.sh`.
The ongoing 5260662 cycle was neither canceled nor switched to this option.
Review benchmark equivalence and timings before a bounded live test or default
adoption. Repeated merge/validation passes are deliberately unchanged in this
first optimization so the original scientific/integrity gates remain intact.

### USGS keyed controller adoption

Benchmark **5263100** passed: all 32 stations (including eight previously slow
ones) succeeded with one worker in 155.2 seconds and two workers in 160.0 seconds.
All 64 requests returned HTTP 200 on their first attempt; no throttling or timeout
occurred, and both fetches produced identical normalized records. The API reported
a 1,000-request/hour allowance. This short test does not establish the cause of
the earlier stalls or guarantee a full cycle without delays. There was no speed
advantage to two acquisition workers at the shared five-second start spacing.

The adopted **station-cycle USGS** defaults now supersede the earlier opt-in notes:

- `USGS_API_KEY` is required in the launcher environment. Keep it in ignored,
  permission-restricted `config/site.local.env`; it is sent only as a header.
- One serialized acquisition worker uses `ControlledRequest`. Every request and
  retry follows the shared pacing/cooldown controller. There is no extra outer
  station retry loop; the same 10/30-second connect/read inactivity limits and
  120-second admission budget described above apply.
- Four preparation processes are the default for USGS in both the submitter and
  runner. Publication remains single-writer and journaled. CDEC defaults are
  unchanged; `--optimized-coordinator` remains off pending its own validation.
- Individual exhausted/failed station acquisitions retain their old histories
  and are reported for retry. A shared cooldown exceeding the admission budget,
  HTTP 429, or HTTP 401/403 pauses the entire pass; already queued preparations
  finish before the partial report is written. The current and subsequent
  stations remain in `remaining`. Missing-key configuration fails before work.
- Prepared-path automatic continuations are still disabled. Inspect the report
  and explicitly resume partial work. Explicit `--prepare-workers 0` retains
  the serial preparation path, without reverting keyed USGS acquisition.

Attempt evidence is under each task's `request-attempts.json`; acquisition and
publication receipts remain unchanged. Historical direct-download entry points
are not silently switched by this station-cycle adoption. Neither cron schedules
nor the installed crontab are changed.

The full resume uses the canceled 5260655 progress report and freezes its
2026-11-10 endpoint. Its 609 accepted results are reused only while their catalog
hashes still match; any drift is reprocessed. After this pass completes, a normal
fresh cycle advances the endpoint. The expected 6,886 remaining one-page queries
have a pacing floor of about 9.6 hours, excluding processing/retries/queue time.

Resume job **5263574** was submitted with the adopted defaults, eight allocated
CPUs, 24-hour SLURM limit and 20-hour work budget, after termination of active
CDEC SWE job **5260662**. The normal collection lock/recovery gate still applies.
The first submission using `afterok:5263100` was rejected because SLURM had
already purged that completed benchmark from controller memory. Its accounting
record (`COMPLETED`, `0:0`) and passed report were independently rechecked, and
no duplicate USGS job existed before resubmission without the expired dependency.
The review ledger is `observations/status/stations/usgs-keyed-adoption.json`.
Inspect `observations/work/station-cycles/5263574/acceptance.json` (or
`progress.json` while running); submission is not full-cycle acceptance.

### CDEC preparation commissioning (November 11, 2026)

The same opt-in path is being tested for CDEC, without changing normalization,
fixed-PST labels, flags, omitted-record retention, or API concurrency. Historical
full runs took 10m19s for 47 FNF histories and 3h29m12s for 1,321 SWE histories.
Receipt timestamps suggest substantial local post-acquisition work, particularly
for changed hourly SWE histories, but are not an instrumented timing profile.

The cached benchmark now supports CDEC responses, verifying response checksums
and selecting small/median/largest histories within every sensor/resolution
group. Native monthly versus timestamp-label bounds are preserved. It compares
serial, one-worker and four-worker preparation against immutable migration
histories in isolated full-catalog mirrors; no live publication or acquisition
occurs in these benchmarks. Full-column/value/metadata/flag equivalence and
unchanged unrelated catalog entries remain mandatory.

| Job | Purpose | Dependency |
| --- | --- | --- |
| 5260658 | Cached SWE benchmark, all native sensor/resolution groups | None |
| 5260659 | Cached FNF daily/monthly benchmark | Success of 5260658 |
| 5260660 | Bounded live SWE update, largest history per sensor/resolution | Both benchmarks pass; existing USGS cycle 5260655 terminates |
| 5260661 | Full FNF update with four preparation workers | Success of 5260660 |
| 5260662 | Full SWE update with four preparation workers | Success of 5260660; FNF job terminates |

Benchmarks use 16 allocated CPUs and two-hour limits. The bounded live test uses
eight CPUs and one hour; full cycles use eight CPUs and the standard 24-hour
limit/20-hour durable work budget. Collection updates remain serialized, including
across providers. A failed benchmark/live acceptance blocks the downstream full
updates. `--representative` now selects the largest history in each group and
does not overwrite full-collection latest status or allow resume/continuations.
The routine defaults remain unchanged pending measured live acceptance.

Evidence paths are `observations/work/station-preparation-benchmark/<job>/report.json`
and `observations/work/station-cycles/<job>/acceptance.json` respectively.

### Current publication protocol

An update writes and validates an isolated candidate, then saves a persistent
transaction binding the old checksum, candidate checksum and new catalog entry.
It atomically replaces the station, atomically updates the catalog and marks the
transaction complete. A subsequent run recovers an interrupted transaction only
if target, catalog and candidate match the recorded states; unrecognized bytes
stop recovery. Tests cover interruptions before and after station replacement,
corrupted candidates and repeat no-op updates.

Station and catalog publication are not one filesystem transaction. A reader
may briefly reject a changed member until catalog publication finishes; retry
after recovery, never bypass the checksum. Other entries remain readable. A
cycle continues after individual acquisition or station-validation failures,
preserving the failed history and recording it for retry. Transport failures get
two attempts (in addition to provider-client HTTP retries), with separate raw
evidence. Publication/catalog uncertainty still stops the collection: the durable
transaction must be reconciled before any other writer proceeds.

Reports distinguish `passed`, `passed_with_warnings`, `partial`, and fatal
`failed`. Warnings list omitted keys retained; partial reports list failed and
unattempted histories. Partial runs exit nonzero even though accepted stations
remain published. Compact per-product status is saved in
`observations/status/stations/<selection>-latest.json`; job-specific acceptance
remains authoritative. A warning does not establish provider completeness.

`bin/submit_station_updates.py --selection usgs --resume-report <acceptance.json>`
resumes the original provider window and skips successful histories only when
their accepted checksums still match the current catalog. Changed entries are
reprocessed. The runner uses a 20-hour work budget inside the 24-hour SLURM limit,
leaving durable unattempted work instead of relying on a timeout. Optional
`--continuations 3` permits at most three additional serialized jobs, each with a
decrementing bound and `afterany` dependency. Successors are submitted only when
unattempted work remains, not for endless retries of permanently failed stations.
Failures are revisited on resume; warnings remain visible. No continuation is
submitted after a fatal publication exception. Routine cron settings are unchanged.

To roll back, first stop/hold collection jobs and leave cron paused. Preserve
activation records and all post-cutover raw/candidate/publication evidence.
Restoring legacy routing requires an explicitly reviewed archival/removal of the
activation switch; never do this during preparation or a pending transaction.
Old files still exist but may lack post-cutover observations, which must be
reconciled or reacquired before claiming rollback is current. No automatic
rollback deletes new data or silently downgrades it.

## Commissioning

Finalization **5217086** accepted 8,863 staged histories. Live handoff **5217167**
passed nine observation groups and 11 windows in 4m14s. These validate the
snapshot and interfaces, not provider-wide current completeness.

Authorized cutover **5217205** copies/checks the permanent collection and
activates routing. Success-dependent commissioning jobs follow:

| Job | Scope |
|---|---|
| 5217206 | All 47 CDEC FNF histories |
| 5217207 | Representative CDEC SWE sensor/resolution histories |
| 5217208 | Representative USGS discharge history |

Submission records: `observations/status/stations/cutover-submission.json`.
Logs: `observations/logs/station-cutover-5217205.out` and
`observations/logs/station-cycle-<job>.out`. Inspect acceptance before declaring
commissioning complete. Representative tests do not refresh all SWE/USGS stations.
Cron schedules and installation remain unchanged; review commissioning before
resuming cron or considering deletion of legacy archives.

### Full-provider commissioning outcome

CDEC FNF **5217206** passed all 47 histories in 10m19s. Full CDEC SWE **5217280**
passed all 1,321 histories in 3h29m12s (462 published, 859 unchanged). USGS
**5217281** stopped after 6h12m55s with 818 accepted histories (777 published,
41 unchanged), out of 7,495. The original fail-fast policy rejected station
`USGS-01583800` because the provider omitted October 24–25, 2026. A fresh narrow
API query also returned no records for those dates. Existing numeric provisional
observations remain retained; no inference is made that USGS has confirmed them.
The resilient recovery targets 6,677 outstanding histories using the frozen
October 8–November 7 window and preserves earlier accepted publications.

Recovery **5223360** was submitted with eight CPUs and up to three bounded
continuations. Its report is
`observations/work/station-cycles/5223360/acceptance.json`; submission is not
completed recovery. Preflight confirmed all 818 accepted history checksums still
match the active catalog. The full suite passed 874 tests (four skipped), and
the subsequently added continuation-submission test passed with focused tests.
Cron remains paused and no legacy observation files were deleted.
