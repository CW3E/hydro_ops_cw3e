# Precipitation repair rollout

## March–April 2026 Retro coverage diagnosis

Repair array 5174006 completed tasks 0–61 but tasks 62 (March 2026) and 63
(April 1–17) failed during final calendar-day rainfall validation. All 32/18
PRISM windows passed their constraint checks; that does not imply active-cell
coverage and static-envelope compliance. The boundary audit 5174007 and all
downstream publication/summary jobs remain gated; no publication bypass is allowed.

Read-only diagnostic array **5177448** scans every candidate hour in both periods,
counts missing active-cell values, finite values outside the keep envelope, and
negative rain separately, and compares offending values with the preserved
originals. It writes per-day/hour counts, representative coordinates, inherited
versus introduced failure counts, and union masks under
`forcing/work/rain-coverage-diagnosis/5177448_{0,1}/`.
`diagnosis_complete` means the scan finished, not that the forcing passed.
The diagnostic does not modify production files, candidates, masks or job gates.

The diagnostic completed: all active rainfall was finite and no negative values
were found. March 1–2 passed; every hour March 3–April 17 retained 2,869,865
finite cells outside the keep envelope, inherited from the originals. The
intended Retro mask policy had not been enforced on those original rain fields;
the strict writer assumed compliant originals and correctly refused publication.

`rain_envelope_writer.py` now provides an isolated assembly adapter: project
scratch copies of accepted windows onto the existing keep mask, leave retained
rain unchanged, set excluded timing IDs to unconstrained, and run the existing
strict calendar writer/readback/raw-chunk nonrain checks. Original scientific
windows are not modified, so adjacent-month window equality can still be audited.
Missing active rainfall or negative retained rainfall still fail closed. This is
not a new geographic policy or a relaxation of acceptance thresholds.

Two-day canary **5177638** checks March 3 and April 1, including a full scan of
nonrain active coverage and envelope compliance. The subsequent monthly
reassembly is gated on its acceptance. `bin/reassemble_retro_envelope.py`
preserves each failed acceptance report and installs an amended month report
only after every rebuilt calendar file passes. Existing accepted days/months,
original forcing snapshots, and accepted PRISM windows remain available.
New evidence is under `forcing/work/retro-envelope-reassembly/<job>/`.

Submitted reassembly array **5177639**, tasks 62–63, afterok:5177638.
The existing boundary audit **5174007** was reconnected to afterok:5177639
instead of the failed original array. Its downstream canary, publication,
subsetting and summary dependencies remain intact. This does not bypass the
scientific-window or publication audits. Ledger:
`forcing/work/repair-submissions/retro-envelope-reassembly-5177638.json`.
Regression tests: 688 passed, four skipped; focused masking tests verify exact
retained rainfall, immutable source windows, unchanged nonrain compressed chunks,
and rejection of missing/negative retained rainfall.

### Publication metadata recovery

Reassembly 5177639 and boundary audit 5174007 passed. Publication array 5174009
completed January 2021–February 2026, but March/April tasks failed with
`KeyError: static_envelope`: historical manifests lacked that section. Several
CONUS files had already been transactionally replaced before metadata/subsetting
stopped. Originals, candidates and transaction receipts are retained; these days
must be resumed through publication, not refitted or treated as fully published.

The publisher now validates metadata prerequisites before replacement. Missing
historical envelope metadata requires explicit accepted envelope-reassembly
proof; it constructs current mask provenance and rain-only verification scope
without fabricating historical/full-field audits. Existing transaction receipts
allow already-replaced files to retain their identity while metadata and nested
subsets are completed. Regression coverage includes absent historical metadata,
post-replacement subset failure/retry, and fail-closed missing evidence.
Recovery canary **5179418** resumes March 3 before the monthly retry array.
Retry **5179420**, tasks 62–63, depends on that canary. Summary jobs
**5174010–5174016** were reconnected to afterok:5179420 after verifying passed
publication/subset reports for all **1,885 days** January 2021–February 2026.
No successful months are reprocessed. The new ledger is
`forcing/work/repair-submissions/retro-publication-metadata-retry-5179418.json`.
Full regression verification: **690 passed, four skipped**; Ruff/diff checks passed.

## NRT archive repair using latest downloaded PRISM

### November 5 retry: hourly versus daily NLDAS storage

Array 5174417 passed March–July but failed August–October because the candidate
adapter assumed every NLDAS input was a daily collection under YYYY/. The
August 18 and August 31 data actually have all 24 hourly files under YYYY/DDD/;
October 27 has hourly records through 12 UTC. This was principally a path
resolution bug, not evidence that the dates were absent from the source archive.
Publication 5174434 never ran; its afterok gate blocked it and summaries 5174435.

The NRT adapter now resolves the existing hourly/daily layouts and checks exact
timestamps. Missing records remain NaN, never zero. The established allocation
policy still prefers usable archived forcing timing, then a complete NLDAS
profile, then uniform timing while preserving the prescribed PRISM depth.
Malformed existing inputs still fail closed. Diagnostics distinguish used files,
missing files, and missing timestamps.

To avoid changing code pinned by active Retro jobs, this change is isolated in
`nrt_support_production.py` (a snapshot of the existing support adapter with only
the timing loader changed) and `nrt_rain_timing.py`. Consolidate the adapters
after the pinned Retro campaign finishes rather than evolving two solvers.
Failed task reports are archived by job ID before explicitly authorized retries;
successful March–July candidates and all original forcing snapshots are retained.
Publication continues to require all eight accepted monthly reports and matching
shared boundary windows. Cron remains paused.

Retry array **5174780** runs only tasks 5–7 (August–October), with at most
three 64-CPU workers. The never-started blocked jobs 5174434/5174435 were
cancelled and replaced by publication/subsets **5174781** (afterok:5174780)
and summaries **5174782** (afterok:5174781). Submission ledger:
`forcing/work/repair-submissions/nrt-latest-prism-retry-5174780.json`.
Verification: 683 tests passed, four skipped; targeted loader checks read the
actual hourly source files for all three originally reported missing dates.

The user explicitly confirmed that both streams should use the latest downloaded
PRISM release, not freeze historical provisional releases. Existing NRT contains
241 constrained daily collections (2026-03-02–2026-10-28), plus three unconstrained
newest days which this precipitation repair leaves untouched.

Isolated latest-release pilots **5174409** (April 12–13) and **5174410** (October
15–16) passed. `bin/test_nrt_latest_prism_repair.py` freezes the original NRT
inputs and copies each PRISM target before processing. It records the downloaded
release sidecar, original source identity, frozen target SHA256, and actual
release date. The revision tag is `latest_available`, not an inferred claim of
stable/provisional status. The support-aware algorithm and raw-chunk non-rain
verification are unchanged. These jobs did not alter production.

The dependent candidate array **5174417** covers March 3–October 28 (240 days,
eight monthly tasks, up to four simultaneous 64-CPU workers). Publication job
**5174434** waits for all candidates, verifies identical shared monthly windows,
and runs a serial first-day publication/subset canary before four day workers.
`bin/publish_nrt_latest_prism_repair.py` uses the existing intent-journaled atomic
replacement primitive, requires unchanged original identities and PRISM source
identities, retains originals/previous manifests, updates NRT receipts, and
rebuilds CONUS→CNRFC→B120 and CONUS→UCRB→Yampa subsets. Old static-envelope and
override attestations are historical only, not reused for changed rainfall.

Summary job **5174435** waits for successful hourly publication/subsets. It uses
the existing NRT signature-based summary refresh, then refreshes all four summary
subsets. This first NRT repair refreshes changed summaries with the general
reducers rather than the Retro-only precipitation-summary shortcut. Job-specific
campaign reports are under `forcing/work/nrt-latest-prism-campaign/`; candidate
reports are under `forcing/work/nrt-latest-prism-repair/job-<id>/`.

March 2 has no March 1 NRT/baseline predecessor. Separate candidate-only job
**5174437** explicitly uses the preserved original Retro March 1 file from wave7
as its predecessor; the override is recorded, never silently selected. It does
not publish March 2. Review that boundary and the shared March 3 window before
completing the first day.

Cron remains paused. The normal NRT operational constraint path still needs
integration with the validated support-aware method before cron resumes;
otherwise it could overwrite repaired rain with the legacy strategy. The new
monthly revision queue described below is discovery only until replay is tested.

## Monthly PRISM revision discovery (replay not yet enabled)

`update_nwm_forcing.py --cycle monthly-retro` now submits
`slurm/queue_prism_revisions.sh` after source refresh jobs terminate. This runs
independently of Retro promotion success. The scanner records current downloaded
ppt/tmin/tmax source identities and release metadata for the last 400 days, and
coalesces affected existing Retro and NRT calendar days (PRISM day minus one,
and PRISM day) into `forcing/status/prism-revisions/queue.json`. Unknown initial
provenance is pending rather than assumed current. First inventory **5174411**
registered 1,182 sources and 443 pending stream-days; this is not 443 proven
scientific discrepancies. Entries persist across unchanged scans and paused or
failed cycles. An acknowledgement requires matching source signatures plus
accepted hourly, summary and subset work; older acknowledgements cannot clear
newer revisions.

**Automatic queue replay is not implemented/enabled yet.** The report explicitly
states `automatic_replay_enabled=false`. Temperature revisions in this queue
must use the coupled temperature/pressure/humidity/longwave workflow, not the
rain-only repair. Full replay and the corrected operational NRT path remain
validation tasks before reinstalling cron. No current repair/model job was
cancelled or resized. Full tests after the initial queue changes: 677 passed,
four skipped; publication/retry and queue focused tests: three passed.

## Final available Retro batch: 2021–April 2026

The existing Retro hourly archive ends on **2026-04-18** (24 records); it does
not extend to April 30. The approved main repair covers **2021-01-01 through
2026-04-17**, 1,933 days in 63 full months plus a 17-day April task. April 18 is
frozen as boundary input, not published by this batch. No NRT input is silently
substituted into this campaign.

`prism_repair_wave.py prepare --end-date` now supports an inclusive partial final
month, snapshots only through the following boundary day, and records each
task's actual day count. Array publication clips its final task to that count.
Existing full-month behavior is unchanged. All earlier repair/publication arrays
finished before these shared scripts were changed; model jobs are unaffected.

`bin/submit_prism_repair_2021_2026.py` submitted:

- Preparation **5174005**, repair array **5174006** (64 tasks, six concurrent,
  64 CPUs each), boundary audit **5174007**.
- Publication canary **5174008**, publication/four-domain subsets **5174009**
  (eight concurrent 16-CPU tasks, four day workers each).
- Rain-only summaries/four-domain subsets **5174010–5174016**, covering
  December 2020 and years 2021–2026. The preceding 2020 summary writer was
  verified passed before submission. Summary daily coverage ends April 16;
  April 17 needs repaired April 18, and no complete April monthly product is
  claimed. Full monthly summaries extend through March 2026.

Ledger: `forcing/work/repair-submissions/2021-2026.json`; workspace:
`forcing/work/prism-repair-wave7-202101-20260417`. Standard audit, preceding-window
consistency, publication canary and retry gates remain in effect. Originals
remain retained, and no NRTRES reservation is used.

### Separate terminal-day handoff test

Job **5174017** runs `bin/test_retro_april_boundary.py` with 64 CPUs/120 GB scratch.
It freezes original Retro April 17–18 and explicitly overrides only the April
19 boundary with the NRT file. It checks variable shapes/dimensions and runs the
existing full rain-only candidate pipeline for April 18. Its report is
`forcing/work/retro-april-boundary/job-5174017/acceptance.json`; the candidate
pipeline report is `forcing/work/prism-rain-pipeline/5174017/acceptance.json`.
This test **cannot publish production files**. A passed candidate still requires
review of source/provenance and the shared April 18 PRISM window against the
main batch before any separate April 18 publication or deferred daily summary.

Validation: 675 tests passed, four skipped; seven focused tests passed after the
final publication-range change, plus Ruff/shell/diff checks. Submission does not
establish completion or scientific acceptance.

## Submitted 2011–2020 campaign

After all 2001–2010 hourly repairs, boundary audits and four-domain subsets passed,
the user authorized the next **120 months / 3,653 days**. The existing summary
jobs and model reruns are unchanged. `bin/submit_prism_repair_2011_2020.py` records
the chain in `forcing/work/repair-submissions/2011-2020.json`:

- Preparation **5160140**; monthly repair array **5160141**, six 64-CPU tasks.
- Boundary audit **5160142**; publication canary **5160143**; monthly publication
  and four-domain subsetting array **5160144**, eight 16-CPU tasks, four day workers each.
- Rain-only summaries/subsets **5160145–5160155**, December 2010 followed by
  yearly 2011–2020 work, each using 16 CPUs.

Preparation preserves the preceding original December 31, 2010 boundary and
checks the shared PRISM window against the accepted previous wave. All new
publication is gated by the new audit/canary. December 2010 summary completion
additionally waits for **5133411**, preventing overlap with the current summary
writer. Summaries end on December 30, 2020; December 31 daily and December 2020
monthly completion wait for repaired January 1, 2021 in the following campaign.

The first preparation submission was rejected by SLURM because historical audit
job 5133398 had expired from dependency scheduling. No job was created by that
attempt; its ledger is retained as `2011-2020.rejected-expired-dependency.json`.
The submitter now requires the preceding passed audit and complete publication
reports instead of dependencies on those finished jobs. Runtime preparation
still revalidates the preceding snapshot and audit. Active/new dependencies
remain explicit. No NRTRES reservation is requested or inherited.

Five focused tests, Ruff and diff checks passed. These IDs establish submission,
not execution, publication, or scientific acceptance.

## Queued 2001–2010 campaign

The user authorized queueing **120 months / 3,652 days** before the preceding
1991–2000 audit finishes. `bin/submit_prism_repair_2001_2010.py` records the
duplicate-protected submission ledger in
`forcing/work/repair-submissions/2001-2010.json`; artifacts use
`forcing/work/prism-repair-wave5-200101-201012/`.

Submitted: preparation **5133396**, repair array **5133397**, boundary audit
**5133398**, publication canary **5133399**, publication/subset array **5133400**,
and summary/subset jobs **5133401–5133411** (2000–2010). All were pending their
dependencies at submission. Filename preflight found all 3,653 required hourly
collections including January 1, 2011; this is not a content audit. Five focused
tests, Ruff and diff checks passed.

Preparation depends on **5118063** passing and rechecks the preceding acceptance
report at execution time. It preserves the original December 31, 2000 from wave
4's original snapshot and checks exact agreement with its ending PRISM window.
Six concurrent 64-CPU repair tasks may then run while wave 4 publishes. The new
publication canary waits for both its own audit and **5118065**, so bulk hourly
publication campaigns cannot overlap. Eight 16-CPU publication tasks use the
validated four-day workers. No existing jobs are modified.

Eleven dependent 16-CPU summary/year jobs use precipitation-only reuse with full
regeneration fallback and four-domain subsetting. The December 2000 follow-up
also depends on **5118076**, preventing overlap with that year's earlier summary
job. Summary coverage extends through December 30, 2010; December 31, 2010 and
the December 2010 monthly summary remain deferred until January 1, 2011 has been
repaired. Original January 1, 2011 is an input boundary, not a repaired output.

The submission helper strips inherited `SBATCH_RESERVATION` and never requests
NRTRES. Tests verify dependency edges, duplicate-submission refusal, and absent
reservation requests. The active repair/audit scripts are unchanged.

## 1991–2000 combined campaign

The user authorized the next **120-month / 3,653-day** repair block with the new
rain-only summary workflow. Submission is managed by
`bin/submit_prism_repair_1991_2000.py`; its duplicate-protected ledger is
`forcing/work/repair-submissions/1991-2000.json`. Campaign artifacts live under
`forcing/work/prism-repair-wave4-199101-200012/`.

Submitted jobs:

| Stage | Job |
|---|---|
| Freeze originals | 5118061 |
| 120 monthly repair tasks, six concurrent | 5118062 |
| Complete wave/boundary audit | 5118063 |
| Four-day publication canary | 5118064 |
| 120 monthly publication/subset tasks, eight concurrent | 5118065 |
| December 1990 summaries/subsets | 5118066 |
| 1991–2000 annual summary/subset follow-ups | 5118067–5118076 |

At submission, preparation was pending priority and downstream jobs were pending
dependencies. Full suite: 668 passed, four skipped; Ruff, shell syntax and diff
checks passed. Submission does not imply execution or publication acceptance.

Execution order: freeze originals → six concurrent 64-CPU monthly repair tasks →
119 internal month-boundary comparisons plus the preceding-wave comparison →
four-day live publication canary → eight concurrent 16-CPU monthly publication
tasks (four day workers each) → eleven 16-CPU yearly summary/subset follow-ups.
Only successful predecessors release each stage. No existing forcing or spin-up
jobs are cancelled/resized, and no NRTRES reservation is requested.

Original December 31, 1990 is taken from the preceding wave's preserved originals,
not its repaired live file, keeping the shared PRISM boundary deterministic.
Both monthly-tier 1979–1980 and unrelated data remain untouched. The final
January 1, 2001 input is retained as an original boundary, not published as repaired.

Summary follow-ups use `bin/run_rain_summary_year.py`, processing months serially
within a year while separate years run concurrently. Rain-only reuse, full
regeneration fallback, per-file receipts, original backups and four-domain subset
audits are inherited from the tested month controller. The ordered hourly month
locks now wait in this controller so adjacent December/January workers do not
fail on a temporary shared-boundary collision; ordinary hourly publishers retain
their nonblocking lock behavior. Existing year locks still exclude overlapping
summary writers.

The 1990 follow-up handles **December only** and also depends on the existing
1990 full-summary/subsetting chain (5116861). This completes the previously
deferred December 31 daily and December monthly summaries. 1991–1999 get all
daily/monthly summaries. In 2000, daily summaries stop on **December 30** and
monthly summaries on **November**. December 31 and December's monthly summary
remain explicitly deferred until the repaired January 1, 2001 boundary arrives.
Partial final-month support never publishes a partial monthly mean.

## Precipitation-only summary optimization (publication testing)

Benchmark 5116870 **passed** in 23m48s. Daily rain-only replacements took
20.9/18.1 seconds versus 220.6/214.5 seconds for full regeneration of October
1/31 (10.6–11.9× faster); monthly replacement took 80.4 seconds versus 212.0
seconds (2.6× faster). Both daily references and the monthly reference matched
all variables/missing masks exactly; all 31 daily non-rain datasets were preserved.
These are candidate timings, not complete publication/subsetting throughput.

### Guarded publication integration

`bin/refresh_rain_repair_summaries.py` and its SLURM wrapper provide a complete-month
follow-up after hourly repair publication. Production is explicit (`--production`);
otherwise `--outputs-root` must be an isolated directory under `forcing/work`.
The required `--workspace` retains original summaries, immutable candidates,
candidate audits, publication intent/receipts, and monthly status. Both the month
and its next-month midnight source must have accepted rain-only hourly repairs.
The runner follows the existing yearly summary locks and domain subset locks,
and holds hourly-repair month locks through the boundary month. Do not launch
overlapping month tasks in the same year concurrently; they fail closed on locks.

It preserves all original daily summaries before replacing any, links the old
daily summary provenance to current or preserved-original hourly identities,
and reuses non-rain values only with compatible signatures and accepted rain-only
source changes. Missing/incompatible originals trigger full regeneration. If any
daily file takes that fallback, the monthly file also uses full regeneration.
Published CONUS summaries are subset through CONUS→CNRFC→B120 and
CONUS→UCRB→Yampa, with full existing subset audits.

Each replacement retains a recovery hardlink, records intent, checksum-verifies
the transferred candidate, atomically replaces the file, then commits its receipt.
A crash between replacement and receipt is recoverable; retries verify current
input identities and candidate checksum and do not rewrite committed files.
Conflicting inputs, externally changed outputs or incompatible workspace
configuration block a retry for inspection. This is per-file publication, not
an atomic whole-month/domain snapshot. Monthly status is passed only after all
daily/monthly publications and four-domain subsets pass. Old generic pending
markers are not globally cleared; the job-specific status is authoritative for
this bounded refresh.

For a future combined campaign, submit this follow-up with `afterok` on the hourly
publication array, rather than inside individual hourly-month tasks: otherwise
the neighboring midnight file may not yet be published. Include the preceding
deferred summary month when its repaired boundary becomes available; defer the
last month if the next midnight is still unrepaired. Existing full-summary jobs
5116852–5116861 remain untouched. No broad deployment or cron change is made
before the isolated publication/subsetting/retry test passes.

Integration test **5118047** uses 16 CPUs and a four-hour limit. It seeds an
isolated October 1986 archive from benchmark-pinned old summaries, deliberately
interrupts the first daily replacement before receipt completion, resumes the
whole month and all four subdomains, compares full-grid first/last daily and
monthly outputs to the benchmark oracles, then retries to verify that all 160
published file identities remain unchanged. Report:
`forcing/work/rain-summary-publication-test/job-5118047/acceptance.json`.
Pre-submission unit checks passed; the full suite completed with 664 passed and
four skipped. Submission is not integration-test acceptance.

The user approved exploring reuse of non-precipitation daily/monthly summaries,
and eventually combining summary updates with hourly repair publication. The
existing 1986–1990 full-summary jobs remain running until this alternative is
validated; no running publisher/reducer was changed.

`rain_summary_repair.py` creates isolated summary candidates by copying an old
summary and replacing only RAINRATE. It checks reducer/schema/time-bound
compatibility and matches the old summary's signed source inventory to preserved
original input identities. Its caller must additionally prove that current
inputs changed only in precipitation. Incompatible provenance must use full
regeneration, never silently reuse non-rain fields. New provenance/signatures use
the existing full-summary contract, while an explicit attribute records reuse.
Hourly endpoint means follow 01–00 UTC; monthly means use complete daily records
with equal 24-hour durations. Missing values propagate, not become zero.

Every candidate checks precipitation against a full-field float32 readback hash
and verifies every non-rain dataset, using compressed payload comparison where
possible. The guarded publisher described above is now implemented but remains
under isolated integration testing. Failed candidates are not accepted or used
as inputs to operations.

Benchmark **5116870** (`slurm/benchmark_rain_summary_repair.sh`) uses 16 allocated
CPUs, one reducer worker and a four-hour limit. It pins October 1986 originals,
old summaries and corrected hourly inputs, verifies the accepted hourly rain-only
repair chain, then updates 31 daily summaries. Full all-variable regeneration of
the first/last days provides independent full-grid numerical comparisons. A
monthly precipitation-only replacement is compared across all variables with a
full monthly reduction of the updated daily files. The benchmark uses permanent
storage for both paths so timings include normal file I/O; no live summary is
modified. Results: `forcing/work/rain-summary-benchmark/job-5116870/acceptance.json`.
Pre-submission checks: 16 focused tests, Ruff, shell syntax and diff checks passed.

The integrated publication gates follow hourly → daily → monthly → subdomain
summaries (individual daily subsets may publish before the monthly summary).
An hourly day changes its
own and the preceding interval-end daily summary. Finalize a month only when all
its days and the next-month midnight boundary are repaired. Keep per-resolution
publication receipts and retries; do not claim whole-directory atomicity. Pin or
preserve the old summaries needed to prove reuse before replacing them. Existing
full regeneration remains the required fallback for missing/incompatible inputs.

## November 2: wave 3 published and summary refresh submitted

The February 1986–December 1990 wave completed: all 59 repair tasks (5107952),
boundary audit 5107953, live four-worker publication test 5107954 and all 59
publication tasks (5107955) passed. All 58 internal month boundaries and the
preceding-wave boundary matched exactly. A read-only receipt/identity scan checked
1,795 current files in each of CONUS, CNRFC, B120, UCRB and Yampa (8,975 total),
including current parent identities, with zero errors. Publication/subsetting
took 2h34m42s wall time, averaging about 19 minutes per monthly task. January
1986 was already published in wave 2. Originals remain retained.

Summary replacement jobs use the established audited, atomic stale-retro
replacement path and 16 allocated CPUs/eight workers per CONUS year. Four-domain
summary subsetting follows each successful annual job, using four workers and
16 CPUs. The five chains can run concurrently (80 allocated CPUs at each stage).

| Year | CONUS daily/monthly refresh | Dependent four-domain subsets |
|---|---|---|
| 1986 | 5116852 | 5116853 |
| 1987 | 5116854 | 5116855 |
| 1988 | 5116856 | 5116857 |
| 1989 | 5116858 | 5116859 |
| 1990 | 5116860 | 5116861 |

1986–1989 cover complete years. **1990 stops at December 30 for daily summaries
and November for monthly summaries.** December 31 and the December monthly
summary need the corrected January 1, 1991 midnight record. Their old files are
not refreshed or represented as current by this campaign. Track this explicit
deferral in `forcing/work/repaired-summary-submissions/1986-1990.json` and revisit
it when the next hourly repair wave publishes. Do not clear all derived-pending
markers merely because these jobs complete.

`bin/submit_repaired_summaries_1986_1990.py` records submission intent/results and
rejects duplicate submission. `bin/subset_repaired_summaries.py --year YYYY --end
YYYY-MM-DD` now supports bounded years after 1985 and excludes incomplete months.
Focused tests cover normal/leap years and the unrepaired ending boundary.

The monthly-task repair controller has no five-year restriction. Ten years
(120 monthly tasks) fit the site's MaxArraySize=1001 limit. Batch length does not
increase simultaneous CPU usage; array concurrency controls that independently.
A 1991–2000 next wave is reasonable, but is **not submitted** by the summary
refresh. Larger waves retain more candidates/originals and delay all publication
until the whole-wave audit passes. Incremental audit/publication groups would be
needed to shorten that wait without relaxing acceptance gates.

## Scope and gates

The support-aware, bounded-cost precipitation repair replaces only RAINRATE;
seven other meteorological fields retain their original compressed chunks.
Strict and direct-conservative fallback acceptance remain distinct. Shared-area
volume conservation does not imply exact agreement in every PRISM or basin cell.
Scientific trial results are recorded in `nwm_land_climatology.md`.

Cron and model consumers remain paused during this rollout. An accepted hourly
file does not imply that its daily/monthly summaries or model simulations are
current. No original snapshot is deleted by the publication runner.

## Publication

`bin/publish_prism_repair_wave.py` is deliberately scoped to two accepted waves:
January–August 1981 and September 1981–January 1986. It is not a general
production scheduler. Select the second with `--wave wave2`; `--array-month`
maps SLURM tasks 0–52 to its calendar months.

1. Require passed wave and candidate audits; preserve original data in hard-link
   snapshots and save the previous CONUS publication manifest.
2. Pin checksums, original identity and audit evidence in a durable per-day plan.
3. Hold shared CONUS repair and four-domain subsetting locks, plus exclusive
   CONUS calendar-month locks. Different months can publish concurrently;
   overlapping months and existing exclusive cron/backfill writers cannot.
4. Journal intent, checksum a staging copy and atomically replace the CONUS file.
   Interrupted publication reconciles the intent; retry never silently accepts
   an unrelated replacement.
5. Update the production manifest and current static-envelope audit identity.
   Old retained-value-unchanged claims are not carried forward as true for rain.
6. Re-subset in the established parent-first hierarchy: CONUS → CNRFC → B120
   and CONUS → UCRB → Yampa, including full subset acceptance and retry-safe
   source identities. Children use their newly published parent's identity.
7. Record pending daily/monthly reductions. Changed hour 00 also affects the
   previous interval-end day, including its month when crossing a boundary.

Pending-summary records are an explicit work queue, not an automatic summary
rebuild or a consumer-enforced invalidation mechanism. Do not resume operations
or declare the archive fully current until that queue is processed and audited.
The first live canary and subsequent rollout retain snapshots and candidates.
Storage reclamation requires a separate audited deletion plan and confirmation
that all adjacent-window consumers have finished. Existing model restarts and
outputs are preserved.

## Launch record — October 31, 2026

| Job | Work | Gate |
|---|---|---|
| 5093027 | Freeze August 31, 1981–February 1, 1986 original inputs | Completed |
| 5093028 | Publish January 1–2, 1981 and subset four domains | Successful snapshot preparation |
| 5093029 | Publish remaining January–August 1981 accepted candidates and subsets | Successful live canary |
| 5093030 | 53 monthly repair tasks: September 1981–January 1986 | Successful live canary |
| 5093031 | Candidate completeness and exact shared-window boundary audit | All 53 tasks successful |

The array uses seven concurrent 64-CPU tasks (448 CPUs), with 120,000 MB scratch
per task. The publisher uses 16 CPUs: peak combined allocation is 464 CPUs,
below the approved 512-CPU budget. Each monthly task has a four-hour limit.
The publication job has a 24-hour limit. January 1986 is included as a full
month so the 1981–1985 spin-up has its required end-boundary forcing.

Wave 2 produces isolated candidates, not automatic live publication. Its
53-month boundary audit must pass before a later publication phase. Publication
of wave 1 and calculation of wave 2 can overlap because wave 2's original
boundary inputs were frozen first. No model rerun or snapshot cleanup is
submitted by this launch.

Reports:

- `forcing/work/prism-repair-wave1-198101-198108/production/job-<id>.json`
- `forcing/work/prism-repair-wave1-198101-198108/production/<day>.transaction.json`
- `forcing/work/prism-repair-wave1-198101-198108/production/<day>.derived-pending.json`
- `forcing/work/prism-repair-wave2-198109-198601/plan.json`
- `forcing/work/prism-repair-wave2-198109-198601/acceptance.json`
- `forcing/work/prism-rain-pipeline/5093030_<task>/acceptance.json`

Local checks before submission: 638 tests passed, four skipped; changed Python
files passed Ruff; shell syntax and diff checks passed. Submission is not
publication acceptance; inspect the job-specific reports above.

### Canary correction

Job 5093028 stopped after publishing January 1 CONUS and CNRFC. B120's receipt
correctly rejected a change of source from CNRFC to CONUS. The publisher now
preserves the existing hierarchy rather than relaxing provenance checks. Retry
reuses the accepted CONUS publication and skips the already-current CNRFC subset.
Summary-pending records are written before subsetting so a child failure does
not omit that work item. Regression tests cover parent-first ordering, current
parent identities, retry skips and stopping after a failed parent. Original
snapshots remain protected; wave 2's pinned calculation code is unchanged.

Retry job **5094060** runs the same January 1–2 live canary. Existing jobs
5093029 and 5093030 were reattached with `afterok:5094060`; they were not
duplicated. The final audit 5093031 still depends on successful completion of
5093030. Check `production/job-5094060.json` for the retry result.

## Second-wave publication — November 1, 2026

Wave 1 completed: 243 days published for CONUS and all four subdomains.
Wave 2 passed all 53 monthly candidate audits (1,614 days), and all 52 shared
window comparisons were exact. Publication may now proceed from those accepted
candidates without recalculating precipitation.

A one-day September 1 live check gates a 53-task monthly publication array,
with eight concurrent tasks, each using 16 CPUs and 40,000 MB scratch. Total
publication concurrency is 128 CPUs. Each task retains the 24-hour time limit.
The September task safely revisits the canary day using the existing transaction
and subset receipts. All four domains retain their established parent hierarchy.
This is the first eight-way publication run; its speed is not yet benchmarked.

Submitted live check **5102388** and publication array **5102389**, with the
array gated by `afterok:5102388`. At submission the live check was waiting for
ordinary partition resources; no reservation was requested.

Daily/monthly summaries and model runs remain pending; originals and candidates
are not deleted by these jobs. A successful task records every published day
and four-domain subset result under the wave's `production/job-<SLURM_JOB_ID>.json`.

### Publication throughput investigation

Early wave-2 receipts (1,085 newly published days) averaged 49.5 seconds for
CNRFC, 13.7 for B120, 29.2 for UCRB, and 4.4 for Yampa: about 97 seconds/day
in subsetting alone. Sample completed 16-CPU tasks used 57–64 CPU-minutes over
65–79 elapsed minutes, indicating roughly one busy core per task, not 16.
These figures include full subset validation; they do not isolate compression
from readback and shared-storage latency.

With user approval, array 5102389's throttle was increased from eight to sixteen
monthly tasks (256 allocated CPUs). No running publication code was changed.
JobArrayTaskLimit for the remaining months is expected throttling, not failure.

Sandbox benchmark **5103157** (`bin/benchmark_prism_publication.py`) compares
four already-accepted days at 1, 2 and 4 concurrent day workers, retaining all
existing subset audits and the parent-first hierarchy. It also measures a
verified full-file copy against a verified hard-link publication on the same
filesystem. The link experiment is not a production transaction implementation
and does not relax checksum/receipt/rollback checks. Any later adoption needs
an explicit immutable-candidate contract and crash/retry tests. Results belong
in `forcing/work/prism-publication-benchmark/5103157/acceptance.json`.

This benchmark runs alongside the live array and therefore measures contended,
cache-sensitive performance, not an isolated filesystem benchmark. Its 16 CPUs
bring maximum combined requested concurrency to 272 CPUs, below 512. Prefer
increasing useful day-level concurrency over allocating more idle CPUs to a
serial publisher. Summary regeneration and original cleanup remain separate.

## Summary replacement, restart checks and monthly-tier investigation

Wave 2 publication completed with all 53 tasks successful in 7h45m. A read-only
post-publication scan checked all 1,614 days in each of CONUS, CNRFC, B120, UCRB
and Yampa (8,070 files): current output identities and subset parent identities
matched their receipts, with no missing days or mismatches.

The summary runner now recognizes the current rain-repair publication chain:
current CONUS identity, published transaction, pinned plan and candidate audit,
active rain coverage, outside-envelope masking and unchanged non-rain fields.
It does not pretend that repaired rain is unchanged from the original archive.
`--replace-stale-retro` explicitly permits atomic replacement of stale summaries;
the default retro behavior remains refusal. Existing reducer/time-bound semantics
are unchanged.

Submitted jobs:

| Job | Scope | Gate |
|---|---|---|
| 5106711 | January 1981 CONUS summary replacement check | Current hourly audit chain |
| 5106717 / 5106718 | 1981 summaries / four-domain summary subsets | Canary / annual summary success |
| 5106719 / 5106720 | 1982 summaries / subsets | Same |
| 5106721 / 5106722 | 1983 summaries / subsets | Same |
| 5106723 / 5106724 | 1984 summaries / subsets | Same |
| 5106725 / 5106726 | 1985 summaries / subsets | Same |
| 5106713 | CNRFC cold/warm ingestion test, 64 MPI ranks | Isolated test outputs |
| 5106714 | B120 cold/warm ingestion test, 32 MPI ranks | Isolated test outputs |
| 5106715 | UCRB cold/warm ingestion test, 64 MPI ranks | Isolated test outputs |
| 5106716 | Yampa cold/warm ingestion test, 32 MPI ranks | Isolated test outputs |
| 5106712 | 24 monthly audits/proposed spatial corrections, 1979–1980 | Four concurrent 16-CPU tasks |

Annual summary tasks use eight workers/16 allocated CPUs. Their dependent subset
tasks use four day/file workers/16 CPUs, preserving the parent-domain hierarchy
and full subset audits. Disjoint years use separate year locks; existing exclusive
backfill writers remain excluded. Reports are in the job-specific summary logs
and `forcing/work/repaired-summary-subsets/job-<id>.json`.

This summary campaign deliberately stops at December 31, 1985, whose required
January 1, 1986 boundary is repaired. January 1986's final summary also needs
February 1, which is not repaired yet. The affected December 31, 1980 summary
and December 1980 monthly product belong with the pending monthly-tier work.
Do not mark all pending-summary records resolved merely because these five
annual tasks complete.

Model checks use January 1–3, 1981 as a two-day continuous cold start, then
resume at January 2 with `rst_typ=1`, `RSTRT_SWC=0`, `GW_RESTART=1`. Final restart
states must agree within the established tolerances. Daily land and both channel
output modes are checked. Reports are
`nwm/outputs/<domain>/tests/repaired-spinup-<job>/acceptance.json`.
No old production outputs/restarts are removed, and the five-year spin-up chains
are not submitted before these gates pass.

The monthly investigation reads current stored precipitation, not deleted
baselines. It checks exact calendar-hour coverage, active-cell validity, and
monthly totals reconstructed on PRISM's grid. January 1979 retains 11 hours on
its first day: 731/744 of its PRISM monthly target, not a synthetic full day.
The bounded spatial solver proposes monthly depths with conservative fallback
where necessary, without altering temperature or any production file. Results:
`forcing/work/monthly-rain-repair/5106712/YYYYMM/acceptance.json` and
`proposed_monthly_depth.nc`. A passed investigation means the audit/proposal ran
successfully, not that old precipitation passed closure or that hourly repair is
ready. Timing allocation, stored-hourly validation and publication remain to be
implemented/tested based on those findings. Monthly PRISM is never presented as
daily PRISM.

The validated multi-worker publication benchmark has not yet been integrated
into the hourly publisher. Hard-link publication remains experimental. No
snapshot/candidate cleanup is authorized by a mere filename match; prepare a
separate accepted-replacement deletion manifest before reclaiming that storage.

### Bounded 1979–1980 basin-impact investigation

All 24 initial monthly audit/proposal tasks passed execution. Existing totals
had 2–1,300 PRISM cells/month outside the cell tolerance (at most 0.27%), with
some large local differences but shared-area volume bias no larger than 0.0131%.
All proposed spatial fits met tolerance without conservative fallback. These
facts do not establish that individual basin losses are negligible.

The user prioritizes basin precipitation preservation over cell-perfect fits.
Before deciding on monthly repairs, array **5107899** repeats the bounded monthly
read-only audit while retaining PRISM-grid spatial diagnostics. Dependent job
**5107900** computes fractional basin-area monthly and annual comparisons using:

- RFC regions available across CONUS (a broad screen, not individual watersheds);
- the existing CNRFC forecast basins;
- the four subdomain reporting regions, UCRB HUC4 context and selected Yampa HUC8s.

Report net bias and gross precipitation deficit/surplus separately, in both mm
and percent. Annual deficit after summing months may mask within-cell temporal
cancellation; monthly deficits are retained. Dry-month ratios and small or
partly supported basins need interpretation. The comparison uses common PRISM
support across the 24 months, with covered-area fractions explicitly reported;
it is not exact integration of NWM pixels along basin edges. Missing PRISM areas
and unsupported cells cannot be certified by this screen.

The ten largest cell discrepancies in each month are saved as GeoJSON with RFC
location and fractional NWM active-cell link support. Activity is not proof of
hydrologic connectivity to US territory. Review locations before deciding whether
isolated exceptions matter. No automatic rejection threshold or production
replacement is introduced by this diagnostic.

Reports: `forcing/work/monthly-rain-basin-audit/5107900/acceptance.json` and
`largest_discrepancies.geojson`. Spatial month diagnostics:
`forcing/work/monthly-rain-repair/5107899/YYYYMM/spatial_diagnostics.nc`.
The additional archive read is necessary because the original audit retained
only summary metrics, not stored-minus-target fields. This is one bounded pass,
not an open-ended cell-level investigation.

## Decision: retain 1979–1980; extend daily-tier repairs

The basin screen completed. Regional annual net losses were small (largest of
the four reporting regions about 0.0534%). Among 8,976 CNRFC basin-months, 14
exceeded both 1 mm and 1% net deficit; SKRC1 in June 1980 lost 12.73 mm (1.84%
of its annual precipitation). These are localized documented limitations, not
evidence of a widespread basin precipitation deficit. **The user explicitly
decided to leave both years untouched.** Monthly proposals are diagnostics only;
do not publish them or start a 1979–1980 repair campaign without new authorization.

### Wave 3: February 1986–December 1990

The next daily-tier wave comprises 59 months. January 1986 is already repaired.
Jobs submitted:

| Job | Purpose | Dependency |
|---|---|---|
| 5107951 | Freeze originals, including preserved January 31 boundary | Completed |
| 5107952 | 59 monthly repairs, six concurrent 64-CPU tasks | Snapshot success |
| 5107953 | Internal boundaries plus preceding-wave boundary audit | All repairs successful |
| 5107954 | February 1–4 live publication with four day workers | Wave audit success |
| 5107955 | 59 monthly publication/subset tasks, eight concurrent 16-CPU jobs | Live four-worker check success |

The active summary campaign allocates up to 80 CPUs. Six repair tasks allocate
384 CPUs, giving a combined maximum of 464. Publication runs only after the
repair array: eight jobs × four workers means 32 concurrently processed days,
within 128 allocated CPUs. Each day still completes CONUS publication followed
by CNRFC → B120 and UCRB → Yampa, with existing audits and atomic replacements.
Hard-link publication remains disabled; this change parallelizes the verified
copy-based publisher rather than relaxing its checks.

`--previous-wave` and `--previous-array-job` capture the old January 31 source
from wave 2's original snapshot. Taking that boundary from the corrected live
archive could change rainfall timing in the shared February 1 PRISM window.
Wave 3 acceptance requires exact rainfall/timing agreement with that preceding
candidate window, in addition to all 58 internal month boundaries.

The publisher now accepts `--wave-dir`, `--array-job` and `--workers 1|2|4`.
Custom waves must have complete matching array/wave acceptance and preceding
boundary acceptance when applicable. Parent-held shared campaign locks plus
exclusive month locks remain held while child workers publish distinct days.
Both `.subset.lock` and `.subset-pilot.lock` are observed for all four domains.
Per-day transaction journals and original manifests support recovery if one
worker fails after other days have already published.

Reports use `forcing/work/prism-repair-wave3-198602-199012/` and
`forcing/work/prism-rain-pipeline/5107952_<task>/`. Summaries remain pending until
the corresponding corrected hours settle. December 1990's terminal reduction
and a simulation ending January 1, 1991 also need that next boundary hour
corrected by the following wave; a frozen input is not a repaired publication.

All four repaired-forcing cold/warm checks (5106713–5106716) passed, including
restart equivalence. This launch extends forcing only; it does not submit model
spin-up/production runs or delete old model outputs, originals, or candidates.

## November 5: remaining endpoints and cross-resolution closure audit

The bulk campaigns have published hourly repairs through April 17, 2026 (retro)
and March 3–October 28, 2026 (NRT), including the four domain subsets. Completion
of those campaigns is not blanket acceptance of every derived file.

The NRT summary finalizer previously searched both resolutions with the daily
`YYYY/MM/` glob. Monthly products use `YYYY/`, so its successful report covered
daily subsets only. It now uses the shared resolution-aware discovery function,
records separate daily/monthly counts, observes NRT/subset locks and saves an
immutable job-specific report. April–September monthly subsets must be refreshed
parent-first (CONUS → CNRFC → B120; CONUS → UCRB → Yampa).

The following closure jobs were submitted; submission is not acceptance:

| Job | Scope | Gate |
|---|---|---|
| 5181284 | Publish accepted March 2 NRT boundary and hourly subsets | Exact shared-window agreement with March 3 campaign |
| 5181285 | Reassemble April 18 retro from accepted windows with static envelope, publish/subset | Exact retained-window agreement with April 17 campaign |
| 5181305 | Refresh NRT daily/monthly summaries and all four subsets | Boundary and retro summary jobs succeed |
| 5181295 | Refresh April retro daily summaries through April 17 and subsets | 5181285 and 5181296 succeed |
| 5181296 | Recheck/refresh December 1980 summaries and subsets against repaired January 1 | Existing accepted hourly provenance |
| 5181297 | Annual 1979–2026 coverage/publication/summary-lineage audit, four concurrent tasks | All three summary jobs succeed |

The initial NRT summary submission 5181294 stopped before work because an active
endpoint publisher held the subset lock. Retry 5181305 waits for the other
closure jobs, and summary lock acquisition now waits instead of treating a
legitimate concurrent holder as a repair failure. The audit dependency was
updated to the retry. March 2 publication 5181284 passed in 2m30s, including all
four subsets. Unit/regression validation: 696 passed, four skipped.

Endpoint publication retains originals and uses existing atomic transaction and
metadata gates. It does not refit accepted PRISM windows, change non-rain chunks,
modify monthly-constrained 1979–1980 hourly data, or alter running model jobs.
The audit checks current parent/output identities and CONUS summary input
inventories; scientific validation remains in the referenced publication audits,
not a new full-array rescan. Reports are in
`forcing/work/rain-repair-endpoints/` (annual reports under `audit/`) and
`forcing/work/nrt-latest-prism-campaign/summary-acceptance-<job>.json`.

Partial January 1, 1979 is excluded from full-day coverage. Incomplete April 2026
retro and March/October 2026 NRT months are explicitly deferred rather than
published as complete monthly summaries. NRT October 29–31 hourly files remain
unconstrained recent products, not failed PRISM repairs. Cron remains paused.

### Audit correction and restart

Array 5181297 was canceled at the user's request after its completed 1981–1994
tasks reported only a checker error: the checker compared the NetCDF
`temporal_resolution` attribute with directory labels `daily`/`monthly` instead
of the producer's ISO durations `P1D`/`P1M`. Reports from that array are retained;
its failed status alone is not evidence of faulty forcing values.

Replacement array **5182099** repeats 1979–2026 with four concurrent tasks and
records `audit_version=2-iso8601-summary-resolution`. Focused tests (10 passed)
and metadata-only checks of actual 1981 daily/monthly files passed before
submission. Tests also require wrong resolutions, missing source paths and
stale source identities to remain failures. The separate 1979–1980 historical
source-path and B120 subset-lineage discrepancies have **not** been waived or
repaired by this checker change. No forcing files or model runs were changed.

### November 6: remaining historical lineage repairs

Corrected audit 5182099 passed every year 1981–2025 and the retro portion of
2026. The remaining findings were old CONUS daily-summary input paths and stale
B120 subset identities in 1979–1980, plus stale B120/UCRB NRT hourly subsets for
October 31, 2026. All audited NRT daily/monthly summaries passed.

`bin/repair_forcing_lineage.py` repairs the bounded historical summary provenance
without recomputing fields. It permits only the known insertion of `hourly/`
under the CONUS retro root and requires identical source size/mtime plus accepted
hourly provenance. Every variable's compressed payload (or contiguous values)
must remain identical before atomic publication. Monthly input identities may
advance only through an exact old-identity match to a passed metadata-only daily
transaction. Originals, candidates, input pins and transaction receipts are
retained under `forcing/work/rain-repair-endpoints/lineage-repair/`.

Parent-first subset refresh uses the existing mask/content audit and source-race
checks. The NRT refresh checks all four domains so a changed UCRB parent cannot
leave Yampa stale. No CONUS hourly rainfall is altered by this work, and no
model reruns or cron activation are included.

| Job | Work | Dependency |
|---|---|---|
| 5188376 | October 31 NRT subset refresh | None |
| 5188377 | January 2, 1979 metadata/publication/subset canary | None |
| 5188378 | 1979 summary provenance and subsets | Canary success |
| 5188379 | 1980 summary provenance and subsets | Canary success |
| 5188380 | Repeat corrected 2026 lineage audit | 5188376 success |
| 5188381 | Repeat corrected 1979–1980 lineage audits | Both historical repairs succeed |

These are submission records, not acceptance. Twelve focused tests passed,
including metadata-only publication/retry, monthly provenance propagation,
unchanged rainfall and rejection of unexplained source changes.

Initial execution: NRT subset refresh 5188376 passed in 56 seconds; historical
canary 5188377 passed in one minute, including all four subsets. Jobs 5188378 and
5188379 then started, and the 2026 read-only re-audit started. Historical full-year
completion and the final audit verdicts remain pending.
