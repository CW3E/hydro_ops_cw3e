# Explicit baseline cleanup plan

`bin/plan_baseline_cleanup.py` is read-only with respect to forcing data. It has
no deletion option. It creates a JSON inventory listing each eligible baseline
NetCDF and manifest sidecar by absolute path and inode/size/mtime identity.
Each entry names its retro replacement and captures the replacement's identity,
previously audited data checksum, and hashes/identities of the supporting audit
and manifest JSON files.

Checks require stable accepted PRISM, all 24 correctly dated calendar-hour
records, the current static-envelope mask/policy/content audit, matching
publication identities, and the CNRFC policy for dates on/after July 1, 2020.
The planner reuses prior scientific audits; it does not recompute full NetCDF
checksums or revalidate every field value. Any missing/stale/unaccepted
replacement is reported as blocked. The entire report passes only when every
requested day is eligible.

The September 22–23, 2026 plan is:

```bash
python bin/plan_baseline_cleanup.py \
  --start 2003-01-02 --end 2020-10-12 --workers 4 \
  --output forcing/status/cleanup/baseline-20030102-20201012-deletion-plan.json
```

This scope retains January 1, 2003 and October 13, 2020 as boundary buffers. It
does not include NRT-period baseline, final retro files, summaries, source data,
or leftover hourly files. It does not recursively delete directories.

Before any separately authorized deletion, check active and queued consumers,
revalidate the recorded baseline/replacement/evidence identities, and abort any
changed entry. A saved plan is evidence of eligibility at scan time, not a lease
preventing later changes. Keep the manifest and a separate execution journal
after cleanup; baseline files would otherwise require regeneration to recover.

`bin/execute_baseline_cleanup_plan.py --plan PLAN --journal NEW_JOURNAL` performs
identity/evidence preflight only. Explicit `--execute` enables deletion after
all entries pass. It confines targets to the exact dated baseline file and
sidecar, rejects symlinks and changed files, rechecks each entry immediately
before deletion, and writes durable per-day intent/completion records. It never
deletes directories or expands wildcards. Interrupted execution requires review
of the journal; it does not silently resume or skip missing targets.

The approved 2003–2020 plan's execution journal is
`forcing/status/cleanup/baseline-20030102-20201012-deletion-journal.jsonl`.
Only its final `completed` event establishes successful execution.

## Completed precipitation-repair originals (separate cleanup scope)

`bin/plan_forcing_repair_cleanup.py` inventories the original hourly snapshots
from the seven completed Retro precipitation-repair campaigns. This is **not**
the baseline cleanup plan format and must not be passed to the baseline deletion
executor. There is no deletion mode in this new planner.

For each proposed old file it verifies the snapshot identity, rejects symlinks,
protects any inode still used by the canonical replacement, and validates the
current rain-repair publication through its accepted audit/transaction chain.
The plan pins replacement identities and the hashes of all required JSON
evidence. It preserves that evidence, even where the old numerical files may
eventually be deleted. Reclaim estimates group device/inode pairs and count
space only when all known hard links are included in the plan; snapshots or
filesystem-level deduplication may still affect actual savings.

Job **5188478** completed on November 6, 2026. Its plan is
`forcing/status/cleanup/repair-originals-5188478.json`, with a separate progress
report. Expected final status is `review_required`, not deletion authorization.
An operator must review active/queued consumers and retire old retry paths that
still require originals before execution. All identities and evidence must be
checked again immediately before separately authorized deletion.

This first inventory excludes NRT originals, repair candidates/windows, old
summary backups, operational test workspaces, canonical forcing/baselines and
all model outputs/restarts. Summary backups and other large intermediate files
will require a subsequent scoped inventory. No files have been deleted by this
planning work.

The reviewed first batch contains 16,556 paths (approximately 72.7 TB of
reclaimable allocated storage). The December 31, 1980 snapshot remains protected
because its inode is still used by production. The user authorized execution
after consumer review. Active model jobs use canonical forcing/publication JSON,
and the operational revision tests use current baselines, not these snapshots.

`bin/execute_forcing_repair_cleanup.py --plan PLAN --journal NEW_JOURNAL`
performs preflight only; `--execute` enables the authorized deletion. The scoped
SLURM entry point is `slurm/execute_forcing_repair_cleanup.sh`. It holds the
CONUS precipitation-publication lock, rejects unreviewed active job entry points,
rechecks every original/replacement/evidence identity, and journals each unlink
durably. It accounts for multiple snapshot links to the same inode. No recursive
directory deletion is used. A final `completed` journal event, not submission
or SLURM completion alone, establishes success.

Snapshot `RETIRED.json` markers disable historical snapshot validation and
publication retries; existing canonical audit readers continue to use retained
JSON evidence. Future repairs must prepare new inputs rather than reuse retired
campaigns. Interrupted execution requires journal review and explicit recovery,
not blind resubmission. Deleted numerical originals cannot be restored by Git;
recreating their values would require regeneration from source data.

Execution job **5188493** was submitted after the consumer review and tests
(707 passed, 4 skipped). Its durable journal will be
`forcing/status/cleanup/repair-originals-deletion-5188493.jsonl`; its log is
`forcing/logs/repair-cleanup-execute-5188493.out`. Submission does not establish
that any deletion has occurred: preflight must pass first.
