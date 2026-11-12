# Two-stage PRISM revision policy

## Operational intent

NRT is a timely, limited-revision operational product. Retro is the separate
mature climatological product. Acquiring a newer PRISM source no longer means
every existing forcing file must immediately be recomputed.

| Cadence | Product | PRISM selection |
| --- | --- | --- |
| Six-hourly / newest daily hours | NRT extension | Never wait for PRISM; retain required HRRR/GFS fallback |
| Daily | Recent NRT constraints | Actual daily PRISM **grid count 2**, once all ppt/tmin/tmax inputs exist |
| Monthly | Retro production | Actual daily PRISM **grid count ≥8**, in addition to existing age/coverage gates |

The second daily PRISM release normally arrives about five days after its
12 UTC endpoint. Two adjacent PRISM windows are needed for one 00–23 UTC
forcing collection, so a calendar day generally becomes eligible about 5–7 days
later. Dates are not admitted merely because their age exceeds five days.
The final daily PRISM release normally follows approximately six monthly updates.
See [PRISM's official schedule](https://prism.oregonstate.edu/calendar/).
Our monthly forcing summaries remain reductions of hourly forcing; they do not
switch to PRISM's monthly grids.

## Selection, freezing and exceptions

- Skip grid-count-1 constraints for new NRT production. Keep otherwise valid
  unconstrained forcing available while waiting for second-release inputs.
- Select all three daily variables together. Copy the selected release into
  `forcing/inputs/oregon_state/prism/selected/nrt/daily/`, preserving native file
  contents and release metadata. `selections/YYYYMMDD.json` records original and
  selected identities and source checksums. No hard links to the mutable download
  archive are used. Selection happens under the revision-queue lock.
- Later source downloads cannot advance an existing selection. Recent source
  repairs reuse the same selected release, including its frozen PRISM input root.
- Routine NRT revision admission is limited to the last **11 days**. Older
  changed/untracked-release obligations are explicitly recorded as
  `not_applied_policy` under `policy_dispositions`, not marked scientifically
  applied. Existing forcing files are not deleted, downgraded or replaced merely
  to impose the new policy retrospectively.
- If release 2 was missed and only release 3+ is available, record
  `second_release_missed`. Do not silently use a later release. Such outages and
  major source errors require an explicit exception/recovery decision.
- Pre-policy constrained recent files without eligible pins are retained under
  `retained_pre_policy_constraint` when their accepted output identity still
  matches. This is a transition safeguard, not proof they used release 2; it
  also means other revisions for those retained days await an explicit recovery
  decision. Routine new dates use the new selection normally.
- Intermediate releases 3–7 do not create routine NRT replay work. Stable
  releases are eligible for the separate retro stream. Major stable-source
  revisions still require source-aware reconciliation.

## Scheduling and downstream work

Cadences stay at daily 02:30 UTC, six-hourly 08:30/14:30/20:30 UTC, and the saved
monthly-retro schedule on the 18th at 18 UTC. This policy does not install cron,
unpause the monthly cron entry, or enable model hooks.

With the policy active, daily forcing revision scope contracts from approximately
200 days to dates `today−11` through `today−2`. The recent-day processor handles
this entire range; the old broad PRISM calendar production path is not used for
NRT. External acquisition remains separate: PRISM metadata/download refresh can
still inspect approximately 215 days for retro needs, downloading only changed
releases. This does not trigger intermediate NRT reconstruction.

After normal source-aware publication and summaries, the scanner selects eligible
release-2 inputs and creates exact-source obligations. The existing bounded replay
consumer applies at most two eligible dates per cycle, normally about one newly
eligible date per day plus catch-up. It uses open-month deferral: hourly forcing,
affected daily summaries and all four subsets must pass before acknowledgement;
an unfinished month's summary is deferred, not fabricated. Closed-month summary
checks remain unchanged. Batching their rewrites once per whole month is a future
optimization, **not implemented by this policy change**.

Monthly retro retains its separate scheduling and baseline cleanup acceptance
gates. Grid count ≥8 is checked both in eligibility planning and the normal
production worker, so age alone cannot label a provisional source stable.

Routine daily NRT and monthly retro revision submissions now pass
`--reuse-baselines`. Reuse checks accepted receipts, exact native-input/assets
fingerprints and published file identities, and copies into the isolated replay
workspace. Changed/missing/unaccepted cache entries trigger normal rebuilding;
PRISM-only changes do not invalidate the native baseline. The search is limited
to earlier accepted workspaces in the same batch and the retained canonical
baseline archive, never unrelated experiments. Publication, repeat/no-op checks,
and exact-source queue acknowledgement remain mandatory. Manual callers still
opt in explicitly. This corrects the scheduler omission that caused job 5265466
to rebuild overlapping baselines unnecessarily.

## Rollout and evidence

`config/prism_revision_policy.toml` requests the policy but requires
`forcing/status/prism-maturity/activation.json` with a passed matching-policy
record before it takes effect. Until then, normal production uses the prior
policy. Missing/failed activation is not permission to switch inputs mid-job.

`slurm/test_prism_maturity_policy.sh` projects the real queue into an isolated
workspace, selects real second-release grids there, and runs an isolated hourly,
daily, four-domain candidate plus unchanged repeat. It does not modify the live
queue or forcing archive. The report pins the tested implementation hashes.

`slurm/activate_prism_maturity_policy.sh --test-report <acceptance.json>` requires
that successful test and unchanged implementation, waits via scheduler dependency
for the old catch-up batch to finish, rejects conflicting forcing submissions,
and takes the production/queue locks. It saves before/after queue snapshots,
creates production selections, records skipped obligations, and writes the
activation report last. It does not rewrite forcing or start NWM.

The old all-release backlog campaign
`forcing/work/revision-backlog-drain/nrt-20261111/` has a `PAUSED.json` boundary
marker: its in-flight batch may finish but it must not submit another batch.
Do not resume it under the new policy. Do not delete its accepted artifacts or
publication originals merely because some remaining revisions are now skipped.

Unit tests cover release 1 rejection, exact release 2 pinning, later-release
isolation, stable-release eligibility, source tampering, cutoff/disposition
semantics, preservation of acknowledged stable history, and activation gates.
Any failed live test or cutover requires review; no false queue acknowledgement
or silent fallback is authorized.

Rollout jobs submitted November 11, 2026:

- **5265040**: isolated real-data selection, queue projection, candidate and no-op
  test; 64 CPUs, 120 GB requested scratch, four-hour limit.
- **5265041**: conditional cutover after successful 5265040 **and** the last old
  batch 5264856; two CPUs, 30-minute limit, ordinary shared resources. Failed
  dependencies cancel cutover; conflicting forcing submissions also block it.

The initial cutover 5265041 safely refused activation because forcing submissions
were active. The later revision batch 5265466 published October 9–10 and its
downstream synchronization finished before the retry.

**Activated November 12, 2026 at 10:04:48 UTC:** retry **5270654** passed in 50
seconds using the unchanged implementation accepted by test 5265040. Effective
`policy(root)['enabled']` is now true. The live marker and job-specific receipt
are `forcing/status/prism-maturity/activation.json` and
`activation-5270654.json`. Before/after queue snapshots are retained alongside.
Pending counts changed from 31 NRT/200 retro to 6 NRT/192 retro; 39 old queue
events have explicit `not_applied_policy` dispositions, not false successful
acknowledgements. New selected-release events explain why the net count change
is not simply the number of dispositions. Five NRT dates (November 1–5) have
both required PRISM windows; November 6 still awaits its following window.

Routine revision scheduling now requests guarded baseline reuse (separately
tested scheduler wiring; the underlying reuse path was already validated).
No forcing outputs or restart files were rewritten/deleted by activation.
Installed cron timing and `--skip-model-runs` remain unchanged. The monthly cron
entry remains paused; policy activation does not install or enable schedules.

Post-cutover verification: 980 tests passed, 4 skipped; changed-file Ruff and
`git diff --check` passed. The cycle-window regression test now explicitly covers
both disabled/enabled policy states instead of reading the live activation marker.
The next routine revision job will exercise scheduler-enabled baseline imports;
no new full forcing replay was launched as part of this configuration cutover.
