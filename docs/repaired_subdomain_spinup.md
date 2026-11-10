# Corrected-forcing subdomain spin-up

## Thermal forcing refresh and NRT continuation (November 2026)

The user authorized independent Yampa/B120/UCRB chains after forcing publication
5232861 passed. `bin/refresh_thermal_model_2026.py --stage submit` freezes one
campaign under `nwm/status/thermal_refresh_2026_v1/`. Each chain refreshes Retro
from **2026-02-01 00 UTC through 2026-04-18 00 UTC** (76 days), followed by NRT
from that new terminal pair through **2026-11-08 00 UTC** (204 days). The partial
November 8 forcing collection includes the required midnight; later hours are
outside this authorization. NRT has both an `afterok` dependency on its own
Retro job and an application-level check of its domain/plan/endpoint acceptance.

Yampa/B120 use 32 MPI ranks each; UCRB uses 64. Each job requests 120 GB scratch,
six hours, ordinary shared-128 resources and no NRTRES. Existing CNRFC jobs and
cron are unchanged. The existing shared runner gains a narrowly bounded refresh
campaign only; previous campaign endpoint and acceptance guards remain intact.

Before either long run, the driver verifies the forcing publication identity,
domain/startup evidence, parent/subset lineage and paired initial checkpoints.
It preserves initialization and pins forcing inodes in an isolated workspace,
then performs a two-day continuous versus warm-restart comparison. The shared
runner uses `rst_typ=1`, 600/600 routing steps, daily LDASOUT, hourly/daily CHRTOUT,
calendar-day hourly stacking, and monthly plus terminal restart checkpoints.
Neither existing accepted run markers nor stale old NRT records skip new work.

Outputs remain staged until the complete interval and source-identity checks
pass. Existing recoverable publication merges hourly boundary records, backs up
old destinations and replaces only validated products. Interior old checkpoints
not regenerated (notably April 17) are moved to an inventoried recovery location,
not left discoverable as current state; February 1 remains untouched. The driver
records new acceptance under `<domain>/<stream>/acceptance.json` and updates
NRT daily-status endpoints after publication. Old model receipts are historical
evidence, not proof of current canonical values. No old backups are deleted and
no daily cron hooks, including UCRB operations, are enabled by this campaign.

The full regression suite passed 898 tests with four skips before the final
checkpoint-retirement test was added; all nine targeted refresh tests passed.
Model tests execute inside the submitted jobs before production publication.

Submission ledger: `nwm/status/thermal_refresh_2026_v1/submission.json`.
Yampa Retro/NRT: **5237506 → 5237507**; B120: **5237508 → 5237509**;
UCRB: **5237510 → 5237511**. These are submissions, not completed simulations
or publication acceptance.

## Remaining B120/UCRB intervals (November 8, 2026)

Receipt review found continuous accepted segments from January 2, 1979 through
December 31, 2020 00 UTC for B120 (504 segments), January 1, 2026 for UCRB
(564), and April 17, 2026 for Yampa (569). Counts matched daily LDASOUT,
hourly/daily CHRTOUT expectations and terminal restart pairs existed. This was
an acceptance-record review, not a fresh full-payload or scientific audit.
These corrected runs already publish to canonical archives: no separate
promotion is needed. Old production model data were removed by 5150917;
preserve new outputs, checkpoints and historical evidence.

UCRB final job 5179521 stopped before simulation because the March 12, 2026
CONUS forcing identity changed after its original preflight. The current file
passes the accepted coupled-replay publication check and its UCRB subset matches.
Do not simply bypass or overwrite the old preflight: retain it as evidence.

`bin/submit_remaining_retro.py --domain ucrb` submitted fresh 2026-only
preflight **5217097**, then **5217098** (64 MPI ranks) to continue from the
accepted January 1 pair through April 17, 2026 00 UTC. Separate evidence lives
under `nwm/status/repaired_rain_retro_v1/ucrb-extension-20260101-20260417/`.

`--domain b120` submitted preflight **5217099**, one-day bridge **5217100**
(December 31, 2020 to January 1, 2021), then yearly jobs **5217101–5217106**
through April 17, 2026. B120 uses 32 MPI ranks, independent of UCRB/CNRFC.
Evidence lives under the corresponding `b120-extension-20201231-20260417/`
directory. Each submission ledger is exclusive-create to prevent blind repeats.

The existing `continue_yampa_retro.py` now accepts `--domain b120|ucrb|yampa`
(default Yampa unchanged). UCRB is bounded to 2026; B120/Yampa preserve the
partial-2020 bridge semantics. Preflight checks every required forcing day and
subset provenance, including terminal midnight. Models require successful
preflight, unchanged pinned forcing, preceding interval acceptance and paired
restarts. Each model job requests 120 GB scratch and six hours, without NRTRES;
daily land and hourly/daily channel output, option-1 warm restart and monthly
checkpoints remain unchanged. CNRFC's active chain is untouched. Submission is
not completion; inspect job-specific logs and interval acceptance before use.

B120 preflight 5217099 and the bridge 5217100 passed, but 2021 job 5217101
stopped before simulation: the shared corrected-retro guard still limited the
post-2020 small-domain continuation to Yampa. The guard now includes B120 with
the same April 17, 2026 upper bound and explicit initialization/startup gates.
Regression tests cover both domains for 2021–2026 and reject a later endpoint
or missing initialization. Retry **5217121** replaces only the failed 2021 job;
existing 2022 job **5217102** now depends on it, preserving jobs 5217103–5217106.
SLURM rejected a dependency on the already completed preflight/bridge jobs, so
their successful accounting and acceptance records were checked before retry
submission; runtime still rechecks preflight, forcing pins and bridge acceptance.
The ledger is `b120-extension-20201231-20260417/retry-2021-guard.json` beneath
the campaign status directory. No bridge rerun or output deletion was needed.

## Yampa continuation after its partial 2020 run

Yampa accepted the 2001–2020 extension, ending at **2020-12-31 00 UTC**.
`bin/submit_yampa_retro_continuation.py` schedules a dedicated provenance
preflight, a one-day **December 31, 2020 → January 1, 2021** bridge, then yearly
jobs 2021–2026 ending **April 17, 2026 00 UTC**. Each model job uses 32 MPI
ranks, 120 GB requested scratch, and a six-hour limit (recent years took about
22 minutes). Other domains and their active chains are unchanged.

The bridge explicitly uses the December 31 paired checkpoint, preserves all
previous output, and stores interval acceptance as
`20201231-20210101-passed.json`, not `2020-passed.json`. The 2021 job requires
that bridge marker; subsequent years require their preceding annual acceptance.
Existing hourly calendar-file merging handles the shared midnight file without
repeating the initial output step. Daily LDASOUT, hourly/daily CHRTOUT, option-1
warm restarts and monthly checkpoints remain enabled; the final partial April
run saves an additional terminal restart pair.

Preflight covers every required date including terminal midnight and verifies
the complete CONUS→UCRB→Yampa corrected-forcing provenance chain. Evidence and
submission ledger: `nwm/status/repaired_rain_retro_v1/yampa-extension-20201231-20260417/`.

Submitted: preflight **5181249**, December 31 bridge **5181250**, and yearly
2021–2026 jobs **5181251–5181256**. Every stage depends on successful completion
of its predecessor and checks the corresponding acceptance evidence at runtime.
Regression suite: **693 passed, four skipped**; focused interval tests and
Ruff/shell/diff checks passed. Submission is not evidence of model completion.

## UCRB extension through April 2026

UCRB accepted all 120 monthly segments for 1991–2000 and retains the matching
January 1, 2001 land/hydro restart pair. Its newly authorized continuation uses
`bin/submit_repaired_retro_extension.py --ucrb`: a dedicated forcing-provenance
preflight followed by 26 sequential yearly jobs, 2001–2026, at **64 MPI ranks**.
The terminal time is **2026-04-17 00 UTC**, completing April 16. April 18 forcing
has not been accepted by the rain-repair publication campaign and is not used.

The preflight verifies every required corrected CONUS publication and direct
CONUS→UCRB subset receipt, including the ending midnight, across repair waves
5–7. Every annual job rechecks pinned file identities and requires the preceding
year's accepted paired restart. Existing monthly output/state checks, locks,
daily LDASOUT, hourly/daily CHRTOUT, monthly restarts, and 120 GB scratch requests
are preserved. The final partial April segment and partial-year acceptance use
date-range labels, with an additional restart pair at April 17 00 UTC.

Evidence/ledger: `nwm/status/repaired_rain_retro_v1/ucrb-extension-2001-20260417/`.
Other domains' existing chains and authorization endpoints are unchanged.

Submitted November 5: preflight **5179495**, followed by UCRB annual jobs
**5179496–5179521**. The first year depends on the preflight; later years depend
on their predecessor. These are submitted jobs, not accepted model results.
Regression checks: **692 passed, four skipped**, plus Ruff/shell/diff checks.

## B120/Yampa corrected Retro extension (November 2026)

Both small-domain chains accepted all monthly segments through **2001-01-01
00 UTC**. The next authorized extension is **2001-01-01 through 2020-12-31
00 UTC**, using the same `repaired_rain_retro_v1` campaign, canonical archives,
and matching option-1 land/hydro checkpoints. CNRFC/UCRB chains are unchanged.

`bin/submit_repaired_retro_extension.py` submits a metadata/provenance preflight,
then two independent chains of 20 annual jobs. Each model job uses 32 MPI ranks
and 120 GB requested scratch. Preflight verifies accepted wave-5/wave-6 repairs,
current CONUS publication identities, and both links in the CONUS→CNRFC→B120
or CONUS→UCRB→Yampa subset chain for every required day, including terminal
midnight. Jobs verify pinned identities again before model execution, and retain
the existing per-month input/output checks and duplicate-writer lock.

Forcing is published through December 31, 2020, but January 1, 2021 is still
behind the blocked wave-7 publication gate. Thus the last job completes
**December 30**, not the entire 2020 calendar year. It saves an additional
checkpoint at December 31 00 UTC. Its final segment uses a date-range run
directory and its completion is `20200101-20201231-passed.json`, deliberately
not `2020-passed.json`. Later continuation must use that checkpoint and an
explicit interval once repaired 2021 forcing is published; it must not treat
this partial year as a full-year acceptance or silently rerun overlapping days.

Outputs remain daily LDASOUT and both hourly/daily CHRTOUT; restart checkpoints
remain monthly plus the partial-year terminal checkpoint. No old-data cleanup,
model physics change, executable rebuild, or cron installation is part of this
extension. Ledger and preflight evidence are under
`nwm/status/repaired_rain_retro_v1/extension-2001-2020/`.

Submitted November 5: preflight **5177570**, B120 **5177571–5177590**,
and Yampa **5177591–5177610**. Each first year depends on successful preflight;
subsequent years depend on the preceding year and validate its acceptance and
restart pair at runtime. These are submissions, not completed simulations.
Regression verification: 686 tests passed, four skipped; changed Python files
passed Ruff and the shell entry point passed syntax checking.

The approved new campaign is `repaired_rain_spinup_v1`, covering **1981-01-01
00 UTC through 1986-01-01 00 UTC** for CNRFC, UCRB, B120 and Yampa. It starts
from cold conditions, not old precipitation-driven spin-up states. This campaign
does not launch retrospective or NRT production automatically.

## Gates and execution

Corrected forcing was published and subset for the full interval, including the
ending midnight record. Cold-start/option-1 continuation equivalence tests passed:
CNRFC 5106713, B120 5106714, UCRB 5106715 and Yampa 5106716. These are engineering
checks, not proof of hydrologic equilibrium or independent model validation.

`bin/submit_repaired_domain_spinup.py` previews the plan; `--submit` creates four
independent five-job chains. Each job covers one calendar year, with `afterok`
dependencies within a domain. CNRFC/UCRB request 64 MPI ranks each; B120/Yampa
request 32 each (192 total if all four run). Each requests 120 GB node scratch
and a conservative 48-hour limit. The user explicitly approved exceeding the
earlier combined 512-CPU budget; forcing repair allocations remain unchanged.
No NRTRES reservation is requested.

Submitted chains (1981 through 1985 in order):

| Domain | MPI ranks | Annual job IDs |
|---|---:|---|
| CNRFC | 64 | 5109654, 5109655, 5109656, 5109657, 5109658 |
| UCRB | 64 | 5109659, 5109660, 5109661, 5109662, 5109663 |
| B120 | 32 | 5109664, 5109665, 5109666, 5109667, 5109668 |
| Yampa | 32 | 5109669, 5109670, 5109671, 5109672, 5109673 |

Pre-submission checks: 651 tests passed, four skipped; targeted Ruff and shell
syntax checks passed. Cold and warm namelists were generated against the actual
template successfully. Submission does not establish completion or checkpoint
acceptance; inspect each monthly and annual acceptance report.

`slurm/run_repaired_domain_spinup.sh` loads the validated MPI/compiler stack;
`bin/run_repaired_domain_spinup.py` executes successive calendar-month segments.
History output is disabled (both hourly/daily LDASOUT and CHRTOUT, plus ancillary
history). Each segment writes only its terminal land/hydro restart pair, at
00 UTC on the first of the following month. Cold-start settings match the passed
test; subsequent segments use `rst_typ=1`, `RSTRT_SWC=0`, `GW_RESTART=1`.
Terrain/channel timesteps remain 600/600 seconds, lakes disabled,
`PCP_PARTITION_OPTION=1`, and `t0OutputFlag=0`.

## Isolation, publication and recovery

- Runs, logs and monthly acceptance markers:
  `nwm/runs/<domain>/retro/repaired_rain_spinup_v1/YYYYMM/`.
- Monthly checkpoint pairs:
  `nwm/restarts/<domain>/retro/repaired_rain_spinup_v1/YYYY/MM/`.
- Submission ledger: `nwm/status/repaired_rain_spinup_v1/submission.json`.
- SLURM logs: `nwm/logs/repaired-domain-spinup-JOBID.out`.

The domain campaign lock excludes duplicate writers. Each month pins hourly
forcing using same-filesystem hardlinks after checking current subset receipts;
timestamps/required variables are checked before execution. Atomic source-file
replacement cannot change the pinned files. Model parameters must match their
accepted checksums. Resume requires matching executable, runner, template,
parameter-manifest and test-gate signatures plus unchanged checkpoint checksums.

Publication requires the completion sentinel, no history files, correctly dated
restart pairs, finite routing state, finite/unmasked active land state, and
unchanged pinned inputs. Each restart file is atomically published; the monthly
`accepted.json` marker gates use of the pair. There is no whole-pair atomic rename.
An interrupted publication cannot silently authorize continuation. A conflicting
unaccepted checkpoint blocks the retry for inspection.

Successful months can be skipped on resubmission after rechecking acceptance and
restart checksums. Automatic failure retries are not submitted; an annual failure
blocks later years. Accepted checkpoints survive scratch cleanup. Failed attempts
retain logs and input snapshots; scheduler scratch itself may be purged.
The submission command refuses an existing ledger to prevent duplicate chains.

Old model outputs/restarts are not deleted or promoted by this campaign. Review
the completed five-year state before using it to initialize corrected-forcing
retro/NRT reruns. Scientific spin-up convergence remains a separate assessment.

## Corrected-state retrospective startup tests

All four five-year chains completed with 60 passed monthly acceptance records
per domain. The next gate is `bin/test_repaired_retro_start.py`, submitted through
`slurm/test_repaired_retro_start.sh`: CNRFC **5150178** (64 ranks), UCRB
**5150179** (64), B120 **5150180** (32), and Yampa **5150181** (32).
Each requests 120 GB scratch and two hours, without a reservation.

The test verifies final spin-up checkpoint checksums, screens active land state
against broad physical bounds, and inventories finite routing state. It copies
the new domain-specific 1986-01-01 checkpoint pair into an isolated initialization
at 1979-01-02. Physical fields and the hydro history counter are preserved;
accumulated land diagnostics are reset using the existing initialization policy.
The production initialization helper now accepts an explicit donor directory;
its default CONUS donor remains unchanged for existing callers.

Tests run January 2–4 continuously and January 3–4 from the intermediate warm
restart. They validate daily LDASOUT, hourly and daily CHRTOUT, archive records,
active-cell coverage, and final-state continuation equivalence. The small test
uses daily checkpoints to exercise continuation, not the intended monthly
production checkpoint cadence. It is an engineering gate, not a convergence test.

Reports and initialization copies are isolated under
`nwm/outputs/<domain>/tests/repaired-retro-start-<job>/`; the job-specific final
report is `acceptance.json`. Submission bookkeeping is
`nwm/status/repaired_rain_spinup_v1/retro-start-tests.json`.
No old outputs/restarts are deleted and no long production chains are launched
by these tests. Review results before preparing cleanup and production reruns.

## Approved old-archive replacement and 1979–2000 reruns

All four startup tests passed. The user authorized deleting obsolete results
for these four domains and starting the long reruns. Cleanup job **5150917**
first writes `nwm/status/repaired_rain_retro_v1/deletion-plan.jsonl`, then removes
only inventoried model-data files under the old Retro/NRT archives. Its
`deleted.jsonl` journal and `cleanup.json` report record execution. The new
`repaired_rain_spinup_v1` checkpoints, isolated tests, metadata/logs and CONUS
archives are excluded. Deleted model data are not moved to trash; recovering
them requires rerunning the model or an independently retained backup.
Historical acceptance/promotion records are provenance, not evidence that
deleted outputs remain available.

`bin/submit_repaired_retro.py` submitted 88 annual jobs via
`slurm/run_repaired_retro.sh`, four independent 22-year chains:

| Domain | MPI ranks | First job (1979) | Last job (2000) |
|---|---:|---:|---:|
| CNRFC | 64 | 5150918 | 5150939 |
| UCRB | 64 | 5150940 | 5150962 |
| B120 | 32 | 5150964 | 5150987 |
| Yampa | 32 | 5150988 | 5151009 |

Exact IDs and dependencies are in
`nwm/status/repaired_rain_retro_v1/submission.json` (IDs need not be contiguous).
Every chain waits for successful cleanup, then for its preceding annual job.
Year 2000 additionally waits for forcing publication **5133400**, protecting the
2001-01-01 terminal forcing boundary. Jobs request 120 GB scratch and 48 hours,
without NRTRES. Maximum model concurrency is 192 MPI ranks.

`bin/run_repaired_retro.py` verifies the cleanup and new startup gate, uses the
tested 1979-01-02 initialization, and requires preceding-year acceptance for
later years. It reuses the established monthly production/validation/publishing
runner, with `rst_typ=1`, daily LDASOUT, hourly and daily CHRTOUT, explicit
calendar-day hourly record stacking, and monthly restart pairs. No Jan 1, 1979
simulation is attempted. New outputs/restarts use the canonical
`nwm/{outputs,restarts}/<domain>/retro/` roots; fresh run/acceptance records use
`nwm/runs/<domain>/retro/repaired_rain_retro_v1/`. Old run markers cannot skip new
months. The existing production campaigns retain their original routing rules.

Validation before submission: full existing suite 670 passed, four skipped;
new cleanup-scope and campaign-gate checks plus production tests: 10 passed.
Ruff, shell syntax and diff checks passed. Submission is not completion or
scientific validation; inspect monthly acceptance and job-specific reports.

## CNRFC continuation through April 2026

On November 7, 2026, all 22 CNRFC annual completion records (1979–2000)
and all 264 monthly acceptance records passed. The matching land/hydro restart
pair at 2001-01-01 00 UTC passed `restart_check`. The final 2000 job, 5150939,
completed in 3h34m14s on 64 ranks. December recorded 744 hourly CHRTOUT records,
31 daily LDASOUT files, and 31 daily CHRTOUT files. This is operational/archive
acceptance, not independent hydrologic validation.

`bin/submit_repaired_retro_extension.py --cnrfc --after 5202241` submitted the
continuation from 2001-01-01 through **2026-04-17 00 UTC**, the accepted forcing
endpoint, not the end of calendar year 2026. Preflight **5202816** waits for the
bounded Retro forcing publication canary 5202241 before auditing and pinning
every daily forcing file and its CONUS-to-CNRFC subset lineage, including the
terminal forcing day. The extension audit recognizes completed coupled-replay
publication receipts as well as the earlier rain-only campaign receipts; stale
identities or incomplete downstream publication remain failures.

The 26 yearly model jobs start with **5202817** (2001) and end with **5202844**
(2026); each waits for successful completion of the preceding job. Exact IDs,
commands and dates are in
`nwm/status/repaired_rain_retro_v1/cnrfc-extension-2001-20260417/submission.json`.
Each requests 64 MPI ranks, 120 GB scratch, and 48 hours without NRTRES.
Existing daily LDASOUT, hourly/daily CHRTOUT, monthly restart checkpoints, and
`rst_typ=1` are unchanged. Eight focused tests passed, with Ruff and diff checks.
No shared executable was rebuilt. Submission does not imply forcing preflight
or simulation completion.

For other already-pinned model chains, replacing a forcing file intentionally
invalidates its old identity: the UCRB 2026 preflight evidence predates the
March 2026 publication canary and must be rechecked before that year executes.
Do not bypass this guard or silently edit pinned identities.

Preflight **5202816 passed** in 7m31s across all 26 years. Initial 2001 job
5202817 failed before model execution: `run_cnrfc_production.py` still omitted
CNRFC from the corrected post-2000 extension authorization guard. The guard now
permits CNRFC under the same explicit initialization/test-gate/end-date limits
as UCRB; model physics, executable and restart files are unchanged. Replacement
**5203602** retries 2001, and 2002 job **5202818** now depends on its success.
The remaining chain and April 17, 2026 endpoint were retained: shortening the
period would not resolve this initialization guard error. Retry provenance is
in `cnrfc-extension-2001-20260417/retry-5203602.json` under the campaign status
directory. Nine focused model-extension tests passed after updating the earlier
test that intentionally rejected all CNRFC post-2000 runs.
