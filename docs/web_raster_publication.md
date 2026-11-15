# Web raster pilot publication

Globus email policy: all repository transfer and remote-cleanup submissions,
including commissioning tests, request failure notifications only
(`notify_on_failed=true`, `notify_on_succeeded=false`,
`notify_on_inactive=false`). Use the same settings for ad-hoc station pilot
transfers. Inactive tasks still require monitoring through task status reports.
This submission policy does not alter already-submitted tasks or account-wide
notification preferences.

`bin/publish_web_raster_pilot.py` creates a self-contained transfer directory at
`web/publications/pilot-<job>/`. This is a presentation export, not a replacement
for scientific NetCDF archives. The directory combines forcing and model maps;
no changes are made to production cycles, model runs, or the web application.

Submit from the project root after loading `bin/project_environment.sh`:

```bash
sbatch slurm/publish_web_raster_pilot.sh
```

The pilot deliberately pins corrected CNRFC/UCRB example campaigns: land maps
5260671/5260679 and monthly forcing maps 5263662 from accepted climatologies
5263647/5263648. These are not generic operational discovery defaults. Samples
are April 1, 2015; April 1, 2023; September 30, 2025 and corresponding months.
There are six products per sample/domain, 36 PNGs total.

## Transfer package and consumer contract

- `catalog.json`: written last; only a completed package has this entry point.
- `legends.json`: exact RGBA colors, numeric units, lower-inclusive/upper-exclusive
  bins, transparent intervals, missing color, and customary-unit label conversion.
- `<domain>/family.json`: fixed EPSG:3857 footprint, dimensions, outer-edge
  geographic corners (NW, NE, SE, SW), north-to-south image rows and provenance.
- `<domain>/availability.json`: actual image combinations, relative links, UTC
  intervals, source fields, conversion rules, checksums and visible pixel counts.
- `<domain>/images/*.png`: RGBA data only, no annotations, antialiasing or margins.
- `acceptance.json`: local technical checks; not a claim of browser acceptance.

Transfer the entire completed directory, preserving relative paths. Scientific
source paths are lineage references relative to this repository, not downloadable
assets. Serve PNG/JSON with correct MIME types, HTTPS and appropriate CORS. Public
reuse terms/contact must be confirmed before deployment. The hydromet-map
publication guide is draft guidance; this pilot needs an adapter and browser
alignment/legend checks, not an assumption of a universal manifest reader.

## Geometry and colors

The fixed grid footprint is derived from the full current buffered-domain forcing
grid's cell corners, not legacy web bounds or daily valid-data extents. A nominal
1500-m Web Mercator image pixel sets dimensions once for each domain. The manifest
records exact extent/dimensions and grid hash; a changed grid requires a new family
version. Native cell quadrilaterals are projected and rasterized flat directly,
without filling holes or interpolating ranks. This is visualization sampling,
not a new scientific regridding product. The canvas has no added margin, although
cells outside the irregular mask remain transparent. Force nearest-neighbor
display in the browser to avoid inventing intermediate colors.

Temperature uses 5°C boundaries from −15 to 40°C (5–104°F), with 13 sampled
Matplotlib rainbow colors including saturated endpoint bins. Daily precipitation
is derived from mean RAINRATE times the actual 86400-second time bounds; storage
remains kg m-2 s-1. Values below 2.5 mm are transparent. Convert mm labels using
25.4 mm/in, round to two decimals, and never reclassify pixels on unit switching.
Daily soil/SWE ranks hide [30,70); monthly ranks hide [35,65). Missing and hidden
values have zero alpha but distinct documented meanings. SWE ranks <=1 mm were
excluded during native-grid rank calculation. No negative precipitation becomes
zero. The source palettes are pinned to hydromet-map commit
`9f91e4242470e1b3701833c61b31a30ecf4e247a`, with these explicit modifications.

PNG colors cannot support exact pixel value retrieval or distinguish multiple
transparent causes. Use scientific numerical companions for analytical queries.
Review representative wet/dry examples and browser placement before extending
this pilot into incremental operational publication.

## First pilot result

Job 5268948 completed in 25 seconds on 8 allocated CPUs. The package is
`web/publications/pilot-5268948/`: 18 images per domain, approximately 2.5 MB total.
CNRFC images are 1027×1323 pixels; UCRB images are 689×887. Technical acceptance
passed. Three unit tests cover thresholds, units metadata, transparent central
bins, invalid ranks, north-up orientation and exact image dimensions. Browser
overlay acceptance and user visual review remain pending. Initial attempt
5268940 stopped on an equivalent precipitation unit spelling; its incomplete
directory has no catalog and must not be transferred as a publication.

## Lossless indexed PNG experiment

`slurm/benchmark_indexed_web_rasters.sh` creates a separate transferable package
at `web/publications/indexed-pilot-<job>/`, leaving the RGBA pilot untouched.
`bin/benchmark_indexed_web_rasters.py` accepts explicit source/output directories.
The output must not already exist. It verifies source image checksums, builds an
exact dictionary of RGBA colors, stores palette indices with a PNG transparency
table, and checks every decoded RGBA component against the original. It does not
quantize, dither, change resolution, or collapse invisible RGB values. Inputs
with more than 256 distinct RGBA colors are rejected rather than approximated.
The smallest supported index width (1, 2, 4, 8 bits) is used with compression 9.

Each copied availability record has the new checksum and palette storage details;
the scientific encoding remains precolored RGBA after decoding. Geometry, bounds,
legends, time intervals, and filenames are unchanged. `encoding-benchmark.json`
records per-image byte sizes, encoding-plus-verification times, and five-read
mean local Pillow decode times. Those timings do not measure browser performance
or network latency. A catalog is written only after all conversions pass.
Rendering defaults and operational publication remain unchanged by this experiment.

Benchmark 5269590 passed for all 36 images in 11 seconds wall time (2 allocated
CPUs). PNG bytes decreased from 2,407,163 to 1,305,170 (45.8% reduction); the
largest image decreased from 277,365 to 156,124 bytes. Median sizes decreased
from 39,553 to 21,998 bytes. All decoded RGBA arrays are exactly identical,
including transparent pixels. Encoding plus round-trip verification took 4.08
seconds total. Mean local decode-to-RGBA time was 9.0 ms versus 5.2 ms; this is
not a browser benchmark. Package: `web/publications/indexed-pilot-5269590/`.
Ten focused tests passed, including transparent/partial-alpha colors, palette
sizes from 1 through 256, and rejection above 256 colors.

## Multi-stream integration collection (October 2025–November 2026)

`bin/build_web_raster_collection.py` provides `plan`, `worker`, and `finalize`
commands with an explicit `--output` directory. Planning freezes source identities
and corrected climatology references. Workers render one domain/month each;
the finalizer requires every shard, verifies image hashes, and publishes the
catalog last. This is an offline test export, not a cron integration. Do not
transfer a directory until its `catalog.json` and passed `acceptance.json` exist.

The corrected full inventory is `web/publications/test-20251001-20261111-v2/plan.json`:
28 domain/month tasks, 1,747 source files, 3,494 indexed PNGs including overlapping
retro/NRT products. Model ranks are computed from current daily model outputs
against the accepted WY1980–2025 climatologies, not copied from old previews.
The fixed geometry and legends remain identical to the first pilot. Complete
monthly forcing products are included through October 2026; partial November
is not advertised as a complete monthly product.

Each domain's `availability.json` exposes all stream alternatives. Asset `id`s
are unique descriptive filename stems. `timeline.json` groups by product,
variable, resolution and date and names one `preferredAsset` (retro before NRT),
alongside `availableAssets`. The web client can navigate the default timeline
and optionally select a different stream. All paths resolve relative to the
document containing them. The dates label UTC interval starts, not publication
dates. `coverage.json` records missing combinations in the requested period;
no image is invented for a missing model day. Forecast assets are not included.

**Scientific caveat:** current model snapshots do not necessarily incorporate
recent forcing revisions. These maps test web presentation, not forcing/model
revision synchronization. The caveat is carried in both family and acceptance
metadata. Model outputs are neither rerun nor altered by this export. Canonical
source identities are checked around each task; later source revisions do not
change the already frozen image snapshot.

Use `--smoke` when planning to select one April 2026 source for each available
product/stream/resolution/domain. The full build should depend on successful
smoke workers. Output paths are immutable: existing images are not overwritten.
An interrupted shard needs explicit review/recovery or a fresh collection plan.

Submission chain: corrected smoke array **5270578**, full array **5270580**
(28 tasks, concurrency 8, 8 CPUs/task, maximum 64 CPUs), and finalization
**5270581**, each dependent on its predecessor's successful completion.
Initial smoke 5270563 caught CHRTOUT files in a broad daily inventory glob;
the corrected scan explicitly selects LDASOUT for model maps. Dependent first
full array 5270574 was canceled by its failure dependency. The incomplete `v1`
and smoke folders are not publications; use only the finalized `v2` catalog.
Eleven focused renderer/timeline tests pass. Forcing daily coverage reaches
November 11, 2026; available daily model files reach November 10 (CNRFC) and
November 7 (UCRB). Missing model dates are listed rather than synthesized.

## V3 representative publication contract

`plan --pilot-v3` selects six representative days (2015-04-01, 2023-04-01,
2025-09-30, 2025-12-13, 2026-04-12, 2026-09-15) and their monthly forcing
products for CNRFC and UCRB. This is a sparse sample, not continuous coverage.
Build `web/publications/pilot-v3-20261112/` separately; v2 is unchanged.

V3 adds `variables.json` with source fields, units, time bounds, aggregation,
soil-layer weights, percentile methods, leap-day treatment and sample thresholds.
Packed/masked NetCDF values are decoded before calculations. Negative rain and
invalid temperatures are excluded before ranking. All invisible PNG pixels are
transparent black, including deliberately hidden legend bins. Indexed encoding
remains lossless; nearest-neighbor display preserves discrete classes.

Per-asset quality counts distinguish source grid cells from rendered image pixels:
outside domain, missing, invalid, SWE below cutoff, unavailable statistic,
intentionally hidden and visible. A fully transparent image can contain valid
data and must not be dropped merely because its visible count is zero.
Named filesystem identity fields use decimal strings; source SHA-256 and code
hashes identify the exact snapshot. Local source paths are provenance references,
not URLs relative to the web manifest.

Coverage reports distinguish absent requested products from incomplete months,
and summarize each product/variable/resolution/stream. Retro/NRT alternatives
remain in availability and the preferred timeline. The November cutoff month is
explicitly incomplete, not a missing completed month.

Transfer only after `acceptance.json` passes and `catalog.json` exists. Publish
under a revision-specific URL, never overwrite v2. Plan/shard files are internal
build evidence, not required by the web client. Browser acceptance remains
pending. The pilot is evaluation-only: public contact and approved reuse terms
have not been supplied, so `publicReleaseReady` is false. Existing model/forcing
synchronization caveats remain applicable; this export does not rerun the model.

V3 submission: array **5278718** (12 domain/month tasks, 38 frozen source files,
76 expected PNGs, concurrency 8 at 8 CPUs/task); acceptance/catalog finalizer
**5278749** runs only after successful completion of the array. Submission is
not acceptance: consult this release's own `acceptance.json` before transfer.

This release passed finalization (5278749, 2m45s): 38 CNRFC and 38 UCRB PNGs,
76 total, 2,696,131 bytes (largest 162,547 bytes). No requested sample gaps;
two valid fully transparent precipitation images are retained. Every decoded
color, image checksum and quality-count partition passed. Browser validation
is still pending. The full repository suite passed (994 passed, 4 skipped),
as did Ruff and diff checks. `README.txt` lists the web-facing transfer files.

## V4 continuous October–November collection

`--collection-v4 --start 2026-10-01 --end 2026-11-11` exports continuous daily
coverage for both domains, plus complete October monthly forcing products.
The fixed endpoint uses existing files and does not wait for running daily
cycles. Frozen identities reject sources changed during rendering. This is a
snapshot export, not a claim that all model states reflect every forcing revision.

Output: `web/publications/collection-v4-20261001-20261111/`. Array 5279499 has
22 bounded tasks, 170 sources and 340 expected scientific PNGs; concurrency is
eight tasks with eight CPUs each. Dependent finalizer **5279515** requires every shard and no
missing requested daily/monthly combination. Consult acceptance.json before
transfer; submission alone is not acceptance.

V4 uses canonical `Cel` for temperature in definitions and legends (documented
alias `degC`). Rank unit is `1` in both, display label `%`, with structured
0–100 scale metadata explicitly disabling multiplication by 100 or physical-unit
conversion. Monthly climatology table SHA-256 identifies the actual table;
daily provenance continues to identify the acceptance manifest containing tile
checksums. Family statistics now point to authoritative variables.json instead
of repeating potentially inconsistent scientific definitions.

Daily aggregates require 24 hourly endpoints; complete months require every
calendar day, equally weighted. Missing forcing cells propagate rather than
being skipped. The publisher checks source sample counts and interval bounds;
it does not independently re-read the hourly history. These requirements are
distinct from climatology minimum sample counts.

`validation-fixtures/` contains explicitly synthetic invalid-estimate and
unavailable-statistic checks plus a February-29 threshold interpolation check.
They are excluded from scientific families, coverage and timelines. There is no
February 29 in the requested publication interval. Browser acceptance remains
separate: verify alignment, discrete colors, interval labels and retention of
valid transparent maps. This interval currently has NRT assets only; retro/NRT
preference remains covered by the v3 overlap samples and unit tests.

## V5 short labels and exclusive streams

`variables.json` now exposes `shortLabel` alongside the unchanged descriptive
`label`: Daily SM %-ile, Daily SWE %-ile, Daily P, Daily T, Monthly P %-ile and
Monthly T %-ile. Consumers can use shortLabel for selectors and label for
tooltips. This is an additive metadata change; scientific calculations, palette
thresholds and units do not change. V5 is the publication revision; the compatible
metadata convention remains 0.4.

Build with `--collection-v4 --exclusive-streams --start 2025-10-01 --end 2026-11-11`.
Exclusive selection retains Retro when present, otherwise NRT, independently
per domain/product/resolution/interval. Only the selected asset is published,
not both stream alternatives; finalization rejects any duplicate timeline key.
Existing v2–v4 publications are unchanged.

Release: `web/publications/collection-v5-exclusive-20251001-20261111/`.
Its inventory has 407 daily dates and 13 complete monthly intervals per domain,
without gaps or overlapping assets (1,654 sources / 3,308 expected scientific PNGs).
Daily forcing switches to NRT on April 18, 2026 in both domains. Daily model
maps switch on April 17 for CNRFC and April 18 for UCRB. Monthly forcing maps
switch with April 2026. Clients must use the per-variable timeline rather than
assuming one cutoff for every product.

Array **5281915** runs 214 bounded tasks, maximum eight concurrent tasks with
eight CPUs each. Dependent finalizer **5281928** validates coverage and writes
the acceptance/catalog. Do not transfer before acceptance passes. Superseded
overlapping build **5281882** was canceled and its folder
`collection-v5-20251001-20261111/` is marked DO_NOT_PUBLISH; it is not a release.
The new README lists transfer files; synthetic fixtures remain outside all
scientific timelines. Browser acceptance of v5 is separate from these checks.

## Mexico display exclusion for soil-water ranks (v6)

`bin/mask_web_publication_mexico.py` creates a complete replacement publication
from an accepted immutable collection. It clips **only** soil_water_percentile
PNGs using pixel-center containment within the Natural Earth 1:10-million Mexico
country polygon (including polygon holes/islands), not a latitude cutoff. At
this cartographic scale the border is suitable for display, not cadastral use.
The fetched source checksum and extracted GeoJSON are retained in provenance.

This is a PNG display transform: scientific archives, climatology, source-cell
counts, forcing/model domains and all other variables remain unchanged. Every
non-SM PNG is copied and checked byte-for-byte. SM pixels outside Mexico must be
exactly identical after lossless indexed encoding; all pixels inside Mexico
must be transparent black. No rank recalculation or scientific rerun is needed.

For affected assets, qualityCounts.sourceCells stays pre-display and the old
image counts are retained as preDisplayImagePixels. New imagePixels reports
visible, transparent, displayExcluded, newlyHiddenVisible, and
previouslyTransparentOutsideDisplayExclusion. The disjoint partition is
visible + displayExcluded + previouslyTransparentOutsideDisplayExclusion = total.
Display exclusions include already-transparent pixels in Mexico and must not
be interpreted as missing estimates. display-policy.json is authoritative.

Job **5287597** builds
`web/publications/collection-v6-mexico-sm-20251001-20261111/` from accepted v5.
The complete package retains short labels, exclusive Retro/NRT timelines,
geometry, palettes and coverage. Transfer the entire folder only after its own
acceptance.json passes and catalog.json exists. V5 remains unchanged.

Job 5287597 completed in 4m27s and the v6 acceptance passed for all 3,308 PNGs:
814 soil-moisture images processed and 2,494 other images byte-identical.
CNRFC has 36,973 Mexico pixel centers; UCRB has none, so its displayed maps
are unchanged. Across the collection 1,534,802 previously visible pixels were
hidden. All SM pixels outside the exclusion were checked for exact equality.
Full repository tests: 1,000 passed, 4 skipped; Ruff and diff checks passed.
The folder is complete for transfer; browser verification remains separate.

## Globus deployment commissioning and storage policy

The public entry point is
`https://cw3e.ucsd.edu/hydro/hydro_ops/nwm/catalog.json`. Its confirmed destination
in collection `de258060-df3f-4eaa-b10e-3799d6e6604f` is
`/projects/website/mirror/htdocs/wrf_hydro/hydro_ops/nwm/`.
**The web server aliases HTTPS `/hydro/` to filesystem `wrf_hydro/`.** Keep the
public URL and collection filesystem path separately configured; never derive
one by simply appending the HTTPS path to the collection root. The historical
`wrf_hydro/nrt_hydro/nwm/` path is NOT this publication's destination.
The user confirmed this mapping on November 14, 2026. Source collection
`63ab9e0e-2049-4aff-bfec-5dfb64aadc11` sees this project's files under
`/mead/projects/cwp206/agentization/hydro_ops/`, without the local `/cw3e` prefix.
`config/web_globus.json` explicitly configures these mappings; update the source
mapping if relocating the project. Credentials are never stored in that config.

**Server storage/update policy (user-approved relaxation, November 14):** use
ordered in-place updates under the existing public root, NOT immutable remote
release directories or atomic release switching. A short period of mixed old/new
content is acceptable. Do not retain historical full publication copies remotely.
Keep audit receipts and any recovery data locally.

The intended deployment phases are:

1. Freeze a validated local publication and inventory; transfer changed data
   files (PNGs, station records, extracted series), overwriting their owned paths.
2. Only after that task succeeds, transfer dependent definitions, legends,
   indexes and family/availability/timeline metadata.
3. Only after those tasks succeed, transfer root catalog.json last.
4. Verify public HTTP content, then delete only obsolete files in the previous
   deployment inventory that are absent from the new referenced-file inventory.
5. Confirm deletion completion and record a successful deployment receipt.

Classify files by manifest ROLE, not extension: a station JSON record is payload,
not publication metadata. Files within one Globus task have no assumed ordering;
use distinct tasks with explicit success gates. On failure, resume the phase;
do not claim the old deployment is intact after in-place overwrites and do not
start metadata/cleanup prematurely. Avoid concurrent deployment writers, even
when upstream domain pipelines run independently. Source publications stay
frozen until transfer, verification and cleanup are resolved.

Unchanged files remain in place; checksum-based selection prevents mtime-only
misses. No second full server-side collection is required, although the transfer
service can need temporary space and obsolete files remain until cleanup. Never
use recursive destination-extra deletion over the web root. Initial legacy
inventory/migration must identify owned v6 assets, excluding unrelated content.
Short metadata cache lifetimes/revalidation remain useful: bounded inconsistency
is accepted, not indefinite browser caching of stale manifests. Orphaned old
stream paths are removed only after the new exclusive timeline is public.
The manifest-based deployment and cleanup executor is now implemented (see below),
but live deployment remains disabled. There is no rename/atomicity gate for routine
deployment under this relaxed policy.

The transport implementation is `src/hydro_ops/web/globus_transfer.py` with
`bin/web_globus.py`. This first stage supports only a two-file isolated test under
`_hydro_ops_transfer_test/<unique-test-id>/`; live catalog replacement, rename,
and deletion commands are deliberately absent. It uses SDK v4 (optional `web`
package dependency; included by environment.yml), checksum synchronization and
external SHA-256 checksums, nonrecursive explicit items, durable submission/task
IDs, and separate transfer/HTTP status. A submission timeout can be retried using
the persisted Globus submission ID. Existing task IDs are monitored rather than
submitted twice. A failed task requires review; status never implies publication
or activation merely because the CLI exited successfully.

Authentication reuses the existing `~/.globus_transfer_tokens.json` format from
`/cw3e/mead/projects/cwp206/scripts/utils/globus_login.py`. Configure the existing
native client ID (not a password) in ignored `config/site.local.env`:

```bash
export GLOBUS_NATIVE_CLIENT_ID="<the existing native application UUID>"
```

The original login utility remains the interactive authentication entry point
for this stage. Never paste refresh/access tokens into tracked files or logs.
New code requires a private nonsymlink token file, serializes refresh with a
lock, and atomically replaces it at mode 0600. The original utility does not
participate in this lock: avoid concurrent token writers until migration is
complete. Operational `cw3ehydro` needs its own authorized credentials; do not
copy `mpan` credentials into that account.

Commissioning commands (no need to occupy a large SLURM allocation while Globus
performs the asynchronous transfer):

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/web_globus.py prepare-test
# Use the exact plan.json printed by the preceding command:
"$HYDRO_OPS_PYTHON" bin/web_globus.py submit-test --plan <plan.json>
"$HYDRO_OPS_PYTHON" bin/web_globus.py status --plan <plan.json>
# Only after task_status=SUCCEEDED:
"$HYDRO_OPS_PYTHON" bin/web_globus.py verify-http --plan <plan.json>
"$HYDRO_OPS_PYTHON" bin/web_globus.py cleanup-plan --plan <plan.json>
```

`cleanup-plan` writes an inventory only; it does not delete anything. Test files
are tiny and public, with no credentials/scientific records. Do not upload
plans/state/token files. Task state lives beside the local plan under
`web/work/globus-tests/`. HTTP verification rejects redirects and compares exact
response bytes against the accepted payload. SDK exception bodies are not printed.

Next gates: isolated real transfer and HTTP verification; interruption/resume;
two consecutive small in-place test updates with separate payload/metadata/catalog
tasks; then manifest-owned deletion and failed-phase recovery tests. The revised
policy does not require remote rename or atomic catalog switching. Keep live
deployment disabled until tested. No cron changes are made by these utilities.

Initial offline commissioning (November 14, 2026): SDK 4.10.0 installed, 11
focused tests passed; public catalog returns HTTP 200 and identifies v6. An
isolated plan was prepared at
`web/work/globus-tests/20261114T182155Z-33a6f775cd3f/plan.json`.
No remote transfer was submitted: GLOBUS_NATIVE_CLIENT_ID was absent from the
project environment. Existing token-file presence and mode were checked without
reading or displaying credentials. Configure the native client ID before the
live test; the utility need not reauthenticate if the existing grant is valid.
The full regression suite passed: 1,011 passed, 4 skipped; Ruff and diff checks
also passed. These are offline checks, not a successful live Globus transfer.

After the user configured the native client ID, live task
`ecf96c3c-e058-11f1-9064-0affc602d875` succeeded. Both probes exist on the configured
destination (107-byte JSON and 63-byte text), but HTTPS verification returns 404.
Cleanup and activation remain blocked. Listing the configured nrt_hydro/nwm root
showed only our test directory, not the existing v6 publication. Read-only stat
found a 403-byte catalog at
`/projects/website/mirror/htdocs/wrf_hydro/hydro_ops/nwm/catalog.json`, a likely
corrected destination. No files were written there and configuration remains
unchanged pending confirmation. The test probes remain under their exact unique
test namespace for diagnosis; no production data or live catalog was changed.

The user then confirmed the `hydro` → `wrf_hydro` HTTP/filesystem alias and the
corrected `wrf_hydro/hydro_ops/nwm/` destination. The config was updated without
changing the frozen first test plan. Replacement test
`web/work/globus-tests/20261114T183527Z-15de52f22f41/plan.json` submitted task
`3246d11e-e05a-11f1-8223-0effc68fde15`, which SUCCEEDED. Both public HTTPS probe
responses matched their planned SHA-256 bytes. State is `http_verified`; the
exact two-file cleanup inventory exists but no remote deletes were submitted.
Twelve focused tests pass, including the configured alias regression. The next
gate is ordered in-place update/retry/cleanup testing in the isolated namespace,
not immediate live deployment. The live catalog and all raster files remain
unchanged; both small commissioning probe sets are retained pending cleanup.

### Ordered in-place commissioning

`bin/test_web_globus_ordered.py` exercises the proposed ordering in a fresh
`_hydro_ops_transfer_test/<unique-id>/` namespace, never the live catalog.
It creates two frozen generations, then runs seven separate Globus tasks:
generation-1 payload, metadata, catalog; generation-2 payload, metadata, catalog;
then exact-file deletion of the obsolete generation-1 payload. JSON station-like
records deliberately count as payload, demonstrating that roles are not inferred
from filename extensions. An unchanged payload remains in both inventories.

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/test_web_globus_ordered.py prepare
# Repeat this nonblocking command using the printed plan until phase=passed:
"$HYDRO_OPS_PYTHON" bin/test_web_globus_ordered.py advance --plan <plan.json>
```

Each invocation either submits one task, polls it, or verifies its public bytes.
Submission IDs are saved before requests; rerunning after interruption reuses the
same submission/task instead of creating duplicate writers. No later phase starts
until the preceding task succeeds and HTTPS checks pass. A FAILED task blocks the
sequence; `advance --retry-failed` archives its receipt and resets that phase for
a subsequent submission. HTTP mismatches also block progress; resolve them before
retrying. Never edit the frozen plan or payload in place.

During generation 2, HTTPS checks explicitly verify that metadata/catalog still
contain generation 1 until their respective phases execute. Before deletion,
all generation-2 files must match their accepted checksums. The written
`deletion-manifest.json` contains only old-minus-new test paths. Deletion is
nonrecursive, disables wildcard interpretation, and never uses a destination-wide
extra-file cleanup option. Afterward, every retained file is rechecked and the
obsolete URL must return 404 or 410. Local manifests, frozen payloads, and receipts
remain available for diagnosis/replay.

The test does not configure server cache headers. Operational metadata should
have short cache lifetimes/revalidation; in-place PNG replacements can briefly
appear stale in browsers. Our no-cache verification requests cannot invalidate
already cached client content. This is separate from the accepted short period
of mixed versions while payloads are transferred.

This remains a commissioning runner, not an enabled operational publisher.
Production needs a shared destination lock, an accepted full-publication inventory,
cycle dependencies and durable deployment receipts. Keep
`config/web_globus.json:production_enabled` false until that integration is tested.

November 14 ordered test result: **passed**. Plan and receipts are under
`web/work/globus-ordered-tests/20261114T184044Z-0ed578842b0d/`. All seven Globus
tasks succeeded and their public HTTPS checks passed. The generation-2 payload
task transferred two files and skipped the unchanged third file using checksum
comparison. The cleanup task `34d9296d-e05b-11f1-af7d-0affc602d875` deleted only
`obsolete.json` inside that unique test namespace; its URL returned 404, while
all five retained generation-2 files still matched their checksums. The deleted
test payload remains recoverable from the frozen local generation-1 inventory.
`task-summary.json` records all task IDs and transfer counts. Tiny retained test
files are not production history; neither the live v6 catalog nor its PNGs were
changed. Earlier commissioning probe sets remain untouched.

Offline tests exercise uncertain submission retries (same saved submission ID),
active-task resumption without duplicate submissions, terminal failure blocking,
explicit retry with an archived failed receipt, changed-source rejection,
HTTP mismatch blocking, and exact obsolete-file cleanup. Failures were injected
offline, not deliberately caused against the live Globus service. The real test
resumed from persisted state across separate CLI processes for every phase.
Fourteen focused tests and the full suite (1,014 passed, 4 skipped) passed;
Ruff and `git diff --check` passed. Next: generalize this validated sequence into
the publication-inventory executor, then test daily-cycle integration before
enabling live automatic publication.

## Manifest-based publication runner (not enabled in cron)

`bin/publish_web_collection.py` and `src/hydro_ops/web/publication.py` implement
the ordered payload → metadata → catalog → verification/cleanup sequence for
accepted publication inventories. Public paths and role assignments are explicit;
the executor can handle JSON payloads without treating them as metadata. The
current CLI inventory adapter supports finalized raster collections. Station and
extracted-series adapters can later feed the same inventory schema.

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/publish_web_collection.py plan \
  --publication web/publications/collection-v6-mexico-sm-20251001-20261111
"$HYDRO_OPS_PYTHON" bin/publish_web_collection.py status --plan <printed-plan.json>
```

Planning reads the accepted catalog, families, availability indexes, and PNG
checksums. Only referenced images and public definitions/indexes enter the
inventory. Internal plans, acceptance receipts, shards, credentials and logs are
not transferred. Missing assets, changed asset hashes, duplicate asset paths,
catalog count mismatches and unsafe relative paths stop planning. This adapter
does not replace the renderer's scientific/geometry/stream-exclusivity audit.

Execution requires **both** a matching configuration with
`production_enabled=true` and `advance --enable-transfer`. The repository default
remains false; planning and status require no credentials or remote writes.
After enablement, each `advance` invocation performs one nonblocking step. It is
not yet a scheduled worker. `--retry-failed` resets only a confirmed FAILED phase,
preserving the old receipt; unknown submission outcomes reuse the saved
submission ID. A source checksum change or HTTPS mismatch blocks further phases.
Do not edit frozen sources to resolve a blocked deployment without a recovery plan.

Local operational layout:

- `web/publications/<collection>/`: accepted source collection, frozen until the
  deployment finishes; keep local recovery material until the new receipt passes.
- `web/work/deployments/<id>/`: immutable plan, inventory, phase state, and explicit
  deletion manifest. These are private operational artifacts, not web payloads.
- `web/status/deployments/<destination-key>/`: writer lock, durable owner, and
  last accepted deployment inventory. The key uses collection ID plus filesystem
  root; it does not confuse the HTTPS alias with the Globus path.

The durable owner prevents another deployment from writing between asynchronous
tasks even after the launching process exits. Failed deployments retain ownership;
do not remove `owner.json` merely because the launcher is gone. Resume or explicitly
resolve the outstanding transfer first. Plans pin the prior accepted deployment
ID; a plan prepared against an older predecessor must be regenerated. Receipt
and owner recovery handle a crash at the end of successful publication.

Cleanup is derived solely from the prior accepted inventory minus the new one.
There is no recursive remote scan/delete and unrelated server files are untouched.
Without a previous accepted receipt, the first deployment deletes **nothing**;
adopting legacy v6 ownership requires a separately verified bootstrap inventory,
not an assumption that everything below the destination belongs to us. Obsolete
files are deleted only after public checks pass, and retained files are verified
again before the successful receipt is recorded. Current verification is exhaustive;
benchmark its cost with a representative full daily update before enabling cron.

### Daily-cycle integration gate

The existing forcing/model cron scripts are unchanged by this runner. The next
integration must submit one serialized web coordinator after successful publication
of the exact forcing summaries and CNRFC/UCRB model jobs used by the renderer
(`afterok` dependencies, plus job-specific success receipts). Do not rely on a
fixed clock time or mutable `latest.json` alone. It should:

1. Pin source receipts and discover changed forcing/model dates, including monthly
   summaries; preserve unchanged accepted images and rebuild affected ones.
2. Finalize/audit one complete collection with exclusive Retro/NRT intervals,
   established legends, short labels, and the Mexico soil-moisture display mask.
3. Prepare a publication plan and repeatedly advance it, preserving deployment
   state across launcher retries. A failed domain/render audit must not publish a
   partially built catalog or erase its previously published coverage.
4. Record deployment/HTTPS/cleanup status separately from rendering and model
   completion. Enable cron only after a representative dependency-driven test.

No live v6 replacement, cron installation or production enablement is implied by
the successful isolated transport tests.

Runner validation, November 14: two successive deployments using the general
executor both reached `published` under the isolated namespace
`_hydro_ops_transfer_test/20261114T184801Z-1c8075a740c9/`. Local plans are
`web/work/deployments/20261114T184801Z-e7f4082e4ada/plan.json` and
`web/work/deployments/20261114T184909Z-13095bc60618/plan.json`. The second cleanup
task (`3371275e-e05c-11f1-8cc4-02ffe3faed53`) removed only the obsolete test JSON;
HTTP verified its disappearance and every retained checksum. The frozen local
first generation retains that tiny deleted payload for recovery. No live files
were deleted or replaced.

The read-only v6 production plan is
`web/work/deployments/20261114T184915Z-37056b28a700/plan.json`: 3,308 payload PNGs,
10 metadata files and one root catalog, totaling 128,701,478 bytes. It has no
previous accepted deployment and therefore no deletion candidates. An explicit
transfer attempt with this disabled configuration was correctly refused before
authentication or remote submission. Twenty-six focused web transport/publication
tests pass, including durable-owner exclusion, stale-plan rejection, uncertain
submission recovery, terminal failure, HTTP corruption, frozen-source mutation,
receipt/state crash recovery, and exclusion of private internal files.
The full regression suite also passed: 1,026 passed, 4 skipped; Ruff and
`git diff --check` passed.

## Incremental daily collection prototype

`bin/build_web_daily_collection.py` implements an isolated `plan`, `worker`,
`finalize` workflow. It does **not** enable remote transfers or modify cron.
The existing accepted renderer remains unchanged; the incremental wrapper uses
it only for selected changed/new source files and maintains the v6 display policy.

- Choose exactly one source per product/resolution/date, preferring Retro over
  NRT. A stream handoff changes the source path and requires a new render.
- Reuse both accepted PNGs from an unchanged source snapshot. If only the file
  identity changed, compare the source SHA-256 before deciding to rerender.
  Reused assets retain their validated PNG checksums and update snapshot identity.
- Reject reuse if renderer code, static-domain metadata, or climatology pins
  changed. Such changes need an explicit full-rebuild decision.
- Give each render attempt its own directory and each task a lock and acceptance
  receipt. Repeating an accepted worker verifies its images and does no rendering.
  An interrupted task can retry in a new attempt directory without overwriting
  accepted or partially written PNGs.
- Apply the **pinned local** Mexico geometry to newly rendered soil-moisture
  percentile images only. No boundary download occurs during a daily build.
  Preserve original scientific counts separately from post-display counts.
- Assemble a complete collection using verified hardlinks for PNGs. These linked
  images must never be edited in place. Rebuild family/index/coverage/catalog
  metadata, preserve short labels/legends, reject stream overlap and coverage
  gaps, and recheck source snapshots before acceptance.

Optional repeated `--upstream-receipt <job-specific.json>` arguments pin successful
`passed`/`complete` receipts and recheck their SHA-256 values throughout processing.
Mutable `latest.json` is rejected. This prototype allows receipt-free isolated
snapshot tests; **that is not a sufficient operational readiness gate**. The daily
scheduler must still require the exact forcing-subset and CNRFC/UCRB model receipts,
verify their domains/intervals/job IDs, and connect the corresponding `afterok`
dependencies before production enablement. No such scheduler hook is enabled yet.

Prototype layout is `web/work/daily-tests/<test>/`, with `render/plan.json`,
`cycle.json`, task `attempts/`, accepted `results/`, and a complete `publication/`
subdirectory. Runtime plans/receipts are not web payloads. The transport inventory
now includes `display-policy.json` and `provenance/mexico.geojson`, which v6 family
and variable metadata reference; the earlier 3,319-file dry-run inventory omitted
these two public support files and must be regenerated before deployment.

Test submission **5308854**, `slurm/test_web_daily_collection.sh`, requests eight
CPUs for at most one hour on `shared-128` without a reservation. It uses November
10–11, 2026, in CNRFC and UCRB. Generation 1 refreshes the accepted v6 snapshots;
generation 2 must reuse eight images for November 10 and deliberately rerender
four source files/eight images for November 11. Both resulting generations must
have identical PNG hashes. Repeated worker invocations also check resume/no-op
behavior. Report: `web/work/daily-tests/job-5308854/acceptance.json` when complete.
Submission is not acceptance; inspect that report and the SLURM log before
advancing to a longer revision/extension and dependency-driven cycle test.
Offline checks for this prototype passed: 30 focused tests; full regression suite
1,030 passed, 4 skipped; Ruff and `git diff --check` passed. Scientific rendering
and incremental equivalence remain subject to the running SLURM test.

Job 5308854 subsequently passed in 4:03 with eight CPUs. Both domains had complete
two-day coverage (16 PNGs total). Generation 2 reused eight PNGs and rebuilt eight
from four source files; all final PNG hashes matched generation 1 exactly.

### Real source-revision test

Job **5308982** (`slurm/test_web_real_revision.sh`) tests November 1–3, 2026,
against accepted v6 with **no forced render dates**. Snapshot inspection found
unchanged November 1 inputs, replaced November 2 forcing, and replaced November 3
forcing/model inputs. Replacement alone is not proof of scientific change: the
test records old/new source hashes, PNG hashes, and decoded RGBA changed-pixel
counts for each asset. Acceptance requires actual source-content changes, visible
pixel changes somewhere in the collection, and reuse of unchanged assets. A
source change need not change its displayed bins; that is recorded separately.

The second cycle uses the newly accepted collection as parent and must create
zero rendering tasks, reuse every PNG, and preserve every PNG checksum. Reports
are under `web/work/daily-tests/real-revision-job-5308982/`, with per-generation
`comparison.json` and a final `acceptance.json` only on success. Eight CPUs and a
one-hour limit are requested; no reservation, remote publication, source-data
mutation, or cron activation. This is a daily revision/no-op test, not a new-date
extension or monthly-summary revision test. Five focused incremental tests,
Ruff, shell syntax and diff checks passed before submission.

Job 5308982 passed in 1:39 on eight CPUs. The revision generation took 94.1 seconds:
six source files changed, 12 PNGs were reused, and seven PNGs changed visibly
(9,733 pixel changes across the collection). The unchanged repeat took 2.6 seconds,
reused all 24 PNGs, and scheduled no rendering tasks.

### Extension and monthly gates

`bin/test_web_extension_monthly.py` and its SLURM wrapper exercise two independent
local-only tests, each requesting eight CPUs, two hours, and no reservation:

- **5309052**, `extension`: use the accepted November 10–11 collection from job
  5308854 and extend through November 12 with existing forcing/model outputs.
  Require eight new daily PNGs, report any legitimate intervening revisions to
  old dates, and require a second cycle to reuse all 24 images without rendering.
- **5309053**, `monthly`: build October 1–30, 2025, without monthly maps, then add
  October 31 and require eight new daily PNGs plus four monthly percentile maps.
  No monthly source replacements were found since v6. Therefore revision testing
  uses explicitly labelled **synthetic copies**, not a claimed real provider
  revision: add 3 K to monthly T2D and multiply monthly RAINRATE by 1.5. Only two
  monthly source files may trigger rendering; all 248 daily PNGs must be reused
  and byte-identical. At least one monthly PNG must change. A final unchanged
  repeat must reuse every image. Production monthly files are copied, never
  hardlinked for mutation, and original source checksums are rechecked afterward.

Reports are respectively
`web/work/daily-tests/extension-job-5309052/acceptance.json` and
`web/work/daily-tests/monthly-job-5309053/acceptance.json`, written only on test
success. Controlled monthly maps are test artifacts and must not be published.
Neither test transfers files or changes cron. Thirty-one focused tests, Ruff,
shell syntax and diff checks passed before submission; inspect actual acceptance
reports separately from SLURM completion.

The incremental assembler now holds its provisional acceptance receipt outside
the publication directory until inventory validation succeeds. Only then is
`publication/acceptance.json` written, so an inventory failure cannot leave a
new collection falsely marked accepted. Normal transfer planning continues to
require that final public-directory acceptance marker.

Both extension/monthly tests passed. Job 5309052 took 1:33: the extension added
eight PNGs in 88.0 seconds while reusing 16; the repeat reused all 24 in 2.3 seconds.
Job 5309053 took 5:23: month-end arrival took 193.1 seconds (eight new daily and four
monthly maps); the controlled monthly revision took 106.9 seconds, changed all four
monthly PNGs, and reused 248 unchanged daily PNGs. Its repeat reused all 252 images
in 9.4 seconds. No production inputs or remote files were changed.

## Job-specific daily coordinator gate (isolated test)

`src/hydro_ops/web/daily.py` validates exact CNRFC/UCRB forcing-subset and model
publication receipts. `bin/test_web_daily_coordinator.py` provides separate
`gate` and `run` commands; `slurm/test_web_daily_coordinator.sh` launches them.
The submission helper constructs an `afterok:<gate-job>` dependency with
`--kill-on-invalid-dep=yes`, so a failed gate cannot strand a runnable publisher.
No existing daily-cycle submission scripts or cron entries have been changed.

The gate requires both domains and numeric job IDs, successful and complete
forcing-subset counts, fully published non-test option-1 model runs, matching
model publication transactions, and midnight-bounded model coverage of the entire
requested refresh interval. It verifies every requested daily forcing summary's
subset receipt against current output and parent-source identities, and every
daily model file against its job-specific transaction's recorded output identity.
`COMPLETED`, a submission receipt, or a mutable `latest.json` alone is insufficient.

The gate pins receipt hashes. The coordinator checks those hashes and current file
versions again at startup, after acquiring its shared coordinator lock, throughout
rendering via receipt pins, and after collection assembly. It creates a complete
local collection and a manifest-based deployment **plan only**. Successful
rendering is not a successful web deployment. This test requires
`production_enabled=false` and never invokes the Globus client. Its overall
`coordinator-acceptance.json` is separate from collection acceptance and is written
only after receipt revalidation and deployment planning succeed.

Submission on November 14: gate **5309124** (one CPU, ten minutes) followed by
coordinator **5309125** (eight CPUs, two hours). Request:
`web/work/daily-coordinator-tests/20261114T193237Z/request.json`. It uses completed
forcing jobs 5299614/5299617 and model jobs 5299615/5299620 for CNRFC/UCRB,
respectively. Requested dates are November 10–13, extending the accepted local
November 10–12 test collection. These are existing real published inputs, not
new forcing/model submissions. The fresh gate job permits testing the dependency
chain without relying on SLURM retaining old completed jobs as dependencies.
Result, if accepted:
`web/work/daily-coordinator-tests/20261114T193237Z/run-5309125/coordinator-acceptance.json`.

Twenty-nine focused gate/incremental/publication tests passed, including rejection
of failed/running/mismatched receipts, wrong domains, insufficient model coverage,
changed output identities, and malformed dependencies. Ruff and shell syntax
checks passed. Submission is not test acceptance. The current gate deliberately
limits the requested rendering interval to dates covered by both model receipts;
whole-history incremental operational assembly needs to preserve accepted older
assets separately rather than pretending the latest replay job validates them.
After this chain passes, production integration still needs that historical
retention guard and a validated live-publication bootstrap before enablement.

Both jobs passed: gate 5309124 took three seconds and coordinator 5309125 took
1:24. The coordinator reused 24 PNGs and added eight November 13 PNGs. Its
45-file deployment remained planned only; no remote files were changed.

## Full-history retention during a daily refresh

`src/hydro_ops/web/history.py` merges an accepted short refresh into an accepted
full collection. Logical keys are product, variable, resolution and date; stream
is deliberately excluded so promotion to Retro replaces the NRT selection rather
than creating ambiguous duplicate entries. Retro-to-NRT downgrades are rejected.
All untouched historical assets are retained with their original checksums.
Immutable PNGs are hardlinked locally; metadata is regenerated separately.

Acceptance requires unchanged family grids, legends and display policy, unique
assets, unchanged interval bounds, continuous daily coverage and all complete
calendar months. Source inventories are checked again before writing the final
acceptance marker. Historical assets retain their accepted snapshot provenance;
the latest model-job receipts validate only the supplied refresh interval, not
the entire history. No historical image is claimed to have been scientifically
revalidated by this merge.

The isolated coordinator now plans deployment from `full-publication/`, not its
short `publication/` rendering window. Optional request field `history_base`
selects the full accepted collection separately from the incremental render base.
The gate and post-merge checks still require exact upstream receipt identities.

Full-history test submitted November 14: gate **5309164**, coordinator
**5309165** (eight CPUs, two-hour limit). Request:
`web/work/daily-coordinator-tests/20261114T194244Z/request.json`.
It merges the accepted November 10–13 refresh into full v6 coverage beginning
October 1, 2025. Expected result: 3,324 PNGs, including 16 new November 12–13
images, with no lost historical logical assets. Inspect
`run-5309165/coordinator-acceptance.json` under that request directory for the
actual result; submission alone is not acceptance. Twenty-seven focused tests
passed, including history retention, repeat merges, downgrade/duplicate/gap
rejection, changed grid/legend rejection, receipt gates and deployment planning.
Live transfers remain disabled and cron is unchanged.

The full-history test passed: 5309165 completed in 1:36 with eight CPUs. Each
domain retained 1,646 historical PNGs, refreshed eight existing entries and added
eight new entries, yielding 1,662 images per domain (3,324 total). Coverage is
October 1, 2025–November 13, 2026 with no gaps or lost logical assets. All 32
refresh-window PNGs were reused from the accepted coordinator output; five
refreshed images per domain differ from the older v6 snapshot. The deployment
plan remained local-only.

## One-off initial live deployment

User-authorized job **5309176** uses `bin/test_web_live_bootstrap.py` through
`slurm/test_web_live_bootstrap.sh` (one CPU, two-hour limit). Its request and
one-off enabled configuration are under
`web/work/live-bootstrap/20261114T194752Z/`. The routine
`config/web_globus.json` remains disabled and no cron settings change.

Before transfer the bootstrap verifies legacy v6 public metadata byte-for-byte
against its accepted local snapshot and requires every legacy inventory path to
remain in the new collection. It does not adopt ownership of arbitrary remote
files. This first deployment has no predecessor and **no deletions**. Any
unreferenced legacy files require separate inventory/review before cleanup.

The existing resumable publisher transfers payload first, metadata second and
catalog last, verifying public HTTPS bytes by checksum before accepting the
deployment. A persistent destination writer prevents competing publications.
The 3,337-file collection is approximately 130 MB. The bootstrap uses one HTTP
session for verification; Globus transfers use checksum synchronization.

`deployment.json` records the frozen deployment plan. On interruption, resume
the same request rather than starting a competing deployment. Do not automatically
retry a failed transfer without inspecting its saved state. The final
`acceptance.json` is written only after the publisher reaches `published`; job
submission or Globus transfer completion alone is not acceptance. The accepted
destination inventory then provides ownership tracking for future exact-path
cleanup. Local v6 and the merged collection remain available for recovery.

Twenty-eight focused tests, Ruff, shell syntax and diff checks passed before
submission. Initial live deployment is not yet routine daily-cycle integration.

The initial live deployment **5309176 passed** in 4:17 using one CPU. Payload,
metadata and catalog tasks succeeded, and HTTPS verification accepted all 3,337
files (3,324 PNGs). Deployment `20261114T194907Z-6ecc96573793` is now the accepted
destination inventory. No files were deleted and the destination lock owner was
released. The user independently confirmed November 13 on the web map.

## Daily-cycle integration: plan-only commissioning

`src/hydro_ops/web/schedule.py` is called after the daily NRT controller submits
the CNRFC and UCRB model branches. `config/web_daily.json` defaults to
`enabled=false`, `mode=plan-only`; it is separate from transfer enablement.
There is no additional cron entry. When enabled, the hook submits one eight-CPU
job dependent on successful completion of the controller, both exact forcing
subset jobs and both exact model jobs. Invalid dependencies cancel the job.
No NRTRES reservation is requested by the web launcher.

Both models must have been submitted by that cycle. Disabled/deferred/already
submitted model responses are not replaced by guesses from mutable latest
reports: the web branch is deferred. A locked per-cycle submission record prevents
duplicate jobs; uncertain scheduler submissions require review rather than
automatic resubmission. Web submission failure is recorded without invalidating
successful forcing/model work.

`bin/run_web_daily_plan.py` selects the common complete daily interval from the
two model receipts and takes its full-history base from the accepted live
deployment. It then runs the existing receipt gate, incremental renderer,
history merge and deployment planner. All existing output-identity checks apply.
A missing interval between old and new coverage fails the full-history check;
it is not silently skipped. This commissioning runner never invokes Globus.
Monthly summaries are selected by the existing renderer when complete months
fall within its requested interval; older monthly/Retro revisions still require
an explicitly scoped refresh, not an assumption that the daily replay covers them.

Manual integration test **5309711** uses the real completed 5299614/5299615 and
5299617/5299620 forcing/model pairs. It automatically selected November 3–13 and
validated 28 receipt hashes before submission. Its result belongs under
`web/work/daily-integration/job-5309711/`; the nested
`run-5309711/coordinator-acceptance.json` is the overall success marker.
Forty-four focused tests passed before submission. Routine scheduling and
routine transfers remain disabled pending this integration result.
The full regression suite also passed: 1,050 tests passed and four skipped;
Ruff, shell syntax and diff checks passed.

Integration job **5309711 passed** in 11:08 with eight CPUs. Its November 3–13
refresh reused 40 PNGs and rendered 48 from 24 changed sources; 40 rendered PNGs
differ from the published versions. Both complete families still contain 1,662
images, with no lost logical assets or coverage gaps. Its deployment was planned
only and did not change the web server.

### Live revision commissioning

Job **5309729** tests publishing that accepted daily integration result using
`web/work/live-revision/20261114T203330Z/request.json`. The one-off live runner
now supports an explicit `expected_previous_id` and `coordinator_request` for
revisions. It revalidates the coordinator request hash and upstream receipt pins,
verifies the current public metadata against the preceding accepted collection,
and refuses an unexpected predecessor or any deletion. It retains the existing
payload → metadata → catalog → HTTPS verification sequence and durable destination
ownership. The expected predecessor is `20261114T194907Z-6ecc96573793`.

All 3,324 images remain present; the latest date stays November 13 because this
is a revision, not a new-date extension. Unchanged files use Globus checksum
synchronization. A final `acceptance.json` in the request directory, together with
the matching accepted destination receipt, is required before calling this test
successful. Twenty-five focused tests and Ruff passed before submission; source
receipt identities were also rechecked successfully. The routine daily hook and
routine transfer configuration remain disabled pending this live revision test.

Live revision **5309729 passed** in 3:43 using one CPU. All transfer phases and
HTTPS verification passed; accepted deployment is
`20261114T203425Z-90902b6c538d`. There were no deletions. The public catalog was
rechecked independently and matched the accepted checksum.

## Automatic daily live publication

The existing daily runner now accepts `--mode publish` and completes the accepted
deployment using `src/hydro_ops/web/live_cycle.py`. It checks current configuration
before every transfer step, checks source receipt identities immediately before
transfer, and rejects a plan whose predecessor no longer matches the full history
used for rendering. The shared operational-cycle lock spans selection, rendering
and transfer; the existing durable destination owner also prevents concurrent
writers across asynchronous Globus tasks. Failed/unfinished transfers retain their
plan and block conflicting publications; do not resubmit blindly.

Commissioning job **5309739** runs the entire automatic path against the accepted
November 3–13 source receipts. Transfers are enabled in `config/web_globus.json`,
while `config/web_daily.json` initially remains `enabled=false`, `mode=publish`.
The explicit one-time `--activate-daily-after-success` option sets `enabled=true`
only after live publication and HTTPS verification succeed. It refuses activation
if the scheduling configuration changed during the run. The activation receipt
will be `web/work/daily-integration/job-5309739/activation.json`; live acceptance
will be in its `run-5309739/live-acceptance.json`. Submission is not activation.

Once activated, subsequent daily NRT forcing cycles submit this same runner with
the two domains' exact model/subset dependencies. There is no new cron entry and
no six-hourly web publication hook. Monthly/older retrospective refreshes remain
separate work. To stop new web submissions, set `config/web_daily.json:enabled`
to false. To also stop further transfer-phase advancement, set
`config/web_globus.json:production_enabled` to false. These switches do not cancel
an already submitted Globus task; inspect its saved state before recovery.

The daily hook is fail-closed when either model was not newly submitted by the
same forcing cycle. It records a deferral instead of inferring old job IDs.
Consequently a deferred model cycle also defers the corresponding web refresh.
Local coordinator acceptance is still distinct from `live-acceptance.json`;
only the latter and the matching destination receipt prove web publication.

Validation for the live daily integration: 1,053 tests passed and four skipped;
Ruff and diff checks passed. The old configuration test requiring transfers to
remain permanently disabled was updated to permit explicitly configured publish
mode. Separate tests still enforce disabled/changed configuration rejection,
receipt and predecessor checks, and activation only after successful publication
with an unchanged scheduling configuration. Commissioning 5309739 passed its
28 receipt checks and reused all 88 refresh-window PNGs; final publication and
automatic activation remain contingent on its final acceptance receipts.

Commissioning **5309739 passed** in 4:45 with eight CPUs. All 3,337 public files
passed HTTPS verification, the deployment receipt advanced to
`20261114T205659Z-8e22f516b2cf`, and the destination writer was released. No files
were deleted. Its activation receipt confirms that automatic daily publication
is now enabled (`web_daily.json:enabled=true`, `mode=publish`; transfer enabled).
The daily launcher now uses the neutral job name `web-daily-full-history` rather
than the outdated `plan-only` label. Logs remain `forcing/logs/web-daily-plan-JOBID.out`
for continuity. Monitor the first normally scheduled cycle and require its own
`live-acceptance.json`; the commissioning success does not stand in for it.
