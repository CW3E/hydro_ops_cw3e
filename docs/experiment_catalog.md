# Current workflows and historical experiments

## Pre-commit review — November 10, 2026

The review baseline is commit `8c775e2` (October 29). The initial inventory has
35 modified tracked files and 315 new files under source/script/test/documentation
directories. These counts describe the start of review, not a commit allowlist.
No runtime data, recovery originals, model checkpoints or scripts were deleted
during this first pass; cron installation and active campaigns were not changed.

Initial checks covered all 242 changed/new Python files with Ruff, shell syntax
for changed/new shell launchers, and source scans for old project paths, embedded
credentials and production NRTRES requests. No findings were reported by those
scans (credential-pattern scans are not a complete security audit). Full pytest
initially reported 903 passed, four skipped and one failure: the cron test still
expected monthly Retro to be active. The test now explicitly checks its pause
and `--skip-model-runs` on both NRT schedules, rather than restoring the old
schedule to satisfy the test. README/station-store and forcing-workflow text
claiming pilot-only/not-operational status was also corrected.
After these corrections the full suite passed: **904 passed, four skipped**
(48.33 seconds). One existing Python `load_module()` deprecation warning remains.
Ruff and `git diff --check` also pass. No production jobs were submitted by this
review, and no commit or push has been made.

### Script retention and retirement review

Do not retire scripts based on their names. In particular:

| Script/helper | Current dependency requiring retention |
| --- | --- |
| `test_isolated_revision_replay.py` | Routine bounded revision queue and regression tests |
| `benchmark_prism_rain_writer.py` | Repair-wave and rain-pipeline runners |
| `trial_prism_rain_pipeline.py` | Repair-wave and boundary-test runners |
| `benchmark_nwm_climatology.py` | Climatology extraction, audit and mapping tools |
| `diagnose_temperature_refresh.py` | Independent audit, alignment and curve checks |

Three untracked diagnostic scripts have no lexical callers in the inspected
code/docs: `diagnose_prism_local_exceptions.py`, `diagnose_retro_rain_coverage.py`,
and `trace_quebec_pilot_drainage.py`. They are **review candidates, not approved
deletions**. Their scientific/reproducibility value and runtime references need
review. Unlike already committed historical tools, deleting these now would not
leave a recoverable Git version. Completed campaign submitters likewise need
explicit retirement decisions, not blanket removal or relocation: many embed
campaign acceptance gates and are referenced by retained reports/tests.

### Remaining review before staging

- Inspect the larger new scientific, publication and station-transaction modules
  in coherent groups; passing tests and lint do not complete semantic review.
- Review campaign-specific submitters/diagnostics separately from routine entry
  points. Preserve the running CNRFC and USGS dependencies and saved evidence.
- Separate current operational guidance from chronological experiment logs;
  the large forcing/layout documents still contain historical instructions.
- Build explicit commit file lists by subsystem, including every imported helper
  and corresponding tests. Do not use an indiscriminate `git add .`.
- Keep generated data, logs, executables, credential files and runtime receipts
  outside commits. Actual data cleanup requires its own audited deletion plan.

### Second review pass

Reviewed the bounded replay publisher's pinned-source and stage gates, retained
originals, downstream progress and exact-source queue acknowledgement; the shared
summary replacement intent/recovery path; model refresh initialization and
publication isolation; and station update merging, omission retention, collection
locking and catalog recovery. This is targeted review, not a new scientific audit
of production arrays or a claim that every historical launcher is safe to rerun.

The PRISM source-history review reproduced a recovery regression: an unreadable
old source, or one missing the expected variable, prevented a valid downloaded
replacement because comparison raised first. The fix preserves the old file hash
and inspection error as `old_grid_unreadable`, with unknown value change. It
continues to reject invalid new grids and fails if old bytes cannot be hashed.
Regression tests include actual mocked downloads and interrupted metadata writes.
The deprecated test-module loader was also replaced. Full validation after this
pass: **908 passed, four skipped**, no warnings; Ruff passes for all 244
changed/new Python files, and `git diff --check` passes.

The three standalone diagnostics listed above are retained with explicit
historical/reproducibility docstrings. The forcing guide now begins with current
operating status, so dated investigation notes cannot be mistaken for current
cron instructions. No numerical policy, active model runner, cron schedule or
runtime dataset was changed by this pass.

The proposed file inventory is
`forcing/status/cleanup/precommit-review-20261110.json` (source snapshot before
the final review edits). It groups forcing science/repairs, forcing operations,
station collection, model/climatology, cleanup, and shared docs/environment.
This is **not a staging script**. A static absolute-import check found 87
cross-group import occurrences outside tests, including operational imports of
historical-named helpers. The two forcing groups must be dependency-closed or
combined before committing; shared documentation requires hunk-level assignment.
Refresh the recorded hashes and include the newly changed archive-output test
before using the inventory as a commit checklist. No files were staged,
committed, pushed or deleted during that review pass.

The final commit preparation combines forcing and model changes to preserve
their shared dependencies, followed by station collections, cleanup tooling,
and shared documentation. Exported index snapshots are tested independently of
unstaged files. This caught a missing model cleanup helper in the first group
and a cron test that assumed the checkout path matched the deployment path;
both were corrected before committing. Runtime data, installed cron schedules,
and active jobs are outside this commit operation.

## November 2026 follow-up inventory

`bin/inventory_historical_work.py` provides a read-only metadata inventory of
`forcing/work/`, `nwm/work/`, `nwm/outputs/tests/`, selected historical model run
directories, and `observations/work/`. It records exact file identities, sizes,
hard links, evidence/recovery categories, and lexical source-code references.
It also lists test/benchmark/pilot/profile launchers and their source references.
Run the metadata scan through `slurm/inventory_historical_work.sh`, not as a large
login-node scan. Reports are `forcing/status/cleanup/historical-work-JOB.json`
with an adjacent `.files.jsonl` ledger.

This inventory is **not a deletion plan**. Names, age, lack of a lexical reference,
and completed jobs do not prove that a workspace is disposable. Runtime receipts,
current jobs and downstream consumers still require review. Preserve JSON audit
evidence, restart/recovery material and regression tests. Production/static/source
data trees are outside this scan. No script is retired merely because its name
includes `test`.

The one-shot `bin/queue_historical_inventory_after_cleanup.sh 5188493` waits for
the already authorized original-snapshot cleanup to finish successfully before
submitting the inventory. This avoids triggering that cleanup's unknown-job
safety guard. It requires the completed deletion journal, has a five-hour wait
limit, uses no reservation, and saves the submission ID in
`forcing/status/cleanup/historical-inventory-after-5188493.submission.txt`.
The waiter does not delete anything or enable cron.

### Reviewed follow-up deletion scope

Inventory **5188557** completed without scan errors. The next authorized pass
uses `bin/cleanup_reviewed_benchmarks.py` through its matching SLURM launcher.
Its explicit twelve-root allowlist covers the static-mask scaling/parallel tests,
September 14/16 post-2020 production benchmarks, September 15 PRISM-window
benchmark, September 18 baseline worker/chunk benchmarks, doubled-worker test,
daily/parallel summary benchmarks, and Stage-IV lookback/downstream benchmarks.
Only regular, single-link array files that still match inventory 5188557 are
eligible. Preserve every report and unknown file, links, restart/input material;
verify retained report checksums before and after deletion. Each execution has a
new `reviewed-benchmarks-JOB.json` plan and `.journal.jsonl` under
`forcing/status/cleanup/`. Only the completed journal establishes deletion.
Per-root `CLEANUP_COMPLETED.json` notices identify historical results afterward.

The roughly 75.5-TB `prism-rain-pipeline` tree is **not** covered: publication,
boundary-repair and retry tools reference it, and it needs per-replacement
lineage review. Repair waves, NRT campaigns, summary backups, current operational
revision tests, model tests/recovery/restarts and all production/static/input
trees remain excluded. The September 13 post-2020 benchmark reference is also
retained because `benchmark_chunk_calendar.py` explicitly consumes it.

The inventory's script list is a discovery aid, not an obsolete-code list.
For example, `benchmark_static_mask_chunks.py` is imported by current operational
validation, and names containing `latest` can match the lexical `test` filter.
Keep these helpers and regression tests. No script is deleted in this pass;
script retirement requires call-site and Git/uncommitted-change review first.

The read-only precipitation-pipeline follow-up is job **5188875**, submitted via
`slurm/review_precipitation_pipeline.sh`. It checks inventory 5188557 array
identities and current canonical replacement publication evidence, mapping each
12–12 UTC window to both adjacent calendar days. Results are
`forcing/status/cleanup/pipeline-review-5188875.json` and `.progress.json`.
It distinguishes exact published candidate paths from superseded candidates,
retains JSON reports, and blocks missing replacement/boundary evidence. Prior
scientific audits are reused, not recomputed. This is a review, not a deletion
manifest: fresh tree/consumer checks and explicit retry retirement are still
required before any numerical pipeline files can be removed.

Review 5188875 completed in 6m20s: 50,890 arrays (75,543,167,526,400 allocated
bytes) have accepted replacements. Four boundary arrays are retained: the
January 1, 1981 window/diagnostic pair in `5092482_0` (December 31 has a different
publication lineage), and the April 19, 2026 pair in `5174017` (beyond the Retro
endpoint). All 17,715 evidence/other files are retained as well.

The user authorized this exact array scope. `bin/execute_pipeline_cleanup.py`
and `slurm/execute_pipeline_cleanup.sh` perform whole-tree inventory comparison,
replacement/evidence revalidation, single-link exact-path checks, reviewed-job
checks, and hold the precipitation-publication lock. They save a separate
`pipeline-deletion-JOB.json` plan and durable `.journal.jsonl` before unlinking.
Per-campaign `RETIRED.json` markers disable historical candidate publication
and reference-trial reuse; canonical audit readers retain their JSON evidence.
Original-snapshot-based retries were already retired by the preceding cleanup.
Future campaigns must rebuild inputs, not reuse retired numerical intermediates.
No directories, canonical products, source inputs or model states are deleted.
An interrupted run requires explicit journal review rather than blind resubmission;
only its final `completed` event confirms deletion and retained-evidence checks.
Deleted arrays have no cleanup backup and require regeneration to recover.
Execution job **5188912** was submitted after 716 tests passed (four skipped),
with four CPUs and a six-hour limit. Its journal will be
`forcing/status/cleanup/pipeline-deletion-5188912.journal.jsonl`; submission is
not a claim of completed deletion.

Job **5188912** recorded all 50,890 unlinks but failed its final retained-file
comparison. The initial inspection found a client-local `st_dev` mismatch; this
alone is not proof of changed data. Finalization job **5190258** rechecks every
deleted path, retained stable identity and retained JSON hash. Only `device` may
differ; that attempt also treated allocation changes as failures. If
accepted, it finalizes retirement markers and saves a separate report at
`forcing/status/cleanup/pipeline-finalization-5190258.json`. It performs no new
deletions and preserves the original job/journal failure history.

Job 5190258 checked all 17,719 retained files and all retained JSON proofs, but
failed because 7,515 files had different allocated-block counts. No JSON hash,
inode, logical size, timestamp or link-count mismatch was reported. The corrected
finalizer excludes both mount-local device IDs and allocated storage bytes from
the integrity gate, recording them as diagnostics instead. Path, inode, logical
size, mtime, link counts and JSON hashes remain mandatory. Allocation is a space
estimate, not content identity. The retry performs no new numerical deletions.
Corrected finalization was submitted as **5191360**; the authoritative outcome
will be `forcing/status/cleanup/pipeline-finalization-5191360.json`.

Reviewed 2026-09-27. This is a navigation/safety index, not a new scientific
acceptance claim. Job-specific reports describe the code, inputs and policies at
that run; a `passed` receipt does not certify today's configuration.

## Start here for current operations

| Purpose | Current reference / entry point |
| --- | --- |
| NRT acquisition, extension and revisions | [NRT operations](nrt_gfs_operations.md), `bin/update_nwm_forcing.py` |
| Schedule and cron environment | `cron/hydro_ops.crontab.in`, [cron wrapper](cron_environment.md) |
| Forcing coverage/status | `bin/report_forcing_status.py` |
| Daily/monthly forcing summaries | [Temporal summaries](forcing_temporal_summaries.md) |
| CNRFC propagation | [CNRFC operations](cnrfc_forcing_operations.md), `bin/submit_cnrfc_sync.py` |
| CNRFC model/restart setup | [Domain](cnrfc_domain_boundary.md), [restart subsetting](nwm_restart_subsetting.md) |
| Model output correctness and recovery | [CHRTOUT recovery](chrtout_archive_recovery.md), [CONUS runs](conus_retro_simulation.md) |
| Time conventions | [UTC interval boundaries](nwm_time_conventions.md) |
| Regression tests | `python -m pytest tests` (no production submission) |

The latest staged-revision experiment is documented in
[revision parallelism](nrt_revision_parallel_benchmark.md). Its 4/4/2 configuration
is adopted, but its saved arrays remain experiment data, not published forcing.
The CNRFC model test scripts are retained; review their dates, restart donors and
isolated output locations before submitting them. No expensive test was submitted
as part of this cleanup.

## Retired launchers

Five obsolete launchers have been removed from the working tree, including the
temporary archived copies. Their source is retained in Git revision `7761087`
under `slurm/`; for example, inspect it with
`git show 7761087:slurm/test_nrt_baseline_schema.sh`. No historical launcher needs
to remain runnable just to preserve its implementation.

| Filename | Why retired |
| --- | --- |
| `test_nrt_baseline_schema.sh` | One-date September 15 mixed-schema experiment; canonical schema is now established. |
| `test_nrt_schema_operational.sh` | One-off repair that can write production September 15 data; not a generic safe test. |
| `test_hourly_layout_nwm.sh` | Completed directory-cutover gate, tied to an old campaign's 1985 receipt. |
| `test_nwm_nrt_midatlantic_20260310_48h.sh` | Uses March 9 23 UTC initialization and an old distance-based filling policy. |
| `test_nwm_monthly_prism_periods.sh` | Legacy Mid-Atlantic template and distance-based forcing filling; not current model acceptance. |

No active or pending job referenced these launchers in the scheduler inventory
at review. Current NRT controllers and CNRFC/CONUS production entry points were
left untouched. Unit tests remain active even when they cover historical bugs.

## Results: preserve evidence, distinguish it from products

- Published forcing lives under `forcing/outputs/{domain}/{stream}/{hourly,daily,monthly}`.
- `forcing/work/` contains intermediates **and** experiments; it is not a product archive.
- `nwm/outputs/tests/` contains test results, not production simulations.
- `nwm/recovery/` and all restart collections are protected recovery evidence.
- Historical reports remain at their original paths so provenance links still work.
  Selected superseded result directories carry `HISTORICAL_RESULTS.md`; the
  original machine-readable receipts remain, even when disposable arrays are removed.
  Receipts may therefore reference deleted historical arrays; they are evidence,
  not a current file-availability inventory.
- Unmarked work directories are **unclassified**, not implicitly approved/current.
  Do not delete work directories merely because their jobs have ended: benchmark
  chains and recovery tools can use old results as inputs.

The follow-up cleanup uses `bin/cleanup_historical_experiments.py`: a fixed-scope
plan/execute tool for the nine reviewed directories below, not a general cleaner.
It records exact paths and file identities in
`forcing/status/cleanup/historical-experiments-20260927.json`, retains and hashes
reports, and records deletion intents/completions in the adjacent `.journal.jsonl`.
Only a completed journal is evidence that deletion finished. Unknown file types,
symlinks, hard links and restart/input material are retained. Logical bytes removed
are reported; actual filesystem space reclaimed may differ due to snapshots.
Further data cleanup must inventory dependencies and retained receipts, inspect
live jobs, and produce another explicit deletion manifest.
In particular, do not remove the staged-revision reference/cache trees, old
CHRTOUT recovery material or protected restart hard links.

Completed 2026-09-27 at 18:43 UTC: **20 experimental array files removed,
62,567,988,572 logical bytes (58.27 GiB)**. All 109 retained files passed identity
checks and retained reports passed checksum verification after deletion. Six
affected result roots have `CLEANUP_COMPLETED.md` notices; three reviewed roots
had no eligible arrays. The removed arrays have no cleanup backup and would
require regeneration. The five removed launchers are recoverable from Git
revision `7761087`. Production forcing, model outputs outside these test roots,
restart files, recovery files and current benchmark reference/cache trees were
not deletion targets.

### Result directories labeled in this pass

Each path below now contains `HISTORICAL_RESULTS.md`. These local notices live
beside ignored runtime data; this tracked list records where they were placed.

```text
forcing/work/nrt-schema-v1-4626557
forcing/work/nrt-schema-v1-4626634
forcing/work/nrt-efficiency-4629922
forcing/work/post2020-stage-profile
forcing/work/precipitation-multiday-benchmark
forcing/work/chunk-calendar-benchmark
forcing/work/retro-prism-optimization-20260915T015927
nwm/outputs/tests/mid_atlantic/nrt_20260310_48h
nwm/outputs/tests/mid_atlantic/monthly_prism
```

## Documentation lifecycle

Documents marked **Historical experiment/recovery record** retain old commands,
job states and timing observations for traceability. Those statements are not
instructions for current operations. Their banners link back here and to the
current operational sources. Old benchmark timing is not a current service-level
estimate; workload, caches, source availability and resource settings differ.

New experiments should record the Git revision, inputs/masks, dates, resources,
isolated output root, acceptance criteria, and a current/superseded designation.
Promote implementation and documentation explicitly after acceptance, rather
than treating every saved pilot as another supported operational workflow.
