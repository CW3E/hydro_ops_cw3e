# Daily NRT model operations by domain

The daily forcing controller supports CNRFC, B120, UCRB and Yampa model replays.
**B120, UCRB and Yampa are enabled following accepted November 12 catch-ups
and explicit user approval.** CNRFC retains its separate catch-up activation gate.
Each model job depends on its domain's successful forcing synchronization, the
CONUS summary refresh, and the daily forcing controller. Models run independently
after their own dependencies pass; a model-submission error in one new domain
is reported without suppressing the other domain's submission.

The configurations are `config/<domain>_nrt_daily.json`. CNRFC retains 64 MPI
ranks; UCRB also uses 64, while B120 and Yampa use 32. Each routine job requests
120 GB scratch and a two-hour
limit on ordinary `shared-128` resources. `mpan` never requests NRTRES.
The existing daily cron entry drives these follow-ups; no additional crontab
entry or installation is needed. Six-hourly forcing updates do not launch models.

## Publication and activation

Before enabling a new domain, promote its accepted NRT catch-up results to
`nwm/outputs/<domain>/nrt/{hourly,daily}/YYYY/MM/` and paired checkpoints to
`nwm/restarts/<domain>/nrt/YYYY/MM/`. Run logs keep their campaign identities.
B120 promotion uses `bin/promote_yampa_nrt.py --domain b120 --execute`; its
audit-only invocation must pass first. Exact original metadata, file identities
and post-promotion audit results are retained in
`nwm/status/b120/nrt-promotion/`. No NetCDF data are rewritten by promotion.

The generalized runner remains `bin/run_cnrfc_daily_cycle.py` for compatibility:
use `--domain b120|ucrb|yampa`, with `--snapshot-forcing`.
Each new domain requires a completed promotion and its own accepted warm-start
gate. A two-day `--test` run resumes its second day from a daily checkpoint and
compares eight land/routing state variables with the continuous result.
It tests overlapping-hour publication in a private hardlinked mirror. Original
outputs remain unchanged. Only a complete test with `rst_typ=1`, matching domain
and mask, and passed daily-checkpoint continuation can activate that domain.
The configuration's `daily_acceptance` records that exact test report.

## Replay and restart retention

The runner selects the newest available forcing collection's midnight as the
endpoint, checks the required forcing through that hour, and rewinds 11 days.
It chooses a paired 00 UTC checkpoint at or before the rewind. A publication gap
extends the rewind as necessary. Initially only monthly checkpoints may exist,
so the first daily replay can cover most of a month; subsequent runs can use
daily checkpoints. The replay pins forcing file versions before simulation.

Fully coupled warm starts use `rst_typ=1`, `RSTRT_SWC=0`, `GW_RESTART=1`, no t0
output, daily-resolution LDASOUT, and hourly/daily CHRTOUT. Hourly CHRTOUT files
group calendar-day records; daily reductions use 01–00 UTC interval-end values.
Every replay day produces a paired 00 UTC restart. Retention is still plan-only:
monthly anchors and existing checkpoints are preserved until cleanup is approved.

Model locks, once-per-UTC-day submission journals, workspaces and publication
transactions are separate per domain. Publication preserves overwritten files
as recoverable backups and rolls back ordinary failures. An incomplete journal
blocks subsequent publication. Model jobs for the same domain defer new daily
submissions. The daily status is
`nwm/status/<domain>/nrt-daily/latest.json`; job-specific evidence is
`nwm/runs/<domain>/nrt/cycles/job-<ID>/cycle.json`.

## Initial acceptance runs

These October commissioning notes are historical; the corrected-history
recommissioning below supersedes their enabled status and selected test reports.

B120 **5069197** and Yampa **5069198** passed October 1–3, 2026 at 00 UTC using
32 ranks each. Both completed the daily-checkpoint continuation comparison,
published 14 files into private mirrors, and retained two paired daily
checkpoints with no forcing revisions after snapshot capture. Their exact
cycle reports are pinned in the enabled domain configurations. Routine hooks
are enabled for the next daily forcing cycle; no extra cron entry is required.
The first production replay will bootstrap from the existing monthly checkpoints.

B120 promotion preserved 195 days (April 18–October 30, 2026 at 00 UTC), 4,680
hourly channel records and all paired restart states. Its pre/post audits match.
The full regression suite passed with **586 passed, three skipped**.

## Corrected-history readiness — November 12, 2026

All three domains have accepted, transactionally published corrected NRT model
results through November 8 00 UTC (thermal refresh jobs 5237509 B120, 5237507
Yampa, 5237511 UCRB). Hourly forcing filenames extend through November 12;
model preflight validates actual hours, variables, subset receipts and active-cell
coverage before integration. No extra forcing backfill was indicated by the
calendar-file inventory. All retain paired monthly checkpoints plus November 8.

Comparison with the accepted model's forcing lineage found changed domain-file
identities on September 30, October 1–10 and November 2–8. Identity differences
are not proof that every numerical field changed, but conservative replay avoids
assuming equivalence. September 1 is the latest available paired checkpoint
before September 30; therefore the first catch-up spans 72 days, not 11.

| Domain | MPI ranks | Isolated September 1–3 test | Conditional September 1–November 12 catch-up |
| --- | ---: | --- | --- |
| B120 | 32 | 5271587 | 5271590 |
| Yampa | 32 | 5271588 | 5271591 |
| UCRB | 64 | 5271589 | 5271592 |

All three isolated test reports now say `complete`, with
`daily_checkpoint_continuation=passed`, `rst_typ=1`, 14 files published into their
private mirrors, and no source revisions after snapshot. B120 took 1m09s and
Yampa 30s. These results permit the conditional catch-ups, not routine activation.
The full regression suite passed 989 tests (four skipped); the subsequently added
UCRB dependency/failure-isolation test and focused scheduling tests also passed.

Tests verify uninterrupted versus daily-restarted states and overlap publication
in a private mirror. Catch-ups require both `afterok` and the exact fresh test's
acceptance report. They request four hours for initial bootstrap, without changing
the routine two-hour limit. Each pins forcing, publishes daily checkpoint pairs,
daily LDASOUT and hourly/daily CHRTOUT, retaining recovery backups. No deletion is
authorized. Separate domains may run concurrently (128 ranks total).

UCRB's initial canonical archive came from accepted thermal-refresh publication,
not the older directory-promotion tool. Its new `canonical_acceptance` configuration
checks that completion, its complete publication transaction, successful restart
test, matching campaign-plan checksum and current domain-mask metadata. The daily
runner uses that existing verified archive gate; it does not fabricate a promotion
record or rerun the historical thermal refresh. Approved parameters/mask and all
normal forcing/output checks remain mandatory.

All three configurations now have `enabled=true`, following explicit user
approval and verification of their fresh tests, complete catch-up transactions,
all 434 published artifact identities per domain and unchanged forcing snapshots.
Each published 72 daily checkpoint pairs through November 12 00 UTC. Catch-ups
took 18m56s for B120, 7m02s for Yampa and 34m03s for UCRB. Exact test and catch-up
report gates remain enforced. Their existing daily dependency branches are active:
B120 follows CNRFC-to-B120 forcing synchronization, UCRB follows CONUS-to-UCRB,
and Yampa follows UCRB-to-Yampa. A model failure does not block other domains.
No extra cron rows or changes to the CNRFC schedule are needed. The normal 11-day
rewind then uses newly established daily checkpoints; late revisions outside that
window still require explicit reconciliation.

The installed login1 daily forcing command already permits model hooks, so this
activation required no crontab reinstall and submitted no immediate extra model
jobs. The next daily cycle at 02:30 UTC will schedule each model after its own
successful forcing dependencies. Six-hourly remains forcing-only and monthly
Retro remains paused. At full concurrency the four domains request 192 MPI ranks
(64 CNRFC + 64 UCRB + 32 B120 + 32 Yampa), using ordinary resources without NRTRES.

## B120 retro archive promotion

Job **5070067** audits and promotes the completed `production_v1` retro archive
from January 2, 1979 through April 18, 2026 at 00 UTC. The target paths are
`nwm/outputs/b120/retro/{hourly,daily}/YYYY/MM/` and
`nwm/restarts/b120/retro/YYYY/MM/`. Runs remain under
`nwm/runs/b120/retro/production_v1/`.

The entry point is `slurm/promote_b120_retro.sh`, using four CPUs for metadata
and archive-receipt checks. Every annual acceptance, monthly interval, expected
output date, hourly archive identity/record count and endpoint restart pair is
checked before same-filesystem directory renames. The promotion holds both the
retro campaign and NRT model locks. It preserves metadata originals, rewrites
current run references (including NRT's inherited retro checkpoints), and
rechecks publication after the move. No model rerun or NetCDF rewriting is
needed. No restart files are removed.

`nwm/status/b120/retro-promotion/promotion.json` is the authoritative completion
journal; `plan.json` records exact file identities and moves. The model runner
uses canonical retro paths after completion and refuses an incomplete promotion.
The submission above alone does not establish completed publication.

Yampa uses the same audit and promotion procedure via
`slurm/promote_yampa_retro.sh`, submitted as **5070071**, for January 2, 1979
through April 18, 2026 at 00 UTC. Its targets are
`nwm/outputs/yampa/retro/{hourly,daily}/YYYY/MM/` and
`nwm/restarts/yampa/retro/YYYY/MM/`; logs retain `production_v1` under runs.
The completion journal and original metadata are retained under
`nwm/status/yampa/retro-promotion/`. Yampa and B120 use separate locks and
directories, so their promotions can run concurrently. No model or forcing
data are regenerated, and all restart states are preserved.
