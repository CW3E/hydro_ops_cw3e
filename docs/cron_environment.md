# Cron environment on AWARE login nodes

Use `bin/run_cron.sh` for each entry in `cron/hydro_ops.crontab`. Keep the schedule
installed on **one login node only**. On September 28, 2026, the user confirmed
manual installation of the updated schedule on **login1**. This is a user-reported
deployment confirmation, not a cross-host audit. Ensure no duplicate schedule
remains on login2. Future edits to the saved files still require reinstallation.

## Forcing recommissioning after thermal refresh (November 2026)

**Current update, November 12:** observation schedules are installed as described
in [station operations](station_collection_operations.md). The daily forcing row
now permits the CNRFC model hook, gated on catch-up job 5271018's accepted canonical
publication. B120, Yampa and UCRB are now also enabled after accepted catch-ups
and explicit approval; see [subdomain operations](subdomain_daily_model_operations.md).
Six-hourly still skips models and
monthly Retro remains paused. See [CNRFC reactivation](cnrfc_nrt_daily_operations.md#current-reactivation-gate--november-12-2026).
The November 10 notes below describe the earlier forcing-only installation.

On November 10, 2026, after the daily and six-hourly live tests passed, the
forcing-only daily, six-hourly and status entries were re-enabled on **login1**.
The installed daily and six-hourly commands explicitly include `--skip-model-runs`;
automatic model submissions remain disabled. Monthly Retro remains commented out
pending revision reconciliation. No observation entries were installed or enabled.
An SSH check confirmed no crontab for `mpan` on login2, and the clean-environment
wrapper preflight passed on login1. Before/after installed schedules are retained
in `forcing/status/cron/20261110-before.crontab` and
`forcing/status/cron/20261110-forcing-only.crontab`. Existing manually submitted
model campaigns are unaffected by this scheduling change.

`bin/acknowledge_thermal_revision_records.py` reconciles only exact-source queue
obligations backed by accepted thermal/rain publications and unchanged identities
for **all** published hourly, summary, subset and metadata artifacts. It defaults
to a dry-run; `--apply` preserves a complete queue snapshot, acquires the queue
lock and acknowledges only proven entries. Missing historical source pins remain
pending; neither numerical spot checks nor a completed SLURM job replace evidence.

The daily live test uses `slurm/test_forcing_cycle_after_model_refresh.sh`, dependent
on all three NRT refresh jobs. Its Python gate additionally requires all six Retro
and NRT model publication reports to match the campaign plan. It runs the ordinary
daily coordinator with `--skip-model-runs`, recording its output under
`forcing/work/cron-recommissioning/<job>/launch.json`. The launcher completing is
**not** full-cycle acceptance: review latest-hour publication, the daily revision
continuation, summaries, bounded replay and all domain subsets before proceeding.

After the complete daily chain passes, run the six-hourly cycle with the same
model-suppression flag and audit its descendants. Only then restore the forcing
daily/six-hourly/status entries on login1, checking login2 for duplicates. Preserve
monthly Retro's pause and existing observation scheduling; do not install the
entire saved schedule blindly. Automatic model hooks require a separate handoff.

Commissioning status, November 9 (local time): reconciliation acknowledged 202
NRT stream-days without changing forcing payloads. The retained queue has 39 NRT
and 200 Retro stream-days, which are unresolved provenance obligations rather than
proof of bad data. Evidence and the pre-change queue snapshot are under
`forcing/work/revision-backlog-audit/thermal-ack-applied-20261109*`.
Daily test launcher **5237789** depended on model refreshes **5237507**, **5237509**
and **5237511** and checked their accepted publication reports. Its full daily
chain passed, including summaries, bounded revision replay **5240063**, and all
four subdomain synchronizations. Six-hourly **5246055** passed in 15m25s,
publishing through November 10 13 UTC; CNRFC sync **5246056** passed in 37s.
These results authorized the forcing-only cron restoration described above.

## User accounts and operational reservation policy

The Unix execution account determines the deployment context:

| Account | Purpose | NRTRES policy |
| --- | --- | --- |
| `mpan` | Prototyping, development, benchmarks and operational-cycle tests | Do **not** request `NRTRES`, even to work around long queue waits. |
| `cw3ehydro` | Real operational production | Explicitly activate `NRTRES` for time-critical NRT work; prefer ordinary resources for monthly retro work. |

Slurm listing `mpan` among a reservation's permitted users is technical access,
not authorization under this project's operating policy. The earlier suggestion
to use `NRTRES` as a last resort for `mpan` is withdrawn. Do not substitute another
operations-only reservation as a workaround.

Current submissions run as `mpan` without an NRT reservation. No automatic
account-based reservation selection has been implemented by this documentation
change. Before deploying as `cw3ehydro`, explicitly configure and verify
reservation use for the operational compute jobs, including jobs submitted by
controllers and dependent follow-ups; changing the crontab owner alone does not
ensure that nested submissions request it. Also verify environment paths,
credentials, filesystem permissions and single-host scheduling for that account.
Do not run overlapping prototype and operational writers against the same outputs.

## Wrapper and scheduling

The previous log's fatal error was `FileNotFoundError: 'squeue'`, not a Python
`subprocess` import failure. Cron did not inherit the interactive SLURM module.
The preceding `which: no fi_info` came from the base Conda MPI deactivation hook.
Both symptoms were reproduced with a minimal environment.

The wrapper invokes the hydro-ops Python directly (no `conda run`, activation,
login shell, or `.bashrc`). It explicitly sets the environment's executable path,
AWARE SLURM paths/configuration/library paths, project import path, and single-thread
BLAS/OpenMP limits. It preserves the inherited home directory and credentials.
Startup logs include UTC time and hostname; `exec` preserves the command exit code.
This is a **site-specific login-node launcher**, not the MPI/Fortran runtime setup
for NWM compute jobs; those jobs retain their own module-loading scripts.

The project location is now discovered from the wrapper (or overridden with
`HYDRO_OPS_PROJECT_ROOT`); Python/SLURM defaults live in `config/site.env`, with
optional trusted `config/site.local.env` overrides. Render the schedule for a new
location using `bin/render_crontab.py`. See [project relocation](project_relocation.md)
for remaining script/manifest dependencies and the coordinated maintenance procedure.

From the project root, test without submitting production work:

```bash
env -i PATH=/usr/bin:/bin /usr/bin/bash bin/run_cron.sh --check
env -i PATH=/usr/bin:/bin /usr/bin/bash bin/run_cron.sh \
  bin/update_nwm_forcing.py --cycle six-hourly --dry-run
```

`--check` imports required Python packages, locates CDO/NCO, checks the SLURM
clients, and queries the live queue. It does not submit jobs or validate external
download credentials. The coordinator's dry run can legitimately report a skip
when a conflicting cycle is active. The same checks should be repeated if the
schedule is moved to another host or site modules change.

Review the saved schedule and existing crontab on the chosen host before installing.
Installing a file replaces that host's current user crontab, so preserve any unrelated
entries. The saved UTC schedule runs daily at **02:30**, with extension-only cycles
at **08:30, 14:30, and 20:30**. Daily includes the extension, so do not add a second
six-hourly invocation at 02:30. Status reporting remains every two hours at minute
30; retrospective promotion runs at **18:00 UTC on the 18th of each month**.
The monthly cycle is non-urgent and should use ordinary resources rather than
NRTRES when possible, including its dependent jobs. Current `mpan` submissions
do not request NRTRES at all. This preference must also be preserved when
configuring the future `cw3ehydro` deployment; do not apply a blanket reservation
setting to every cycle. The 18th gives a buffer after PRISM's approximate
mid-month updates, not a guarantee that every daily grid is stable; normal
stable-data eligibility checks still apply. The later UTC hour avoids the
early-day workload.
See [NRT scheduling](nrt_operational_extension_schedule.md) for the timing rationale.
Do not add a duplicate source-only refresh schedule.
The saved schedule also submits CDEC daily/monthly full-natural-flow refreshes
at **19:00 UTC daily** through `bin/submit_cdec_fnf.py`. It uses ordinary SLURM
resources, its own dataset lock, and `observations/logs/`; see
[CDEC observations](cdec_observations.md). Reinstall the saved schedule manually
after reviewing this addition; saving the row does not install it.
The SWE observation update follows at **19:15 UTC** through
`bin/submit_cdec_swe.py`. It skips active SWE campaigns and requires accepted
initial history. See [CDEC SWE observations](cdec_swe_observations.md) for its
native durations, incremental windows and historical revision policy. These
saved observation rows still require manual installation on login1.
Observe the first live cycle and its dependent jobs after installation; successful
environment tests are not an end-to-end production validation.

### Dependent web raster publication

Web publication has no separate cron row. The daily NRT forcing controller can
submit one dependent CNRFC/UCRB web job after both domains' exact forcing subset
and daily model jobs succeed. Six-hourly cycles do not trigger this hook.
`config/web_daily.json` controls submission (`enabled`, `mode`), while
`config/web_globus.json:production_enabled` controls live transfer advancement.
Initial automatic commissioning enables submissions only after its full live
test passes; inspect the activation receipt before assuming it is enabled.
See [web raster publication](web_raster_publication.md) for commissioning evidence,
job-specific acceptance, failure recovery and disable switches. No crontab
reinstallation is needed for this dependent hook. Missing/deferred model branches
defer the web update rather than using unverified older model results.
