# Seasonal land-state percentile climatology

## Scope and reference period

This is an offline analysis of accepted daily LDASOUT, not a model or forcing
change. Initial implementation is a CNRFC tile benchmark; whole-domain products
must wait for its accuracy, throughput and storage review. The numerical library
is domain-independent; the benchmark deliberately selects CNRFC locations.

Reference centers span **1979-10-01 through 2025-09-30**, inclusive (46 complete
water years, WY1980–WY2025). Daily observations are required from **1979-09-16
through 2025-10-15** to supply 15-day margins. Those extra dates supply samples,
not additional reference centers. Coverage and acceptance are checked against
the canonical archive and monthly model acceptance reports.

Daily input values represent model intervals [D 00 UTC, D+1 00 UTC), reduced
from interval-end records 01–00 UTC and labeled at midday. The benchmark checks
time bounds and `day_definition=model_interval`; it does not shift already
reduced daily fields again. See [time conventions](nwm_time_conventions.md).

## Variables and statistics

| Product | Calculation | Units |
| --- | --- | --- |
| SOIL_WATER | `1000 * sum(SOIL_M * layer_thickness_m)` | mm |
| SNEQV | Daily mean snow water equivalent | kg m-2 (numerically mm water) |

The four soil thicknesses are 0.1, 0.3, 0.6 and 1.0 m. The extraction checks
these against the archived run namelists. One missing/invalid soil layer
invalidates the column; layers must not be summed unweighted. Volumetric soil
water outside [0,1] and negative SWE are excluded. Only active model cells enter
the climatology. Missing observations are excluded, with valid counts retained.

For each month/day, pool the **individual daily values** in the ±15-day window
around every corresponding reference date. Do not first average each window.
Each directly estimated calendar date has 46 × 31 = **1,426 potential samples**;
these overlapping samples are not statistically independent.

Calculate all 100 quantiles (0.5, 1.5, …, 99.5 percent) together using exact
Hyndman–Fan type 8 (`numpy.nanpercentile(method='median_unbiased')`). This is
distinct from histogram-based CDO estimates. Store float32 thresholds and int32
valid counts. The prototype permits any positive valid sample count and exposes
counts for review; a production completeness cutoff has not yet been selected.

Calendar keys use the 366 dates of a leap-year template, never unadjusted Julian
day numbers from different years. February 29 has no direct reference centers:
its thresholds are the arithmetic means of February 28 and March 1 thresholds.
Its count is -1 (interpolated, not an empirical sample count). Actual February 29
observations remain in neighboring chronological 31-day windows.

## Percentile-rank maps

Invert the threshold table by piecewise-linear interpolation. Repeated threshold
values use the mean of their table percentile levels. Values outside the table
range are clipped to 0.5 and 99.5: these are censored tails, not exact ranks of
zero or 100. This approximation is not an exact empirical CDF, particularly for
distributions with many tied values.

Keep zeros in the SWE reference distribution, but report SWE ranks only where
the current SWE **exceeds** a configurable threshold (initially 1 mm). At or
below that threshold the rank is missing. This avoids summer zero-snow rank maps
without changing the climatology to a snow-only conditional distribution.

## Tiled benchmark and execution

Implementation:

- `src/hydro_ops/wrf_hydro/climatology.py`: calendar windows, weighted soil
  storage, exact quantiles, rank inversion and indexed tile reassembly.
- `bin/benchmark_nwm_climatology.py`: audited CNRFC extraction and benchmark.
- `slurm/benchmark_nwm_climatology.sh`: compute-node launcher, 32 allocated CPUs,
  eight extraction processes, 120 GB requested scratch, 12-hour limit.
- `tests/test_nwm_climatology.py`: boundary/leap-day, weighting, missing values,
  quantiles, ranks and tile-reassembly tests.

Run from the project root after inspecting active jobs:

```bash
source bin/project_environment.sh
# Small synthetic numerical and projected-grid interoperability check:
sbatch --cpus-per-task=4 --time=00:30:00 \
  slurm/benchmark_nwm_climatology.sh --preflight-only
# Full-reference benchmark: three 16-by-16 tiles, not the whole domain:
sbatch slurm/benchmark_nwm_climatology.sh
```

Tiles sample the Sierra Nevada, northern coast and desert. Source LDASOUT chunks
are large, so extraction reads all three tiles while each daily file is open;
years run in parallel. Compact histories go to node scratch. Quantiles are then
calculated from those histories, avoiding 100 separate source-archive passes.
Do not extrapolate tiny-tile read time linearly to full-domain production: chunk
decompression, archive reads, sorting and output size must be assessed separately.

Checks include an independent sorted-sample quantile oracle, monotonicity,
source identity stability, copy checksums, indexed tile reassembly, a projected
CDO `distgrid,2,2` / `collgrid,2` round trip, and one-pixel CDO p50 comparisons
with `pm=r8` and 10,001 histogram bins. `collgrid` needs the tile-column count
explicitly for the curvilinear representation. CDO equivalence is a test target,
not assumed; a failure blocks benchmark acceptance.

Artifacts remain isolated and non-operational:

```text
nwm/status/cnrfc/climatology/benchmark-JOB/
  plan.json, status.json, sources-YEAR.json
nwm/analysis/cnrfc/retro/climatology/wy1980-2025/benchmark-JOB/TILE/
  history.nc, percentiles.nc, rank-example.npz, cdo.log
```

The rank example uses 2025-04-01. Inspect the job-specific `status.json`, not
SLURM completion alone. No source outputs, restarts or operational settings are
modified. Benchmark artifacts are not yet full-domain climatology products.

After acceptance, choose extraction batching/tile size and estimate full-domain
storage, implement restartable tile publication and a coordinate-aware NetCDF
rank-map interface, then run CNRFC before other domains. Preserve the existing
model-history limitations rather than implying that this analysis recalibrates
the model or removes historical restart effects.

## Initial validation campaign

- Synthetic preflight 5070440 exposed the missing CDO tile-column argument;
  no production data were changed.
- Corrected preflight **5070473 passed** in about 20 seconds. Both projected
  CDO and indexed tile round trips were exact; CDO p50 and exact type-8 p50
  agreed for both synthetic fields.
- Full-reference three-tile benchmark **5070489** was submitted and observed
  running with 32 CPUs and 62.5 GiB allocated memory, eight extraction workers
  and 120,000 MB requested scratch. Its first 107-day extraction (1979 padding
  and reference dates) completed in 56 seconds. This is an early phase timing,
  not a whole-domain or complete-benchmark throughput estimate. Consult its
  job-specific report for subsequent acceptance and timings.

Full-reference benchmark 5070489 subsequently **passed** in 22 minutes 14
seconds: 1,229 seconds extraction and approximately 32–34 seconds per tile for
calculation, writing and checks. Both fields matched the sampled exact oracle
and CDO medians, with exact tile reassembly. This validates the method on small
tiles, not whole-domain throughput.

### Larger-tile scaling campaign

The CNRFC rectangle is 1,343 rows by 857 columns. A 128-by-128 partition needs
77 tiles including partial edges; 160-by-160 needs 54. These are more realistic
production candidates than the initial 16-by-16 correctness samples.

The benchmark now supports tile sizes through 160 and materializes only one
tile's full history at a time, using memory-mapped annual intermediates. For a
160-by-160 tile, the two-field float32 history is approximately 3.2 GiB and the
two 366-by-100 threshold arrays total approximately 7.0 GiB, before calculation
temporaries. The 32-CPU allocation provides approximately 62.5 GiB memory on
this cluster; larger runs require at least 60 GiB free scratch at startup and
request 120,000 MB. Quantile/oracle and history assembly/write timings are
reported separately. Actual peak memory and throughput still need measurement.

Submitted sequentially to avoid competing benchmark I/O:

| Job | Scope | Dependency |
| --- | --- | --- |
| 5076798 | Synthetic preflight, default 16-cell tile | None |
| 5076799 | Three 128-by-128 tiles, full reference | Successful preflight |
| 5076800 | Three 160-by-160 tiles, full reference | Successful 128-cell benchmark |

Both large benchmarks use eight extraction workers and 32 allocated CPUs.
They are isolated analysis jobs, not a full-domain publication campaign. Review
job-specific reports before selecting the production tile size and concurrency.

### Single-pass history extraction

`bin/benchmark_nwm_history_stream.py` tests reading each source day once for
the entire CNRFC grid and buffering the derived SOIL_WATER/SNEQV fields before
distributing them to all active tiles. `slurm/benchmark_nwm_history_stream.sh`
requests eight CPUs (approximately 15.6 GiB memory), 120,000 MB scratch and four
hours. Extraction is currently a single process; CPU allocation supplies memory,
not eight concurrent percentile calculations.

The default test covers all 366 days of 1980, uses 128-by-128 tiles and 31-day
buffers, with time chunks matching the buffer. Each retained tile has **one
NetCDF file for the entire requested period**, containing both fields, coordinates
and time bounds. Entirely inactive tiles are skipped and partial edge tiles are
supported. There are no new daily or annual intermediate files. A full-reference
run would likewise produce one file per tile, but needs separately sized scratch
and runtime; the present request is for the one-year benchmark only.

All writes occur on node scratch. After files close, the writer checks first,
middle and last dates across every tile against independently weighted source
values, verifies time axes and unchanged source identities, then publishes using
checksum-verified temporary copies. `status=passed` in the job-specific report is
the acceptance gate; files alone are not sufficient. The extraction pass reads
each source once; sampled verification deliberately rereads three source dates.

```bash
source bin/project_environment.sh
sbatch slurm/benchmark_nwm_history_stream.sh
```

Reports: `nwm/status/cnrfc/climatology/history-stream-JOB/`.
Histories: `nwm/analysis/cnrfc/retro/climatology/history-stream-JOB/tile-NNN.nc`.
Timing separates input reading/derivation from buffered writing; total includes
validation and publication. These isolated tests do not launch percentile jobs
or replace existing climatology products. Preflight job 5077262 checks all active
tiles over three days with a two-day buffer, exercising the final partial block;
the full-year test is conditioned on its success.

Preflight 5077262 passed in 3.3 seconds of application time (five seconds SLURM
elapsed), with 354 sampled field comparisons across 59 active tiles. The other
18 rectangular tiles contain no active model cells and were omitted. Full-year
job **5077263** then started; its results are pending. The preflight's three-day
timing is not a full-history throughput estimate.

### Full-reference extraction and tile array

The 1980 streaming test 5077263 **passed**: 366 days, 59 retained tiles,
120.5 seconds application time, 81.5 seconds reading/derivation and 27.7 seconds
buffered writing. Peak application RSS was 1,073 MiB; the 59 compressed history
files totaled 903,708,759 bytes. All 354 sampled comparisons passed.

With user approval, the superseded benchmark jobs 5076799 (running) and 5076800
(pending) were cancelled; existing benchmark artifacts were retained. The new
campaign is:

| Job | Work | Resources / dependency |
| --- | --- | --- |
| 5077267 | All active tile histories, 1979-09-16–2025-10-15 | 8 CPUs, 16,000 MiB RAM, 250,000 MB scratch, 6 hours |
| 5077268 | 59 percentile array tasks, indexes 0–58 | After successful extraction; at most 32 concurrent; 12 CPUs and 24,000 MiB RAM per task; 4 hours |
| 5077269 | Complete tile-publication audit | After successful whole array; 2 CPUs, 2 hours |

The percentile array reserves at most **384 CPUs** concurrently. Its current
numerical calculation remains single-process per tile; extra allocated CPUs
provide the memory allowance. Do not describe this as 384 simultaneous numerical
workers. Each task stages only its accepted history onto node scratch, verifies
the copy checksum, computes all quantiles for each variable, checks counts,
monotonicity and leap-day interpolation, compares selected known-valid cells to
the sorted-sample oracle, and verifies every written threshold before atomic
publication. CDO comparisons already passed in the benchmark; they are not
repeated across the whole production array.

Array entry points are `bin/produce_nwm_climatology_tile.py` and
`slurm/produce_nwm_climatology_tiles.sh`. Unique campaign/task paths and exclusive
task claim files prevent duplicate publication. A failed task needs review before
retry; claims are deliberately not silently cleared. The final audit uses
`bin/audit_nwm_climatology_tiles.py` to verify the accepted history's tile plan,
all task reports, current mask checksum and all published product checksums.

```text
nwm/status/cnrfc/climatology/tiles-5077268/
  task-NNN.json, task-NNN.claim, acceptance.json
nwm/analysis/cnrfc/retro/climatology/wy1980-2025/tiles-5077268/
  tile-NNN/percentiles.nc
```

Tile IDs are original rectangular partition IDs, so they are not contiguous
after empty tiles are omitted. Array task indexes refer to the accepted history
artifact list, not directly to tile IDs. Full-domain acceptance requires the
final `acceptance.json` to pass, not merely the presence of tile files. No global
NetCDF recombination or operational rank-map publication is performed yet.
Histories are retained for reuse and potential retries.

The full campaign passed: extraction took 2:21:46, the 59-task array spanned
1:17:12, and final audit 5077269 took 1:51. All 59 tile reports passed, covering
681,063 active cells. Histories total 37.52 GB; percentile tables total 85.84 GB
(decimal). Maximum tile application RSS was approximately 9.8 GiB.

## Full-domain visual previews

`bin/map_nwm_climatology.py` reads the accepted tile manifest, assembles daily
rank maps at original grid indices, and checks that all eligible active cells
have ranks. It reads only the requested calendar-date thresholds, not entire
tile climatologies. Tile coordinates, window ownership, source daily bounds,
month acceptance and mask checksum are verified. The prior full-publication
audit supplies checksums; this plotting step checks file identity stability
during reading rather than rehashing the entire 85.84 GB archive.

`slurm/map_nwm_climatology.sh` uses the site's existing base Python for
Matplotlib/GeoPandas, as the domain-building tools do; no plotting package is
added to the operational environment. Each task requests four CPUs and runs on
a compute node. Submit `--array=0-2%3` for the default three dates: 2015-04-01,
2023-04-01 and 2025-09-30. These are in-reference-period illustrations, not
independent validation. Preview array **5079474** was submitted for these dates.

Each date produces a four-panel PNG (soil-water amount/rank and SWE amount/rank)
and a full-grid NetCDF containing the two daily mean amounts, two percentile
rank fields, valid-sample counts, coordinates and time bounds. SWE ranks at or
below 1 mm are missing; zero SWE amounts remain visible. Amount scales are fixed
across dates (soil water 0–1,000 mm; SWE 0–1,500 mm, with saturated upper colors),
and ranks use the same brown-to-green scale. Outlined reporting boundaries do
not clip the active buffer cells.

```text
nwm/analysis/cnrfc/retro/climatology/wy1980-2025/maps-JOB/
  YYYYMMDD.land_percentiles.png
  YYYYMMDD.land_percentiles.nc
nwm/status/cnrfc/climatology/maps-JOB/YYYYMMDD.json
```

These are visual-review products, not a routine operational publication. High
or low ranks describe this model's own seasonal distribution, not an observed
soil-moisture or snow anomaly. Preview NetCDF fields must not be used as model
forcing. A prefix-sum calculation for tied threshold levels accelerates table
inversion without changing its midrank definition.

Preview array 5079474 passed for all three dates in 56–57 seconds per task.
Each map contains soil-water ranks for all 681,063 active cells. SWE above 1 mm
occurred at 272 cells on 2015-04-01, 119,915 on 2023-04-01 and 1,910 on
2025-09-30; other SWE ranks are intentionally missing, not failed extraction.
These are model-output counts, not observed snow-cover assessments. PNGs and
NetCDFs are published in `maps-5079474/`; April previews were visually inspected
for layout, orientation and consistent scales. This is not an independent
scientific validation of the underlying model history.

### Precipitation-quality issue exposed by the April 2015 map

User review identified circular wet anomalies in the 2015-04-01 soil-water
amounts and ranks. Read-only diagnostic job **5079488** compared the published
CNRFC hourly precipitation with native PRISM over 182 production constraint
windows (2014-10-01 through 2015-03-31). It used the original masked conservative
NWM-to-PRISM weights, retained only targets with full contributing-cell coverage,
and followed the pipeline's previous-day 12 through labeled-day 11 UTC grouping.
The initial attempt 5079487 stopped on a coordinate assertion because weight
longitudes were 0–360; the successful diagnostic normalized them to -180–180.

Across 42,423 full-period comparison cells, mean accumulated forcing was
**35.26 mm versus 266.37 mm PRISM**. Of these cells, 41,662 had at least one
window with forcing below 0.01 mm and PRISM above 1 mm. The forcing/PRISM ratio
map reproduces the circular features; the PRISM target totals do not. Artifacts:

```text
nwm/analysis/cnrfc/retro/climatology/prism-diagnosis-5079488/
  precipitation_comparison.png, diagnostics.npz, status.json
```

Code inspection explains how such deficits can survive the daily constraint:
`reconcile_prism_period` excludes baseline-dry/PRISM-wet and infeasible
scaling-cap targets from its eligible convergence set. The daily acceptance
gate counts NOT_CONVERGED flags rather than all unmet PRISM targets. A two-cell
identity-weight diagnostic with baseline [0,1] mm and PRISM [10,20] mm left
corrected totals [0,1] mm but reported convergence, with dry/cap flags. Stage-IV
zero values are eligible in source selection and thus do not trigger NLDAS
fallback. A sampled 2014-10-15 hour selected Stage-IV archive at all 691,832
retained CNRFC forcing cells. Its file marked reconciliation accepted despite
non-convergence metadata.

This establishes a published-forcing mismatch and an acceptance-policy gap,
not merely hourly redistribution or a percentile plotting artifact. The exact
origin of the upstream circular precipitation support (native Stage-IV versus
preparation/remapping) and its temporal/domain extent remain to be audited.
The percentile numerical checks remain valid but do **not** validate the model
history's meteorological inputs. Do not use the affected 2015 preview as a
scientifically validated drought product. No production forcing, model output,
restart or reconciliation policy was changed by this investigation. Repair,
acceptance tightening, affected-period inventory and model reruns require a
separate tested implementation plan.

### Extent screen: 1981–2025

Read-only year-sharded audits **5079493** (2012–2016) and **5079498** (remaining
years) completed all 540 months across 45 years. Summary job **5079515** passed.
The screen uses CNRFC monthly RAINRATE means multiplied by the exact month
duration, then adds/subtracts boundary hours to match the production PRISM
windows before conservative aggregation. PRISM targets are sums of the daily
files, not a separately downloaded monthly climate product. The six-month
2014-10 through 2015-03 totals reproduce the preceding direct-hourly audit.

Annual regional forcing/PRISM ratios across full-contributing-area comparison
cells were:

| Period | Range of annual ratios | Interpretation |
| --- | --- | --- |
| 1981–2001 | 0.872–0.955 | Smaller but nonzero discrepancies; not exact agreement |
| 2002–2010 | 0.210–0.665 | Substantial deficits throughout this period |
| 2011–2015 | 0.112–0.165 | Most severe multi-year deficit |
| 2016–2025 | 0.885–0.962 | Sharp improvement, but residual discrepancies persist |

Visual inspection of 2002 and 2011 ratio maps confirms circular/sector-shaped
features outside the originally noticed 2013–2015 period. Do not interpret all
years outside 2002–2015 as scientifically validated: these aggregate ratios do
not establish every daily/cell constraint or rule out smaller spatial errors.
The initial 2002 decline coincides with Stage-IV entering the historical blend;
this association does not establish whether the native NOAA fields or their
preparation introduced the coverage pattern. Geographic scope is **CNRFC**, not
a national audit. 1979–1980 monthly-constraint years and 2026 were not included.

Results and monthly/annual CSVs:

```text
nwm/analysis/cnrfc/retro/climatology/prism-extent-5079493/
nwm/analysis/cnrfc/retro/climatology/prism-extent-5079498/
nwm/analysis/cnrfc/retro/climatology/prism-extent-summary-5079493-5079498/
  monthly_ratio_timeline.png, monthly.csv, annual.csv, status.json
```

Reproducible diagnostic entry points are `bin/audit_cnrfc_prism_extent.py` and
`bin/summarize_cnrfc_prism_extent.py`, with matching SLURM launchers. Array index
zero corresponds to 1981. No forcing, model outputs or accepted climatology
tables were modified. The tables remain numerically accepted but their
scientific interpretation is limited by the affected forcing/model history.

### Experimental daily-constraint safeguards

`src/hydro_ops/forcing/prism_safeguards.py` is an isolated prototype, **not an
operational replacement**. It separates the prescribed spatial daily depth
from its hourly distribution:

1. Use the complete local baseline profile when it has meaningful precipitation
   and does not require extreme amplification.
2. Otherwise use the complete local NLDAS-2 hourly fractions, on the same grid
   and aligned to the same 24-hour PRISM window. NLDAS supplies timing, not a
   replacement for the PRISM total.
3. If that profile is also zero, near-zero or incomplete, distribute the
   prescribed total uniformly over all 24 hours and record synthetic timing.
4. Audit the reconstructed hourly sum against **every finite in-domain PRISM
   target**, including capped, dry/wet, unmapped and partially supported cells.
   Missing support must not be hidden by conservative-weight renormalization.
   Failure means no publication, not exclusion from the convergence denominator.

Prototype timing thresholds are 0.1 mm per window and a baseline amplification
factor of 10; these are testing values, not scientifically calibrated defaults.
The ratio threshold changes timing selection, never the required total.
Prototype acceptance uses absolute error <= 0.01 mm + 0.001 times target depth.
Missing PRISM targets remain explicitly unconstrained; a window with no targets
is not accepted as PRISM constrained. Provenance distinguishes baseline, NLDAS,
uniform, dry and unconstrained cells.

This helper does not yet solve the full overlapping-grid spatial problem.
Next integration must provide a nonnegative daily-field solution with support
for initially dry cells, then test the published float32/reassembled hours.
Some overlapping constraints may be infeasible: report/fail these explicitly,
never claim unconditional convergence. Extreme totals need review rather than
a silent cap. NLDAS-unavailable NRT operation needs an explicitly approved
timing fallback; the current prototype only addresses the historical case.
Calendar-day publication remains 00–23 UTC; neither the PRISM window alignment
nor the existing summary time conventions change.

Focused tests cover local timing, zero/near-zero baselines, extreme ratios,
missing hourly/spatial support and the former cap-exemption acceptance gap.
Original production reconciliation behavior is unchanged while this is tested.
`bin/diagnose_stage4_native.py` and its SLURM launcher compare archived hourly
GRIB1 values against converted NetCDF and against native six-hour fields for
sample dates across the affected and improved periods. Its regional screen is
a California bounding box, not the exact CNRFC polygon. Native GRIB forecast
range labels must be inspected separately from filename interval-end labels.

Native-source diagnostic array **5080105** completed all five sample dates
(December 11 in 2002, 2011, 2014, 2015 and 2016). All 120 hourly fields matched
the converted NetCDF values exactly, with zero missing-mask differences.
Results are in `forcing/status/stage4-native-5080105/`. Initial array 5080093
encountered the diagnostic reader's overly restrictive six-hour GRIB range
check; the independent diagnostic decoder now retains the native values without
assuming the accumulation starts at forecast hour zero. Production conversion
code was not changed.

For the California bounding box and common valid cells, the 2014-12-11
06/12/18 UTC six-hour fields averaged 1.726/1.859/4.031 mm, versus
0.100/0.116/0.540 mm in summed native hourly fields. The 2015-12-11 comparison
also showed large deficits (0.130/0.166/0.118 versus 1.003/0.967/0.716 mm).
The 2016 sample was substantially closer. The 2011 sample was almost dry and
is not informative about wet-weather coverage. This rules out value/missing
mask corruption in hourly NetCDF conversion for these samples; it does not
by itself verify geolocation, all historical days, or establish six-hour fields
as a fully audited replacement. The sampled native-product disagreement and
the independent PRISM acceptance gap are distinct findings.

### Real-data fallback experiment

Array **5081220** tests 2014-12-11, 2015-04-01 and 2016-12-11 concurrently,
using 12 allocated CPUs / 24,000 MB per task on shared-128, with no reservation.
The solver is single-threaded; CPUs provide memory rather than an asserted
12-way speedup. Entry points are `bin/test_prism_fallback_strategy.py` and
`slurm/test_prism_fallback_strategy.sh`. Fifteen focused safeguard/reconciliation
tests passed before submission, including overlapping feasible and incompatible
spatial constraints.

This is a **diagnostic**, not a baseline rebuild or production publication:

- Existing retro hourly precipitation supplies the original local timing;
  NLDAS nearest-grid-cell profiles supply fallback timing only. No NLDAS total
  is substituted for PRISM. This nearest-cell timing implementation is not a
  change to operational remapping.
- A PRISM-grid control isolates temporal allocation from spatial fitting.
- An experimental nonnegative multiplicative sparse fit seeds the NWM daily
  field from PRISM, not the deficient baseline. It removes baseline-relative
  amplitude caps but retains a finite iteration limit; incompatible constraints
  may be rejected, and extreme values still require scientific review.
- Comparisons cover only PRISM cells with full CNRFC contributing support and
  complete existing hourly values. Counts of excluded incomplete-support cells
  are reported; this is not a test of filling the entire domain.
- Candidate rain rates are written as float32, reread, and independently checked
  against all targets in that declared scope. No solver eligibility exemptions
  are allowed. Successful execution and scientific acceptance are separate.

Artifacts are in `forcing/work/prism-fallback-tests/5081220/`: date-specific
JSON reports, residual NPZs and `.diagnostic-prism-window.nc` candidates. These
24-hour **PRISM-window diagnostic files must never be used as calendar-day
production forcing**. Calendar-day reassembly, operational source rebuild,
full-domain support and runtime benchmarking at production scale remain further
acceptance steps. No production forcing or model results are replaced.

All three tasks completed and passed the float32 read-back constraint audit:

| PRISM label date | Failed targets before | Failed targets after | Fit iterations | Total diagnostic seconds |
| --- | ---: | ---: | ---: | ---: |
| 2014-12-11 | 12,748 | 0 | 33 | 6.47 |
| 2015-04-01 | 3,508 | 0 | 8 | 6.43 |
| 2016-12-11 | 1,500 | 0 | 25 | 9.20 |

Each date tested 42,423 targets and excluded 16,365 otherwise spatially complete
targets with missing contributing hourly support (including outside the retained
forcing footprint). The spatial solver took 0.14–0.40 seconds and its maximum
absolute residual was below 0.005 mm. Acceptance remained 0.01 mm + 0.1% after
writing float32 rates. The PRISM-grid temporal control conserved totals to
approximately 3e-14 mm. NLDAS and uniform fallbacks were both exercised by real
data, not only synthetic tests. These runtimes are **not** full daily production
costs: there was no baseline regeneration, other-variable processing, operational
remapping or calendar-day reassembly.

The experimental spatial fit uses a PRISM-seeded field rather than preserving
the original sub-PRISM spatial pattern. Numerical closure therefore does not
establish that its fine-scale spatial texture or synthetic hourly timing is
scientifically preferable. Before adoption, inspect maps, quantify uniform
timing, address complete-domain support, compare against a baseline-pattern-
preserving alternative, and test calendar-day assembly over adjacent windows.

### Calendar assembly acceptance test

`bin/test_prism_calendar_strategy.py` and its SLURM launcher exercise the existing
`materialize_calendar_forcing.py` operational assembler without changing it.
Array **5081223** generates three accepted candidate windows per block, starting
2014-12-10, 2015-03-31, 2016-12-10 and 2014-12-31. Each block makes two complete
00–23 UTC collections, covering ordinary, month-end and year-end transitions.
Twelve CPUs / 24 GB are allocated per task, with four concurrent tasks; computation
remains single-threaded. Ten focused safeguard/handoff tests passed before the
real-data results were assessed.

Acceptance checks include exact timestamp sequences, unchanged float32 rainfall
and missing masks, and exact per-hour fallback provenance. Timing provenance is
expanded from a window-static field to a time-dependent field before assembly,
so noon's policy change cannot be lost. The test reconstructs the middle PRISM
window using only the two written calendar files and applies the strict target
audit again. It also verifies that operational time discovery rejects duplicate
records and absent boundary hours. Files remain rainfall-only diagnostics,
**not model-ready LDASIN products**, despite exercising the production filename
and calendar layout. No source production archive is modified.

Date-specific reports are under
`forcing/work/prism-fallback-tests/5081223/calendar-<start>/acceptance.json`;
the corresponding `calendar/YYYY/MM/` directory contains the test collections.
The experiment retains the preceding complete-support comparison scope and
does not validate other forcing variables, full-domain coverage or spatial texture.

Initial array 5081223 stopped safely: three blocks encountered differing time
epochs in the diagnostic inputs (the operational assembler requires identical
units), and the 2016-12-10 window failed one of 42,423 constraints after 2,000
iterations (target 0.612 mm, residual -0.01433 mm). No acceptance tolerance was
relaxed. Diagnostic staging now normalizes times to hours since 1970-01-01 and
has a regression test for both timestamp preservation and time-dependent timing
provenance. Retry **5081227** allows up to 10,000 spatial iterations and keeps the
same strict post-write gate. Its artifacts use the same paths with campaign ID
5081227. These changes affect only the experimental test drivers, not production
reconciliation or calendar assembly.

Retry 5081227 completed three blocks successfully: 2014-12-10/11,
2015-03-31/04-01 and 2014-12-31/2015-01-01. All six calendar files had exact
00–23 UTC timestamps, identical selected rainfall values/masks and hourly timing
provenance. Each reconstructed middle PRISM window passed all 42,423 targets;
duplicate and missing-hour injections were rejected. Pairwise assembly took
11.02, 7.82 and 8.47 seconds respectively. The requested compressed-chunk fast
path fell back to the normal writer because staging used incompatible chunks;
these results validate correctness, not optimized chunk-copy performance.

The 2016 block **remains blocked before assembly**: 2016-12-10 still failed one
target after 10,000 iterations, and its strict gate correctly prevented assembly.
Increasing the iteration budget alone is not an adequate general solution.
Investigate that cell's overlapping weights and neighboring constraints, and
test a more robust local spatial solve before any operational adoption. Do not
relax acceptance or label this four-block campaign fully successful. Existing
production data were untouched; the three successful blocks establish calendar
assembly correctness within the diagnostic scope, not universal solver closure.

### Local constrained fallback experiment

Job **5081232** tests `repair_local_fit` on the blocked 2016-12-10 window and
then reruns the three-window/calendar test. This is an opt-in diagnostic only
(`--local-fallback`), not a production change. It returns to the 2,000-iteration
fast fit rather than spending 10,000 iterations on a stalled solution.

The fallback selects sources of failed PRISM targets and their immediate target
neighbors, fixes all exterior depths, and constrains every finite target touched
by the adjustable sources. A sparse linear program minimizes total absolute
change subject to nonnegative source depths and a half-tolerance safety margin
(0.005 mm + 0.05% of target). All required targets are rechecked afterward and
hourly float32 output retains the original 0.01 mm + 0.1% gate. A 10,000-source
neighborhood cap and 60-second solver limit bound the fallback; failures retain
the original field and are not silently accepted. A local infeasibility result
does not prove global infeasibility because exterior depths were held fixed.

Focused tests cover reviving zeros without altering unrelated cells and refusing
incompatible neighboring constraints. Ten safeguard/staging tests passed before
submission. The SLURM entry point is `slurm/test_prism_local_fallback.sh`, using
12 CPUs / 24 GB and ordinary shared-128 resources. Artifacts and acceptance
reports are under `forcing/work/prism-fallback-tests/5081232/`.

Job 5081232's 36-source/9-target neighborhood could not satisfy the extra
half-tolerance safety margin. Follow-up 5081233 added a diagnostic minimax LP:
the smallest possible normalized error in that neighborhood is **0.9380 times
the original acceptance tolerance**. Thus this local problem is feasible under
the existing acceptance policy, but not under the stricter solver margin. This
is not evidence that the original publication tolerance must be relaxed.

The 5081233 expanded-neighborhood solve (502 sources, 46 targets, 99% safety
margin) succeeded in about 0.44 seconds including diagnostics, with a maximum
source-depth change of 0.003923 mm and no failed targets after float32 hourly
write/read. Follow-up **5081234** tests the smaller neighborhood at the 99% margin
first, expanding only if that fails. Operational acceptance remains unchanged;
minimax solutions are diagnostics, never silently adopted at a larger tolerance.
An added unit test covers infeasibility at the half-margin despite feasibility
inside the original tolerance. These remain opt-in experimental changes.

Final test **5081234 passed**. The smaller 36-source/9-target neighborhood alone
was sufficient at the 99% safety margin; no expansion was needed. Fallback time
was 0.377 seconds, maximum source-depth change 0.003923 mm, and all 42,423 targets
passed the corrected 2016-12-10 float32 window audit. Both 2016-12-10/11 calendar
files preserved exact hourly values/masks/provenance and 00–23 UTC timestamps.
The reconstructed complete middle window (2016-12-11) passed every target, and
duplicate/missing-hour cases were rejected. Calendar assembly took 10.62 seconds;
the complete three-window experiment took 73.94 seconds. Nineteen focused tests
and Ruff passed. This closes the formerly blocked calendar test, not full-domain
or scientific acceptance of the new precipitation method. Production is unchanged.

### Stratified regression campaign

Array **5081235** expands testing to 28 disjoint three-window blocks (84 PRISM
windows and 56 expected calendar files). Starts are January/April/July/October
15 in 1981, 2002, 2011, 2014, 2016 and 2023, plus 2000-02-28, 2012-02-28,
2015-12-31 and 2020-06-30. These are calendar/source-era strata, not a claim
that each chosen date represents a verified extreme storm. They cover the
pre-Stage-IV era, affected historical periods, modern inputs, leap days and
calendar/source-policy transitions. All input paths were checked before submission.

Each block uses the same opt-in fallback and existing calendar assembler with
unchanged acceptance tolerances. Four concurrent tasks each reserve 12 CPUs /
24 GB on shared-128 (48 allocated CPUs total), with single-threaded numerical
processing and a 30-minute task limit. No production data are modified.

`bin/test_prism_strategy_campaign.py` and
`slurm/test_prism_strategy_campaign.sh` define and run the fixed date matrix.
Thirteen focused campaign/safeguard/staging tests and Ruff passed before launch;
tests explicitly ensure the date matrix has no overlapping file writers and
that absent reports cannot pass acceptance. Job **5081240** runs the campaign
audit with `afterany:5081235`, so failed tasks do not suppress the final report.
Per-block execution codes, window acceptance, fallback use and calendar acceptance
are recorded separately under `forcing/work/prism-fallback-tests/5081235/`.
The final machine-readable report is `campaign-summary.json`; its status is
`needs_review` unless every expected block has both acceptance and a zero exit
code. Missing windows after an earlier rejection are reported as missing, not
counted as tested failures or inferred successes. Submission is not acceptance.

### Stratified results and geometric infeasibility

Campaign 5081235 finished with 26/28 accepted blocks (52 calendar files),
80 accepted windows, two rejected windows and two unattempted windows following
early rejection. Two successful local fallbacks took 0.33–0.38 seconds. Successful
blocks took 36–62 seconds; pairwise assembly took 7–14 seconds. Audit 5081240
deliberately returned nonzero with `needs_review`; it did not crash.

Read-only geometry job **5081268**, run by
`bin/inspect_prism_spatial_conflicts.py`, inspected the rejected targets and solved
each neighboring pair independently, with *all* their contributing source depths
free and nonnegative. Results/maps are in
`forcing/work/prism-spatial-conflicts/5081268/geometry.json` and date-named PNGs.

| Date | Conflicting sliver target | Center (lat, lon) | Covered area fraction | Contributing NWM cells | Active cells |
| --- | ---: | --- | ---: | ---: | ---: |
| 2002-01-17 | 344359 | 34.291667, -119.416667 | 1.0930% | 2 | 0 |
| 2016-04-15 | 470737 | 38.041667, -122.416667 | 0.08905% | 1 | 0 |

The 2002 sliver target is 0.333 mm, conflicting with neighboring target 345764
(0.007 mm, 91.01% coverage, 24 contributing cells including 12 active). The
2016 sliver target is 0.345 mm, conflicting with target 470738 (zero rain,
35.71% coverage, nine inactive contributors). In the latter pair, the sliver
forces its sole source to approximately 0.345 mm, while that same source has
weight 0.1721 in the nominally dry neighbor. Nonnegative values at other sources
cannot cancel this contribution.

These two-target LPs already require minimum normalized errors of **1.0118**
and **5.0404** times the original tolerance, respectively. Unlike the earlier
fixed-exterior local LPs, this establishes pairwise infeasibility under the
current weights/nonnegativity/tolerance: enlarging the neighborhood cannot
remove it. These are small support/representation conflicts, distinct from the
large historical precipitation deficits.

Important terminology correction: earlier `complete-support` tests meant all
links of the *masked* global operator were available in the CNRFC subset and
finite. They did **not** mean the full geographic PRISM cell was represented.
`dst_grid_frac` reveals tiny covered fractions even though normalized link weights
sum to one. Treating such slivers as full-cell hard constraints is problematic.

Recommended next experiment, **not adopted policy**: classify targets with no
active NWM contributors and less than 5% covered area as weak-support targets.
Do not impose exact PRISM closure there; retain them as separately reported soft
targets with residuals and support metrics. Preserve the strict audit for normal
targets and do not waive active-cell constraints. The 5% threshold is provisional
and must be inventoried/tested before adoption. This changes constraint support,
not forcing/model masks: do not delete water pixels, drop their forcing values,
or remove possible future lake-model coverage. Weak-support values still need an
explicit spatial estimation policy. Acceptance reports must distinguish constrained
and weak-support targets, never silently label waived targets as matched.

### Experimental weak-support rule and regression

The opt-in `--weak-support` experiment implements the provisional rule without
changing production settings or masks. Among otherwise eligible complete-link
targets, `dst_grid_frac < 0.05` with **zero active NWM contributors** becomes
diagnostic-only rather than a hard equality constraint. Unknown/zero support
fractions are not silently classified as weak. Any active contributor, however
small its weight, prevents this exemption; exactly 5% remains hard-constrained.

These are reported weak targets, not a weighted penalty in the optimizer.
For their spatial estimates, sources shared with hard targets follow the hard
fit; sources not constrained by any hard target retain their original hourly
values exactly. An explicit finite-mask check prevents creating/removing forcing
coverage. This preserves inactive water pixels rather than masking them away.
Weak target indices, targets, coverage fractions and residuals are saved in each
window report; calendar reports recompute weak residuals from assembled files.
Only hard targets participate in the pass/fail count, clearly distinguished from
the original eligible count. Diagnostic files carry a support-policy attribute.

Array **5081269** reruns the same 28 blocks / 84 windows using four concurrent
12-CPU tasks (48 CPUs total), with `--weak-support`. Fifteen focused tests and
Ruff passed before launch, including threshold-boundary and active-contributor
protection tests. Audit **5081274** runs afterany and writes
`forcing/work/prism-fallback-tests/5081269/campaign-summary.json`, retaining weak
support diagnostics rather than counting exemptions as matched PRISM targets.
This is a provisional regression, not production adoption or scientific approval
of every weak-cell estimate; historical experiment 5081235 remains intact.

### Weak-support regression outcome and full-schema trial

5081269/5081274 passed all 28 blocks and 84 windows, including both previous
rejections, producing 56 calendar files. Each window had 42,404 hard targets
and 19 explicitly weak targets; forcing coverage was unchanged, and no local
fallback was needed. Blocks took 31–54 seconds; calendar assembly took 7–13
seconds. Weak residuals are not necessarily tiny: the largest was -8.329 mm on
2023-01-17 at a 20.734 mm target with only 0.4094% coverage and no active sources.
The exemption is justified by spatial support, not universally small discrepancies.

Array **5081298** is a full-schema integration trial, **not a raw-source rebuild
or model run**. `bin/trial_prism_full_schema.py` combines accepted 5081269 rainfall
with existing seven-field meteorology for two-day blocks beginning 2002-01-15,
2016-04-15, 2023-01-15 and 2000-02-28. The launcher
`slurm/trial_prism_full_schema.sh` runs two concurrent 12-CPU/24-GB tasks with
120-GB scratch requested. Originals and operational output paths are untouched.

All original variables except rainfall are compared exactly (old precipitation
diagnostics are retained under `historical_*` names), including coordinates and
masks. Superseded PRISM/precipitation and parent provenance attributes are marked
historical; trial-specific provenance and hourly timing codes identify the new
rainfall. Old combined forcing QC is likewise historical. Tests check complete
active-cell coverage, nonnegative relevant fields, positive temperature/pressure,
compression and matching timestamps. The middle PRISM window is independently
reconstructed from full-schema files and audited; weak residuals are retained.
Permanent copies are SHA-256 checked against audited scratch files before rename.

Artifacts and per-block `acceptance.json` reports are under
`forcing/work/prism-full-schema-trial/5081298/<start>/`, with candidate files in
`hourly/YYYY/MM/`. The files remain isolated trials, not routine model inputs.
Thirteen focused tests and Ruff passed before submission. This trial does not
establish raw-source production performance, operational QC compatibility or
scientific superiority of fine-scale precipitation patterns.

5081298 completed successfully: all four block acceptance reports passed, with
eight full-schema calendar-day files and zero failed hard targets (42,404 per
reconstructed middle window). Exact non-rain variable/mask comparisons, active
cell coverage, compression, timestamp and permanent-copy checksum checks passed.
Each two-file integration/audit took 32.15–35.70 seconds. These timings exclude
precipitation regeneration because accepted 5081269 candidates were reused.
Production originals were unchanged. A short model ingestion test and resolution
of operational QC/provenance integration remain before operational adoption.

### Model ingestion trial

Job **5081302**, launched with `slurm/test_prism_model_ingestion.sh`, runs the
unchanged executable on 64 MPI ranks using the current expanded CNRFC parameters.
`bin/test_prism_model_ingestion.py` reuses the established integration/output
checks in `test_cnrfc_hu18_model.integration` with the explicit canonical CNRFC
domain path; it does not use the obsolete domain name or promotion workflow.

There is no matching monthly checkpoint at the selected mid-month trial date.
Rather than relabel a historical restart, a one-day cold prelude runs April 14–15,
2016 using original forcing for April 14 and trial forcing for the April 15
endpoint. Its newly generated restart pair initializes an April 15–16 warm run,
using both full-schema trial files including the April 16 00 UTC endpoint.
This is a short ingestion/state-continuation test, not spin-up or scientific
validation. The executable is not rebuilt or modified.

The warm segment uses `rst_typ=1`, `RSTRT_SWC=0`, `GW_RESTART=1`, terrain/channel
timesteps 600 seconds, `PCP_PARTITION_OPTION=1`, `t0OutputFlag=0`, and lakes off.
Daily LDASOUT and both hourly/daily CHRTOUT checks are enabled. Forcing snapshots
are checksum checked against accepted full-schema files, verified to contain
changed rainfall, and checked for grid alignment and active-cell coverage.
The shared helper's `.subset.json` receipt is explicitly labeled
`trial_ingestion_snapshot`, not a claim of a new subsetting operation.

Outputs, namelists, logs, new restart pairs and acceptance reports are isolated in
`nwm/outputs/cnrfc/retro/tests/prism_ingestion/job-5081302/`. Scratch is job-specific
with 120 GB requested. No existing forcing, restarts, outputs or production chain
is modified. Submission/running state must not be mistaken for model acceptance.

5081302 subsequently **passed**: the cold prelude took 42.25 seconds and the
warm continuation 45.28 seconds (138.75 seconds for the complete harness).
Both segments produced 24 hourly CHRTOUT records, one daily LDASOUT and one
daily CHRTOUT record, with accepted restart/state checks. The trial contained
1,503,818 changed rainfall samples. This establishes ingestion and warm-start
compatibility, not climatological/scientific validation.

### Opt-in CONUS production gate

The production entry points `produce_prism_constrained_daily.py` and
`reconcile_prism_precipitation_day.py` now accept
`--precipitation-strategy support-aware-v1`, with explicit `--nldas-root` and
`--active-mask`. Their default remains **legacy**; cron and existing archive
campaigns have not switched strategies. The adapter
`hydro_ops.forcing.prism_support_production` restricts candidate output and
diagnostics to resolved paths below `forcing/work/` and refuses overwrites.

This path integrates the tested PRISM spatial fit, local feasibility fallback,
baseline/NLDAS/uniform hourly timing hierarchy and narrowly defined weak-support
exception with the existing temperature/Q2D/LWDOWN corrections. It checks hard
targets before writing and again after float32 storage, requires unchanged
rainfall coverage, and marks inherited precipitation QC variables `historical_*`.
`prism_timing_source_id` identifies the new timing profile; it is not independent
hourly observation provenance. Reports distinguish hard/weak/excluded-support
counts, active-cell timing fallback counts and weak residuals. Diagnostic NetCDF
files retain hard targets, original PRISM targets, residuals and coverage fractions.
These diagnostics use the new strategy's schema, not the legacy diagnostic schema.
The adapter is not yet wired into routine controllers or archive cleanup gates.

Baseline array **5081312** regenerates January 14–17, 2014 from external inputs
in `forcing/work/prism-conus-support-pilot-v1/baseline/`. Four tasks run at most
two concurrently, each requesting 16 CPUs and 120 GB scratch. No previously
PRISM-constrained rainfall is used as the baseline.

Pilot **5081315**, dependent on successful completion of all baseline tasks,
uses `slurm/trial_prism_conus_production.sh` and
`bin/trial_prism_conus_production.py`. It requests 64 CPUs (nominally 128 GB on
this cluster), 120 GB scratch and an eight-hour test limit. Allocation and peak
memory still need checking against accounting after execution.

The pilot clips its private baseline copies to the approved static envelope
**before** applying PRISM; clipping after reconciliation could break closure.
It processes three PRISM windows and publishes two isolated CONUS calendar
files for January 15–16. Its final audit checks all eight fields and all records
for active-cell completeness, static-envelope compliance and exact equality to
the corresponding accepted window records. It independently reconstructs the
middle PRISM window from calendar files and checks every hard target. Output
checksums, stage timings and failures are written to job-specific
`forcing/work/prism-conus-production/<job>/acceptance.json`; candidates are in
that directory's `hourly/YYYY/MM/`. Intermediate 12–12 windows are clearly kept
under `windows/`, never operational stream directories.

The full unit suite passed **612 tests, 3 skipped** before submission. Submission
is not acceptance: CONUS numerical feasibility, active-cell coverage, temperature
integration, memory and throughput still require the pilot result. Following a
passing audit **and** review of spatial patterns/timing-fallback frequencies,
the next gate is a separate January 2014 month-long candidate campaign, with
December 31 and February 1 baseline boundaries. It is **not submitted yet**.
No historical replacement, baseline cleanup, downstream summary/climatology
regeneration or model rerun is authorized by a candidate acceptance alone.

### CONUS rejection, Florida geometry and precipitation-only repair trial

5081312 completed all four baseline days in 20–25 minutes per task. 5081315
stopped after 32:18: the January 15 window passed 480,448 hard targets, but
January 16 failed two targets. No calendar files were assembled or operational
files changed. The rebuilt baselines remain available for reuse.

Read-only geometry audit **5081421** confirmed a pairwise incompatibility at
25.5 N, approximately 81.25 W. Target 48820 has 5.843% coverage, four retained
forcing contributors and **zero active model contributors**; its 0.306 mm target
conflicts with adjacent target 48821 (0.001 mm, 98.88% coverage, 30 contributors,
11 active). Even the two-target nonnegative problem requires 1.06625 times the
original tolerance. This establishes geometric incompatibility, not simply slow
iteration. Geometry and normalized weights are preserved in
`forcing/work/prism-spatial-conflicts/5081421/geometry.json`. Initial audit job
5081419 failed on an unavailable matplotlib import; the corrected inspector can
write its numerical report without plotting dependencies.

The isolated adapter now permits an explicit `--weak-support-threshold` up to
0.10; the default remains 0.05. The candidate rule is still strictly
**coverage below the threshold AND zero active contributors**, not blanket
relaxation of rainfall tolerances or a mask change. All targets with any active
contributors remain hard. Threshold and hard/weak counts are recorded in each
candidate report and NetCDF provenance. This tests a broader geometrically
weak-support class, not an index-specific exception for Florida.

Reference **5081424** reuses the four existing baseline files, applies the
candidate 0.10 threshold and reruns the January 15–16 calendar gate. No baseline
generation job is resubmitted. Dependent **5081425** uses
`trial_prism_conus_production.py --rain-only` with existing CONUS retro files for
the same dates, after the reference succeeds. Both request 64 CPUs and 120 GB
scratch, and run sequentially to reduce competing I/O in the comparison.

Rain-only mode bypasses static remasking and all temperature/Q2D/LWDOWN
corrections. It uses archived hourly rainfall proportions, raw NLDAS timing
fallback and uniform timing as necessary; the fit supplies new PRISM totals.
It requests compressed-chunk assembly for unchanged fields and independently
compares every record of the seven non-rain fields against the original archive.
Final active coverage, static envelope, calendar timestamps, PRISM closure and
transfer checks remain required. Sources are read-only and all results remain
under `forcing/work/prism-conus-production/<job>/`. The candidate route still
writes intermediate windows and performs full audits; timings must not be
presented as those of an optimized direct in-place archive repair.

These are submitted tests, not accepted replacements. The one-month gate remains
closed pending results and spatial/timing review. Existing model outputs and
restarts are preserved; outstanding CONUS 2001–2002 and UCRB 2014–2026 model jobs
were canceled at the user's request while precipitation repair is assessed.

### Localized comparison exceptions: Nantucket and Quebec

Read-only comparison 5082725 found only 38–59 active cells per PRISM window
with total differences exceeding 0.01 mm, out of 10,315,371 common active cells.
Source/support diagnosis is recorded in
`forcing/work/prism-local-exceptions/5082875/diagnosis.json`; no forcing was edited.

Near Nantucket, six **inactive** cells inside the approved static envelope are
missing in the rebuilt baseline but populated in the older retro archive. No
active-cell coverage differs. Because the current acceptance scope requires all
contributing links to be finite, these six cells exclude three hard targets and
one weak target in the reference. They explain the differing target counts;
this is a historical fill/support difference, not a new active-cell hole. The
rain-only candidate legitimately constrains the additional supported targets.
Support differences must remain explicit in comparisons rather than treating
the rebuilt reference as identical in scope.

At the Quebec sample near 48.45 N, 69.59 W, baseline precipitation provenance is
Stage-IV. Native converted cell (y=828, x=948; flat index 929136) totals
646.23, 334.35, 730.20 and 724.70 mm on calendar days January 14–17, 2014.
The baseline matches that native cell's hourly series to floating-point precision;
nearby NLDAS totals are 7.9376, 0.7109, 0.9969 and 2.2447 mm respectively.
The extreme values therefore are not a large amplification by PRISM fitting.

Direct ecCodes command-line checks of original nested monthly/daily GRIB1
archives confirm native-index hourly values 89.5, 152.12 and 40.5 mm for
January 14 08, January 16 11 and January 17 05 UTC. Corresponding sampled
six-hour products also contain very large native-index values (307.75, 327.62
and 142.5 mm); switching to six-hour Stage-IV alone is not a demonstrated cure.
This does not prove the meteorological cause of the source anomalies.

Legacy GRIB geographic lookup differs slightly from the canonical NetCDF grid:
at the sample it chooses index 928015, while canonical coordinates choose
929136. The four nearest raw grid points include the extreme-valued index.
Thus source extremes are confirmed, but exact legacy geolocation/projection
consistency still needs separate checking; do not infer a wholesale conversion
shift from this single comparison. The cached Stage-IV operator has no direct
link at the sampled NWM cell, so direct-link tracing alone is insufficient for
the dynamic/fallback path.

The January 16/17 PRISM-window values 622.22/740.45 mm in the reference and
500/500 mm in the rain-only candidate exactly match their respective input
archives. Both candidates mark this location unconstrained (timing code 0).
The legacy reconciliation's global 500-mm period cap explains why old retro
amounts differ even outside PRISM coverage. The new repair neither creates nor
resolves those inherited extremes. An explicit quality/coverage fallback to
NLDAS outside trustworthy precipitation support should be tested before broad
publication; increasing the cap or assuming six-hour Stage-IV fixes it would
not address the source-quality issue. No such production policy change was made
during this diagnosis.

### US-connected drainage prioritization

At the user's direction, detailed source repair outside drainage contributing
to the US is deferred. Bounded screen **5083123** traced all sixteen 250-m
routing pixels within representative anomalous land cell (3159, 4220), near
48.449 N, 69.586 W. The forcing and Fulldom grids have opposite row directions;
the corrected mapping is coordinate-checked before tracing. All sixteen paths
terminate near 48.3456 N, 69.4051 W, with missing terminal flow/channel values,
without encountering a reach represented in RouteLink. Paths remain north of
48.3394 N. No downstream connection toward the US was found in this local
NWM terrain/network screen.

Report: `forcing/work/prism-quebec-drainage/5083123/drainage.json`. The initial
5083122 report used the wrong row orientation and is explicitly marked invalid;
it must not be used as drainage evidence. The valid screen covers one
representative anomaly cell, not all Quebec pixels, diversions, groundwater
connections or a new CONUS connectivity mask. It supports **deferring** this
localized source-quality investigation rather than declaring nationwide safety.
Preserve the existing forcing and model masks. Any analogous anomaly in a
Canadian/Mexican watershed draining into the US remains a priority for source
QC and NLDAS fallback testing. Nantucket's existing inactive-cell fill is retained;
the diagnosed difference does not require active-cell repair.

### January 2014 precipitation-only month gate

At the user's request, **5083136** runs the isolated January 1–31, 2014
precipitation-only trial. It reads existing CONUS retro archives (including
December 31 and February 1 boundaries), processes 32 PRISM windows, and aims
to assemble 31 calendar-day candidates. Preflight found all required archived
forcing, native NLDAS and PRISM precipitation inputs; 16 focused tests passed.
No baseline rebuild is submitted.

The existing pilot runner uses `--rain-only --weak-support-threshold 0.10`,
64 CPUs, 120 GB requested scratch and an eight-hour limit on shared-128, with
no reservation. It retains the hard-target tolerances, active-cell protection,
exact seven-field comparisons and independent calendar-assembly audits from
the two-day gate. The run stops if a required acceptance check fails; no
unconverged day is silently accepted. Timing extrapolation from the two-day
trial is approximately 3–4 hours, not a throughput guarantee.

Candidates and reports are under
`forcing/work/prism-conus-production/5083136/`, with calendar files in
`hourly/2014/01/` and the overall outcome in `acceptance.json`. They are not
operational replacements. Existing hourly archives, summaries, subsets, model
outputs and restart files remain untouched. Promotion and wider batches require
review of this month gate, including spatial/timing diagnostics and explicit
weak-support reporting. The localized, non-US-connected Quebec issue remains
documented and deferred rather than silently corrected by this trial.

### Texas rejection and bounded inactive-target experiment

5083136 stopped after 8:48: January 1 passed but January 2 had two incompatible
targets, 156660 and 158065, near 28.71–28.75 N, 95.625 W. No final calendar
files were assembled. Geometry audit **5083766** found that the first target
has 12.47% coverage, four source cells and **no active contributors**; its
neighbor has 90.19% coverage, 27 source cells and **seven active contributors**.
The pairwise minimum tolerance multiplier is 1.07709, establishing that the
original targets cannot both meet the strict bounds with nonnegative rainfall.

Rather than keep increasing the coastal coverage cutoff, the candidate-only
`--bounded-inactive-fallback` experiment adds a separately reported allowance:

- It runs only after the strict local solver fails and reports a minimum
  tolerance multiplier above one. A local failure is not generally proof of
  global infeasibility; the Texas pair has independent pairwise evidence.
- At most **32 currently failing, wholly inactive targets** per window may
  receive a **0.05 mm absolute residual cap** (no additional relative tolerance).
  The cap is a provisional small-error acceptance choice, not an observational
  accuracy claim. Targets whose strict tolerance is already larger do not qualify.
- Every target with any active contributor keeps 0.01 mm + 0.1% of its target.
  Every other strict target keeps its original bound. No target is removed by
  this fallback, no forcing mask is changed, and failure remains fatal when
  the bounded local solve cannot satisfy all declared constraints.
- The local solve uses a 0.99 safety margin; the float32 read-back and subsequent
  calendar reconstruction use the explicit per-target tolerance vector. Reports
  retain exception indices, original strict failures, actual residuals and
  strict/bounded counts. The diagnostic NetCDF stores `absolute_tolerance_mm`;
  accepted windows distinguish `strict` from `bounded_inactive` acceptance.

The existing weak-support policy remains separately declared at 10% for these
candidates; operational defaults are unchanged. New tests cover active-target
protection, the fixed absolute cap, float32 re-audit, exception-count limits,
large-conflict rejection and required infeasibility evidence. Twenty focused
tests passed before submission.

Two-day gate **5083807** tests January 1–2 (three PRISM windows) with the bounded
fallback. Month retry **5083808** is submitted `afterok:5083807` with invalid
dependencies canceled. Both use 64 CPUs, 120 GB scratch and separate
`forcing/work/prism-conus-production/<job>/` candidates; no baseline rebuild,
original-archive replacement, model restart or automatic promotion is included.
If the gate or month finds an unsupported failure, it stops rather than enlarging
the allowance automatically. A passed job with bounded exceptions must never be
reported as universal agreement under the original strict tolerance.
# Bounded-cost precipitation fallback (candidate implementation)

## Accepted month and writer optimization

January trial **5090278 passed** in 3:33:48 on 64 allocated CPUs: 31
calendar-day files and 30 reconstructed interior PRISM-window audits. All 32
input windows passed (26 strict, six direct fallback). Correction/window writing
took 79.6 minutes; calendar assembly took 23.5 minutes; remaining checks took
about 110.6 minutes. This is technical acceptance, not basin-scale scientific
validation or operational promotion.

`bin/benchmark_prism_rain_writer.py` isolates the publication bottleneck without
changing the scientific calculation. It consumes an accepted rain-only trial,
copies each original calendar file to scratch, replaces only rainfall and its
timing provenance, and labels inherited precipitation diagnostics historical.
It checks timestamps, active coverage, the static envelope, nonnegative rain,
exact rain/timing readback against accepted windows, unchanged compressed
meteorological payloads and encoding, input immutability, and publication
checksums. It publishes only to a new job-specific `forcing/work` directory,
using atomic no-overwrite publication. It does not rebuild intermediate windows
or rerun their scientific fits. Existing workflows remain unchanged.

The optional `--compare-reference` additionally compares all eight decoded
fields to the accepted calendar files; this measures acceptance-test overhead
separately from the lean writer. Common-window conservation transfers through
exact record equality, rather than another inverse-fit pass. It does not mean
individual basin conservation has been independently verified.

Four-day January 4–7 benchmarks are success-dependent and sequential to reduce
mutual I/O contention: **5091304** (one worker, full reference equality),
**5091305** (one worker, lean checks), **5091306** (two workers, lean checks).
Reports: `forcing/work/prism-rain-writer/<job-id>/acceptance.json`, with per-day
copy/write/readback/compressed-audit/publication timings. Each requests 64 CPUs
for memory and 120000 MB scratch; worker count is separate from allocated CPUs.
These measure writer/audit optimization only, not end-to-end repair throughput.

All three writer benchmarks passed. SLURM elapsed times were 9:51 (full
reference equality), 3:57 (lean, one worker), and 2:10 (lean, two workers).
All four output SHA256 values matched across the three runs, and the full
reference comparison verified all eight fields. Two-worker throughput was
1.84 times one-worker throughput. These timings excluded window calculation.

### Integrated compact-window pipeline

`bin/trial_prism_rain_pipeline.py` now combines precipitation calculation and
the validated copy/replace writer. `--compact-rain-window` on the support-aware
adapter writes only time, RAINRATE and timing provenance, not seven unchanged
meteorological fields. These internal 12–12 windows are explicitly not model
forcing. Scientific fitting, eligibility, timing allocation, float32 readback
and strict/direct conservation gates are unchanged. The regular adapter still
defaults to full-schema windows; operational runners are not switched.

The isolated runner uses separate process pools for window calculation and
calendar publication, each bounded to at most two workers. Stages do not overlap;
all windows must pass before any calendar candidates are written. Output roots
are exclusive job-specific directories, leaving production originals untouched.
Reports include window input/weight loading, spatial fit, timing allocation,
window writing/auditing, publication, and calendar-writer phase timings.

Sequential success-gated jobs: **5092430** (January 4–7, one worker per stage,
full reference equality), **5092431** (same four days, two workers per stage,
full reference equality), then **5092432** (January 1–31, two workers, lean
checks). Each requests 64 CPUs, 120000 MB scratch, and four hours. The previous
month's peak reported memory was about 11.2 GiB for one process; the two-worker
trial must still be checked for actual peak usage. Reports are at
`forcing/work/prism-rain-pipeline/<job-id>/acceptance.json`.

The month is conditional on both equality benchmarks succeeding. Its publication
gate uses exact rain/timing record equality to independently audited windows,
unchanged compressed meteorological payloads, active/static coverage, timestamps,
input immutability and checksums rather than repeating all decoded field scans.
No archive-wide repair or scientific basin-validation claim is implied.

All three integrated runs passed: 5092430 took 14:33, 5092431 took 8:03,
and the full January 2014 job 5092432 took **33:06**, versus 3:33:48 for
the original acceptance runner. Both four-day runs matched all eight fields
against the accepted reference. The month passed 32 window checks (26 strict,
six fallback) and published 31 isolated candidates. Its window and writer
stages took 15.5 and 17.6 minutes respectively; reported peak memory was
approximately 26 GiB. This is measured end-to-end candidate throughput.

### Wet-month totals test

January 2023 is the next wet-month candidate, job **5092471**, using two window
workers and two writer workers (64 allocated CPUs, 120000 MB scratch). No old
accepted trial is required for a new date range; reference comparison still
requires an explicitly accepted reference. All scientific window gates and
writer integrity gates remain mandatory. Production originals remain unchanged.

Dependent job **5092473** runs `bin/audit_prism_rain_basins.py` with 16 CPUs
after the wet-month pipeline succeeds. It reports daily PRISM-window and
month-summed area-weighted precipitation/bias for common CONUS support, the
expanded CNRFC reporting region, and the 374 forecast-basin polygons. Forecast
basins are copied into the audit output with a SHA256 provenance record; no
operational boundary or mask is changed. Region results include represented
PRISM-cell counts and coverage fractions so very small/partial basins can be
identified. Dry-day percentages are undefined; wet-day percentage summaries
use reference depth at least 1 mm. No new arbitrary acceptance tolerance is
introduced: results require scientific review.

The diagnostic uses audited stored NWM totals mapped conservatively back to
PRISM, with fractional polygon intersection calculated in EPSG:5070. It uses a
fixed common eligible support across days, reports exclusions, and assumes
coverage is uniform inside partially supported PRISM cells. This is therefore
a **PRISM-grid basin diagnostic**, not exact polygon clipping of individual
NWM boundary pixels. Areas outside PRISM support are not validated this way.

Monthly totals here mean the sum of daily PRISM labels January 1–31 (December
31 at 12 UTC through January 31 at 12 UTC under our recorded window convention),
not a UTC calendar-month total or the independently published PRISM monthly
product. The February 1 window is needed to assemble January 31's UTC file but
is excluded from this labeled-month comparison. Reports are under
`forcing/work/prism-basin-audit/5092473/basin_totals.json`; production remains
isolated under `forcing/work/prism-rain-pipeline/5092471/`.

### Publication rehearsal and 512-CPU scaling wave

The user paused cron and authorized a publish/subset/retry rehearsal plus an
eight-job scaling test, with subdomain reruns prioritized before CONUS model
reruns. These tests **do not replace live production forcing or launch models**.

- **5092480**: `bin/test_prism_publish_subset_retry.py` rehearses January 1–2,
  2023 publication in `forcing/work/prism-publish-subset-test/5092480/`. It
  pins the accepted wet-month candidate checksums, retains original hard links,
  locks each destination, writes durable intent, replaces the sandbox file
  atomically, deliberately interrupts before receipt completion, then retries.
  Retry must reconcile the receipt without changing the newly published inode;
  another retry must skip. Exact subsets for CNRFC/B120/UCRB/Yampa must pass
  existing coverage/value checks, and repeated subsetting must skip unchanged
  results. Live originals are checked unchanged. Summary products remain pending.
- **5092481**: freezes December 31, 1980–September 1, 1981 (both boundary days)
  using hard links under `forcing/work/prism-repair-wave1-198101-198108/originals`.
  The snapshot and code hashes are recorded before any array task starts.
  Hard links preserve original inodes under atomic replacement; they are not
  protection against in-place modification, which identity checks detect.
- **5092482**, tasks **0–7**, concurrency **8**: January–August 1981, one month
  per task, **64 CPUs and 120000 MB scratch each (512 CPUs total)**, two window
  and two writer workers per task. Success-dependent on both rehearsal and
  snapshot preparation. All output remains isolated, under
  `forcing/work/prism-rain-pipeline/5092482_<task>/`. Scratch paths use the actual
  SLURM job ID, not the array display identifier.
- **5092483**: after all eight tasks pass, check their accepted day counts,
  compare shared PRISM boundary-window rainfall/timing exactly across adjacent
  months, and save task timings/accounting and an aggregate acceptance report
  under the wave root. Invalid dependencies cancel dependent tests.

The prototype publisher is deliberately restricted to `forcing/work`. It is
not yet a production publisher. Data replacement and JSON receipts are not a
multi-file atomic transaction: consumers must honor receipt readiness and
reconcile interrupted intent. The rehearsal retains old files and evidence;
no snapshot cleanup or deletion of old production/model files is authorized by
this test. Full production integration still needs existing manifest/status
semantics, derived-product invalidation, consumer coordination and bounded
automatic repair/cleanup. Eight accepted isolated months can be reused after
those publication gates are implemented and approved; no automatic promotion
is attached to the throughput array.

The January 2014 month trial 5083808 stopped on the January 5 PRISM window:
two active-supported targets could not meet the strict inverse-fit tolerance.
Inactive-only exceptions cannot resolve this class of conflict.

The isolated CONUS trial now accepts `--direct-fallback` (rain-only mode).
This caps the multiplicative spatial fit at 200 iterations and skips local LP
retries. If strict agreement still fails, it directly remaps the entire eligible
PRISM window to NWM using overlap areas. Whole-window fallback avoids an
unvalidated seam between a local replacement and the fitted exterior. It does
not use legacy precipitation amplitudes for daily totals. Baseline timing,
NLDAS timing, and uniform timing remain the ordered hourly allocation choices.

Overlap area is reconstructed from each normalized NWM-to-PRISM link times
PRISM cell area and covered fraction. Reverse area-weighted averaging conserves
volume over the **common covered area**, not necessarily whole boundary pixels
or every individual basin. Missing/weak/excluded PRISM targets retain the
existing eligibility policy; forcing coverage and masks do not change.

Acceptance is explicit: `accepted_strict`, `accepted_fallback`, or rejection
for invalid inputs/failed audits (the older inactive-only trial also retains
`accepted_bounded_inactive`). Direct fallback is not marked converged to the
original PRISM cell targets. Reports retain original strict residuals/failures.
Both stored window data and reconstructed calendar data must match independently
recomputed direct totals within 0.00001 mm + 0.000001 times depth, and common-area
volume within 0.000001 relative error. Negative/missing active data, timestamps,
mask changes, and altered non-rain fields remain hard failures. Global volume
conservation is **not** a substitute for basin-scale scientific validation.

The first regression covers January 4–5 calendar files (including the failing
January 5 PRISM window), followed by a success-dependent January month trial.
Submitted jobs: **5090277** (two-day regression) and **5090278** (January,
`afterok:5090277`, cancel on invalid dependency). Each requests 64 CPUs and
120000 MB scratch; acceptance reports are under
`forcing/work/prism-conus-production/<job-id>/acceptance.json`.
No operational defaults, production archives, model runs, or cron jobs are
changed. No production promotion is implied by candidate acceptance. PRISM
windows remain 12–12 UTC; published files remain 00–23 UTC.
