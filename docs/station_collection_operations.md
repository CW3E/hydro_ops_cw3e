# Station collection production operations

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

Daily/hourly/event updates request 31 provider-label days ending yesterday in
fixed PST. Monthly updates request the two preceding complete months. This is
bounded revision/extension, not a full historical refresh or automatic gap fill
after a long outage. Discontinued stations may return no new rows. Routine cycles
retain previously known observations omitted from a refresh response, rather than
deleting them, and report their series/date
keys as explicit reconciliation warnings; they are not silently marked as newly
confirmed by the provider. Explicit missing values are legitimate revisions. New series/schema
changes require review; no-op responses do not rewrite history.

## Recovery and rollback

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
