# NWM 1-km meteorological forcing production workflow

The newly approved [two-stage PRISM revision policy](prism_revision_policy.md)
supersedes the all-release revision objective once its tested activation gate
passes: daily second-release NRT selection, monthly stable retro selection.
Until activation, the prior policy remains effective; the broad NRT catch-up
campaign is paused at its batch boundary. Model hooks remain disabled.

## Current operating state (November 10, 2026)

Daily and six-hourly forcing cycles and status reporting are enabled on login1;
automatic model launches remain suppressed with `--skip-model-runs`. Monthly
Retro remains paused. See [cron deployment](cron_environment.md) for the verified
installation and schedules. Thermal repair publication 5232861 and the subsequent
daily/six-hourly commissioning chains passed. Earlier dated sections below retain
the investigation and submission history: statements such as "cron remains
paused" or "candidate only" describe that point in time, not current operations.
Acceptance reports and current source signatures still govern each publication;
this overview does not clear unresolved revision obligations or authorize cleanup.

## Operational time and daily-summary convention

NWM hourly forcing is stored in UTC calendar chunks containing timestamps `00-23`. A 24-hour
model cycle initialized from a paired restart at day `D 00` consumes endpoint forcing `D 01`
through `D+1 00`, crossing the chunks for `D` and `D+1`. Model-interval forcing diagnostics use
those same 24 endpoints and bounds `[D 00,D+1 00]`; ordinary timestamp-based `00-23` daily
statistics answer a different question. `bin/reduce_forcing_model_day.py` implements the model-
interval definition, including rate integration, completeness checks, provenance, and explicit
time bounds. See `docs/nwm_time_conventions.md` for the canonical operational timeline.

Full-CONUS job 4464621 reduced the 2026-03-10 NRT model interval in 1 minute 58 seconds with
2.4 GB peak memory. The level-2 compressed one-record product was 216 MB. Its temperature means
and integrated precipitation samples matched direct calculations from hours 01 through next-day
00, and its midpoint and bounds were `2026-03-10 12 UTC` and
`[2026-03-10 00 UTC, 2026-03-11 00 UTC]`.

## Purpose and scope

### Support-aware PRISM precipitation: history versus current operations

The initially opt-in `support-aware-v1` path was developed to address
zero/near-zero baseline rainfall and incompatible low-support coastal targets.
It fits PRISM totals without baseline-ratio caps, selects baseline/NLDAS/uniform
hourly timing, and separately reports narrowly defined weak coastal targets.
All declared hard targets must pass the stored-value audit. The existing
temperature correction and humidity/longwave adjustments remain in the path.
The original candidate tests were isolated in `forcing/work/`; subsequent
audited archive repairs and routine NRT integration are recorded in the
[repair rollout](prism_rain_repair_rollout.md) and the operational revision
sections below. Do not interpret those early test receipts as current defaults
or as authorization to rerun historical publication scripts. The standalone
daily producer retains an explicit strategy switch; use the current coordinated
workflow rather than assuming every entry point has the same default. Model
ingestion alone is not scientific acceptance or replacement authorization.

This document defines the production design for complete hourly meteorological forcing on the
National Water Model (NWM) CONUS 1-km grid. The output contains the eight fields represented by
the sample `LDASIN_DOMAIN1` files:

| NWM variable | Quantity | Units |
|---|---|---|
| `RAINRATE` | precipitation rate | `kg m-2 s-1` |
| `T2D` | 2-m air temperature | `K` |
| `Q2D` | 2-m specific humidity | `kg kg-1` |
| `PSFC` | surface pressure | `Pa` |
| `SWDOWN` | surface downward shortwave radiation | `W m-2` |
| `LWDOWN` | surface downward longwave radiation | `W m-2` |
| `U2D` | 10-m eastward wind | `m s-1` |
| `V2D` | 10-m northward wind | `m s-1` |

The production system combines MRMS, Stage-IV, NLDAS-2, HRRR, PRISM, and GFS. GFS
short forecasts are a **required northern-coverage fallback in NRT operations**
for HRRR-selected hours, not an opt-in experiment or a replacement for NLDAS-2.
NLDAS-only hours and stable retrospective production do not require GFS.
Routine Stage-IV acquisition uses NOMADS realtime only, retaining the seven-day
revision lookback and hourly/six-hourly products. The apparently stalled EMC
archive is used only for explicitly requested historical downloads; previously
downloaded archive data remain valid inputs. Missing older realtime listing
entries are logged and recorded under the Stage-IV input `status/realtime-availability/`
directory. This acquisition change does not alter precipitation selection,
the CNRFC correction, or existing forcing products. See the README Stage-IV setup.
Source choice depends
on the variable, latency, revision state, effective resolution, quality information, and
physical consistency. No product is assumed to be universally best merely because its nominal
grid is finer or because it is described as an analysis or reanalysis.

Detailed algorithms for the two variables with external observation-based daily constraints
are maintained separately:

- [Precipitation production workflow](precipitation_production_workflow.md)
- [Air-temperature production workflow](temperature_production_workflow.md)

This master specification is authoritative for cross-variable coupling, common operational
rules, final file assembly, and the production methods for pressure, humidity, radiation, and
wind.

## Design principles

1. **Use observations for constraints, not invented hourly detail.** PRISM can constrain daily
   precipitation and temperature but cannot determine their hourly timing at 1 km.
2. **Preserve physical consistency.** Temperature, pressure, humidity, and longwave radiation
   are processed as a coupled thermodynamic group even when they are stored as separate output
   variables.
3. **Remap directly to the target grid.** An intermediate 4-km forcing grid would discard
   useful MRMS and HRRR structure. Every native grid is mapped directly to the NWM grid.
4. **Separate spatial detail from accuracy.** HRRR provides meaningful mesoscale structure,
   while NLDAS-2 provides a long, consistent, observation-informed retrospective forcing
   record. Resolution alone does not establish lower bias.
5. **Treat recent output as mutable.** Near-real-time hours are revised as MRMS Pass 2,
   Stage-IV, NLDAS-2, and newer PRISM revisions arrive.
6. **Make every decision auditable.** Source, quality, terrain adjustment, daily constraint,
   revision, fallback, and software/weight versions accompany the forcing values.
7. **Calibrate without contaminating evaluation.** Parameters and thresholds are estimated on
   designated calibration years and assessed on withheld years and independent observations.

## Source roles and initial production policy

### Effective source characteristics

NLDAS-2 File A has an hourly 0.125-degree grid, but its non-precipitation meteorology is based
largely on 32-km, 3-hourly NARR analysis fields that were spatially interpolated, temporally
disaggregated, and in several cases adjusted to NLDAS terrain. Its nominal grid spacing must
therefore not be interpreted as 0.125-degree independent atmospheric detail. Its shortwave
radiation has an observation-based GOES/SRB bias correction.

HRRR provides hourly, approximately 3-km analyses tied to HRRR terrain and surface physics. It
offers substantially more mesoscale structure but remains a numerical weather prediction
analysis with model and data-assimilation biases. HRRR analysis is the timely source while
NLDAS-2 is delayed. During overlap, NLDAS-2 supplies the observation-informed large-scale and
hourly retrospective baseline; HRRR is not a wholesale replacement. Instead, an evaluated
hybrid may add only the mesoscale anomaly that HRRR resolves beyond the effective NLDAS-2
scale. This distinction is important because remapping 3-km HRRR to 1 km preserves useful
approximately 3-km structure but does not manufacture 1-km atmospheric information.

### NLDAS-2 baseline plus HRRR anomaly

For a continuous variable `F`, the candidate retrospective hybrid has the conceptual form:

```text
F_hybrid(x, t) = F_NLDAS_downscaled(x, t) + w(x, t) * A_HRRR(x, t)
A_HRRR          = F_HRRR_fine - C_to_fine(F_HRRR_fine)
```

`C_to_fine` means coarsening HRRR to the effective NLDAS/NARR scale and returning it to the
target grid with a documented, conservative or intensive-variable-appropriate operator. Thus
`A_HRRR` carries spatial and temporal structure rather than HRRR's large-scale mean bias. The
weight `w` is fitted only on calibration data and may depend on variable, region, season,
terrain/elevation class, hour, and weather regime. It is zero wherever independent withheld
validation does not show material improvement.

The exact anomaly representation is variable-specific. Temperature may use additive anomalies;
pressure should use log-pressure anomalies; humidity should use bounded RH or vapor-pressure
space; shortwave should preferably use clear-sky index; longwave must remain consistent with
the thermodynamic bundle; and wind uses earth-relative vector-component anomalies. Anomalies
are limited with calibration-derived safeguards, and the coupled temperature-pressure-humidity-
longwave fields must not be independently mosaicked into a physically inconsistent hour.

This hybrid is a calibration target, not yet an enabled production rule. Until it passes
withheld validation, stable retrospective output retains NLDAS-2 and near-real-time output uses
HRRR provisionally, with required GFS coverage where native HRRR is absent.
When NLDAS-2 arrives, affected provisional hours are regenerated rather
than permanently splicing the two products at the latency boundary.

### Initial hierarchy by output

| Output | Stable retrospective baseline | Near-real-time/fallback | External constraint or special rule |
|---|---|---|---|
| Precipitation | Conditional MRMS Pass 2/Pass 1, Stage-IV, NLDAS-2, HRRR | Best eligible product available | PRISM daily precipitation |
| Temperature | NLDAS-2 | HRRR analysis | PRISM daily Tmin/Tmax and elevation |
| Pressure | NLDAS-2, paired with temperature | HRRR analysis, paired with temperature | Hydrostatic elevation adjustment |
| Humidity | NLDAS-2, paired with temperature and pressure | HRRR analysis as the same bundle | Preserve RH; recompute specific humidity |
| Shortwave | NLDAS-2 initially | HRRR analysis | Preserve night; evaluate HRRR detail independently |
| Longwave | NLDAS-2, coupled to thermodynamic bundle | HRRR analysis | Cosgrove elevation adjustment |
| Wind U/V | NLDAS-2 baseline plus validated HRRR vector anomalies; NLDAS-2 until validated | HRRR analysis | Rotate vectors correctly; evaluate terrain-flow benefit |

Every HRRR entry in the NRT column includes the required GFS northern-gap
completion described below: a common seven-variable meteorological bundle
(`T2D`, `PSFC`, `Q2D`, `SWDOWN`, `LWDOWN`, `U2D`, `V2D`) within the approved static
envelope. GFS precipitation fills only unsupported precipitation cells; it does
not override eligible MRMS, Stage-IV or other selected precipitation.

This table is the version-one policy. The three-water-year overlap evaluation may justify
bias-corrected HRRR or a conditional hierarchy for additional variables. Such a change requires
documented independent validation and a production-method version increment.

## Common time, grid, and missing-data conventions

All output times are UTC. Instantaneous fields are labeled by their valid time. Precipitation
is an accumulation over `(T-1h, T]`, labeled by ending time `T`, before conversion to a rate.
PRISM uses 1200-1200 UTC day-ending periods as described in the detailed precipitation and
temperature documents.

Every adapter must normalize:

- Time and, where applicable, interval bounds.
- Units and sign convention.
- Missing values and valid-data masks.
- Native cell centers, cell bounds, projection, and grid-orientation metadata.
- Source product version, analysis/forecast cycle, publication time, and filename.
- Source terrain and its provenance where elevation adjustment is required.

Missing or out-of-domain values remain missing. Zero is a valid physical value for
precipitation, wind components, and shortwave radiation and must never be used as a generic
missing-data replacement.

The extracted NWM grid definition, SCRIP file, active-domain mask, and target elevation are
immutable, fingerprinted production inputs. A changed grid or mask requires new remapping
weights and a new output version.

## Direct spatial remapping

Maintain separate ESMF/CDO weights for every native-grid definition and target-grid revision.
Weight manifests contain hashes of source and target grid descriptions, remapping method,
mask policy, normalization, creation command, and software version.

| Field | Remapping method |
|---|---|
| Precipitation depth | First-order conservative |
| Elevation-normalized temperature | Bilinear |
| Log pressure or hydrostatically normalized pressure | Bilinear |
| Relative humidity | Bilinear |
| Shortwave and longwave radiation | Bilinear |
| Earth-relative U/V wind components | Bilinear |
| Continuous quality indices | Bilinear where meaningful |
| Source IDs, QC flags, and masks | Nearest neighbor or explicit compositing logic |

Conservative remapping is required for precipitation because it is an extensive water amount.
Bilinear interpolation is appropriate for the remaining continuous state or flux fields.
Nearest-neighbor interpolation of a continuous forcing solely to preserve the native value is
not an acceptable default.

## Ordered hourly production pipeline

The order is part of the scientific algorithm:

1. Discover inputs and resolve their revision state.
2. Validate files, coordinates, units, times, masks, and plausible source ranges.
3. Produce preliminary precipitation according to its conditional source workflow.
4. Select one thermodynamic source bundle for temperature, pressure, humidity, and longwave:
   NLDAS-2 retrospectively or HRRR when NLDAS-2 is unavailable.
5. Derive source relative humidity from the bundle's temperature, pressure, and specific
   humidity.
6. Elevation-normalize temperature and pressure, then remap temperature, pressure, and RH to
   the NWM grid.
7. Restore temperature and pressure to NWM target elevation.
8. Apply the PRISM Tmin/Tmax temperature correction when eligible.
9. Recompute specific humidity from remapped RH, final `T2D`, and final `PSFC`.
10. Elevation-adjust longwave consistently with the final thermodynamic state.
11. Remap and validate shortwave radiation.
12. Select, rotate, and remap wind components.
13. Apply the PRISM precipitation constraint when eligible and convert hourly depth to
    `RAINRATE`.
14. Perform cross-variable validation and write the eight forcing fields plus provenance
    atomically.

The bundle rule prevents combinations such as HRRR temperature with NLDAS-2 humidity from
creating artificial relative-humidity jumps. The final humidity calculation must occur after
PRISM temperature correction; otherwise `Q2D` would be inconsistent with the published
temperature.

## Precipitation

Precipitation follows the complete algorithm in
[the precipitation workflow](precipitation_production_workflow.md). In summary:

- Remap all hourly depths conservatively from their native grids.
- Use conditional, quality-aware selection among MRMS Pass 2, MRMS Pass 1, Stage-IV,
  NLDAS-2, and HRRR rather than a fixed national overwrite order.
- Preserve both MRMS passes and distinguish realtime and archive Stage-IV revisions.
- Reconcile complete 1200-1200 UTC daily totals to the appropriate PRISM revision with a
  constrained, nonnegative method that preserves hourly fractions where possible.
- Convert corrected hourly depth to `RAINRATE` only at final output.

Precipitation has its own source identifier because its best source normally differs from the
other meteorological variables.

## Temperature

Temperature follows [the air-temperature workflow](temperature_production_workflow.md). In
summary:

- Use NLDAS-2 as the version-one retrospective hourly shape and HRRR analysis during the
  NLDAS-2 latency gap.
- Normalize from source terrain with a configurable initial lapse rate of
  `gamma = -0.0065 K m-1`, interpolate, and restore to NWM elevation.
- Use PRISM Tmin/Tmax to correct the daily midpoint and range without forcing the arithmetic
  hourly mean to PRISM `tmean`.
- Treat early/provisional PRISM as mutable within the retained NRT stream. Publish stable data
  independently to the retrospective stream rather than replacing NRT output.

The final PRISM-adjusted `T2D`, rather than the preliminary baseline, drives the final humidity
and longwave calculations.

## Surface pressure

### Rationale

Surface pressure is strongly controlled by elevation. Direct interpolation from a coarse grid
to NWM cells would assign pressures representing the wrong terrain. Both NLDAS-2 and HRRR
pressure are tied to their source-grid surface elevation, so they use the same physical
transformation.

Pressure remains paired with the selected thermodynamic source. NLDAS-2 is used for the stable
retrospective record and HRRR fills the latency gap. Switching pressure independently from
temperature and humidity is prohibited in version one.

### Method

Given source pressure `p_s`, source temperature `T_s`, target-adjusted temperature `T_t`, source
elevation `z_s`, target elevation `z_t`, dry-air gas constant `R_d`, gravity `g`, and constant
lapse rate `gamma`, the hydrostatic/ideal-gas adjustment is:

```text
T_t = T_s + gamma * (z_t - z_s)
p_t = p_s * (T_t / T_s) ** (-g / (R_d * gamma))
```

The implementation uses elevation normalization and interpolation rather than applying this
equation only after raw pressure has been interpolated across differing source elevations.
Log-pressure interpolation may be used because pressure varies approximately exponentially
with height. Numerical implementation must handle the near-isothermal limit and reject
nonpositive pressure or temperature.

The pressure adjustment uses the preliminary lapse-adjusted temperature field, not the later
PRISM daily temperature-range correction: PRISM constrains 2-m temperature but does not imply
a corresponding change to the atmospheric mass column. Final humidity nevertheless uses final
PRISM-corrected temperature with this hydrostatically adjusted pressure.

### Diagnostics

Store source and target elevation, elevation difference, unadjusted/remapped pressure,
hydrostatic pressure increment, source ID, and QC flags for implausible inputs or adjustments.

## Specific humidity

### Rationale

Specific humidity cannot be lapse-adjusted independently. A value valid at one temperature and
pressure may imply supersaturation after temperature is lowered at a higher target elevation.
Following Cosgrove et al., relative humidity is assumed constant through the elevation
translation. This preserves atmospheric moisture demand more consistently than directly
interpolating `Q2D`.

### Method

For the selected thermodynamic bundle:

1. Compute saturation vapor pressure `e_s(T_s)`. The production coupled processor uses the
   meteorological RH-over-liquid-water convention at all temperatures, matching NLDAS-2's
   subfreezing humidity behavior; water/ice auto-selection remains available as a lower-level
   diagnostic option.
2. Diagnose vapor pressure and relative humidity from source specific humidity:

   ```text
   e(T, p, q) = q * p / (epsilon + (1 - epsilon) * q)
   RH_s       = e(T_s, p_s, q_s) / e_s(T_s)
   epsilon    = R_d / R_v
   ```

3. Compute saturation specific humidity for source diagnostics:

   ```text
   q_sat(T, p) = epsilon * e_s(T) / (p - (1 - epsilon) * e_s(T))
   ```

4. Apply source QC, then bilinearly interpolate RH to the NWM grid. Do not use unclipped
   supersaturated or negative source values silently.
5. After target-elevation adjustment and PRISM temperature correction, calculate target vapor
   pressure and specific humidity exactly:

   ```text
   e_target = RH_target * e_s(T2D)
   Q2D = epsilon * e_target / (PSFC - (1 - epsilon) * e_target)
   ```

This sequence preserves source RH while ensuring that final temperature, pressure, and
specific humidity are mutually consistent. Small tolerance-scale RH excursions may be clipped;
material excursions are flagged and either rejected or handled by a documented fallback. The
initial operational processor clips and flags source RH through 110% (a fractional tolerance of
0.10) and rejects the complete input hour for any larger excursion; this threshold must be
revisited during calibration.

### Validation

Recalculate RH from final `T2D`, `PSFC`, and `Q2D`. Require finite, nonnegative humidity and RH
within the configured physical tolerance. Compare source and final RH to verify that remapping
and floating-point operations did not introduce unintended changes.

## Downward shortwave radiation

### Rationale and source policy

NLDAS-2 is the initial stable retrospective source because its NARR shortwave field was
bias-corrected using GOES-based Surface Radiation Budget data. HRRR supplies timely analyses and
potentially valuable 3-km cloud structure. Its greater resolution does not by itself establish
lower radiation bias, so a historical switch to HRRR requires independent validation.

Shortwave is not adjusted with the temperature lapse rate. Elevation effects on clear-sky
transmissivity, slope, aspect, and terrain shading are not reliably represented by a simple DEM
correction and are outside version one.

### Method

- Bilinearly remap downward surface shortwave flux directly to the NWM grid.
- Preserve exact source nighttime zeros and enforce zero where target solar geometry places the
  sun below the horizon, allowing a small configurable twilight tolerance.
- Reject negative values beyond numerical tolerance.
- Do not use an interpolation method that spreads daytime flux across the terminator without a
  target-grid solar-geometry check.
- Record whether the source is NLDAS-2 or HRRR and report the source transition.

The implemented hourly processor uses NOAA's fractional-year solar-position approximation on
each NWM cell. Its initial twilight threshold is −0.833° solar elevation, representing the
apparent sunrise/sunset horizon; the threshold is configurable and retained in output
provenance. Negative source values down to `-0.1 W m-2` are treated as numerical noise, clipped,
and flagged. Larger negative values reject the hour. Values over `1400 W m-2` are retained but
flagged for review rather than silently capped.

A later evaluated hybrid may retain NLDAS-2 large-scale bias characteristics while using HRRR
cloud-scale anomalies, preferably in clear-sky-index space. Such a hybrid is not enabled until
it demonstrates improvement against independent radiation observations.

## Downward longwave radiation

### Rationale

Longwave radiation depends on atmospheric temperature, water vapor, and clouds and therefore
belongs to the thermodynamic source bundle. A raw flux interpolated from the wrong terrain
elevation is inconsistent with the adjusted near-surface state. The Cosgrove method uses the
Stefan-Boltzmann relationship to adjust the radiative emission state while retaining source
information about atmospheric emissivity and cloud enhancement.

### Method

1. Select longwave from the same NLDAS-2 or HRRR source as temperature, pressure, and humidity.
2. Diagnose source vapor pressure and the clear-sky emissivity term using the documented
   Cosgrove equations.
3. Diagnose the multiplicative cloud/emissivity adjustment implicit in source `LWDOWN`.
4. Elevation-normalize the radiative temperature/state, bilinearly remap the continuous terms,
   and restore them using final target temperature, pressure, humidity, and elevation.
5. Recalculate target `LWDOWN` through the Stefan-Boltzmann law while retaining the remapped
   source cloud adjustment.

The implementation must reproduce the equations and constants from Cosgrove et al. explicitly;
it must not approximate longwave as `sigma * T2D**4` with unit emissivity. Cloud-factor and
emissivity bounds are configuration parameters established during calibration and must trigger
QC flags when invoked.

For implementation, equations 14-18 reduce to the following source-to-target ratio, with vapor
pressure `e` expressed in hPa, temperature in kelvin, and pressure in Pa:

```text
e       = q * p / (0.622 * 100)
epsilon = 1.08 * (1 - exp(-(e ** (T / 2016))))

L_target = L_source * (epsilon_target * T_target**4) \
                     / (epsilon_source * T_source**4)
```

The Stefan-Boltzmann constant cancels in the ratio. The version-one code follows the paper's
published vapor-pressure approximation here even though the humidity consistency calculation
uses the exact specific-humidity conversion.

Longwave is finalized after PRISM temperature correction and humidity reconstruction. This is
the most internally consistent ordering, but validation must quantify how much the PRISM daily
correction alters hourly longwave flux.

## Wind components

### Rationale and source policy

HRRR's native approximately 3-km terrain and boundary-layer analysis contain more meaningful
near-surface wind structure than the much coarser NARR information underlying NLDAS-2, making
wind one of the strongest candidates for an HRRR refinement. Nevertheless, stable retrospective
production retains NLDAS-2 as its baseline. HRRR vector anomalies receive nonzero weight only
in strata where withheld station and hydrologic validation demonstrates improvement. HRRR is
the direct provisional source during the NLDAS-2 latency window; NLDAS-2 remains the fallback
outside HRRR availability and supplies the consistent long historical record.

Version one does not apply empirical 1-km terrain-speed enhancement, valley channeling, canopy,
or exposure corrections. A DEM alone cannot determine these effects reliably, and an
unvalidated correction could create extreme mountain winds.

### Method

1. Read the GRIB vector-orientation flag and projection metadata; never assume components are
   earth-relative merely from their variable names.
2. Rotate native grid-relative vectors to earth-relative eastward and northward components
   before spatial interpolation.
3. Bilinearly interpolate `u` and `v` components independently. Do not interpolate speed and
   meteorological direction.
4. Write earth-relative `U2D` and `V2D`, matching the sample NWM variables' CF standard names.
5. Reconstruct wind speed and direction for validation only.

Calm winds are valid. Missing components are handled as a missing vector pair; a valid zero
component must not be interpreted as missing. Cells or hours switching to the NLDAS-2 fallback
are flagged, and transition discontinuities are reported.

The implemented HRRR rotation uses the archived grid definition (`LoV=-97.5°`, tangent standard
parallel `38.5°`) to calculate Lambert meridian convergence at every native cell. The rotation
occurs before interpolation, preserving vector meaning. The NLDAS-2 adapter bypasses rotation
because its components are already earth-relative.

## Cross-variable quality control

In addition to variable-specific checks, every output hour must pass:

- Identical time coordinates, NWM grid, active mask, and array shapes for all eight fields.
- Finite values on every active cell unless the publication mode explicitly permits and reports
  gaps.
- `RAINRATE >= 0`, `SWDOWN >= 0`, and physically plausible radiation upper bounds.
- Positive `T2D` in kelvin and positive `PSFC`.
- Nonnegative `Q2D`, internally consistent RH, and no unflagged supersaturation.
- Paired finite `U2D` and `V2D`.
- Nighttime shortwave consistency with target solar geometry.
- Thermodynamic source-bundle consistency.
- Complete source identifiers and QC flags for every active cell.

Spatial seam diagnostics compare neighboring cells with differing sources. Temporal diagnostics
compare each hour with its predecessor and explicitly summarize product transition times. QC
thresholds detect suspect values; they do not silently replace scientifically plausible
extremes.

## Output, provenance, and publication

### Final spatial-domain repair and publication gate

Spatial repair is the final scientific transformation of every published NWM forcing day. The
ordering is fixed: assemble the source fields (including the post-2020 CNRFC Stage-IV hourly/
six-hourly correction), apply the applicable PRISM constraint, materialize the 00-23 UTC daily
collection, repair missing active-land cells inside the hard NLDAS-2 rectangle, preserve existing
inactive-cell values inside it, mask cells outside it, and then run the publication audit.
No later transformation may overwrite these fields.

The hard forcing boundary is 25-53 degrees north and 125-67 degrees west. Missing active values
inside that rectangle are filled from the nearest valid donor inside the same rectangle; values
outside it are never used as donors. The operational NWM land mask comes from
`nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc`. Publication requires every
hour of all eight forcing variables to be nonmissing over active (`XLAND == 1`) model cells and
missing outside the rectangle. All 10,315,371 active cells lie inside it. Existing values on the
3,414,803 inactive in-domain cells are preserved, including inland lakes/rivers and any missing
values. The fill operation does not create new values on inactive cells. Donors may be any
valid in-domain cell; the hard geographic boundary applies to both donors and fill targets.

Baseline production and final daily/monthly PRISM publication now use
`repair_nwm_forcing_domain.py --active-gaps-only`, policy
`nldas2_active_gaps_preserve_inactive_v3` (also the CLI default). Every run audits all records,
requires zero missing active values and zero valid outside-domain values, and verifies that
inactive in-domain values are byte-identical before and after spatial repair. Missing inactive
values are intentional and do not fail validation. `--preserve-active` additionally rejects
active gaps rather than filling them; complete active fields are compared by SHA-256 as well.
The grid, run mask, and coordinate/provenance variables are not changed.

**Approved historical static-envelope cleanup (2026-09-12).** Previously overfilled
NLDAS-2-based retro files now have a separate, versioned final clipping policy,
`nldas2_seven_met_static_envelope_v4`. Its mask is
`forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc`: the NLDAS-2
rectangle intersected with the union of active NWM cells and sampled coverage of
the seven non-precipitation meteorological fields. The same mask is applied to all
eight fields; extra modern precipitation-only coverage is intentionally discarded.
It retains 10,860,309 cells, including all 10,315,371 active cells, and does not fill
inactive holes. Active completeness and unchanged retained values are required.
This avoids full reconstruction while accepting that retained old inactive donor
values cannot be distinguished from original coverage.

Campaign 4519428 applies this policy to all 8,766 retro files in 1979–2002, with
eight concurrent 12-CPU workers, 120 GB scratch per task, full read-back validation,
atomic replacement and resumable manifest/audit publication. Baseline leftovers
are not included. The post-2020-10-14 CNRFC rebuild remains separate and unchanged;
its envelope cleanup must be coordinated after reconstruction. The v3 generation
default above is deliberately unchanged during these running jobs. Do not apply
legacy rectangle-wide filling after v4 clipping. Full policy, mask provenance,
pilot results, scope, timing and recovery details are in
[the static-mask study and rollout record](forcing_mask_stability_study.md).

### Required GFS northern fallback in NRT

The [source-aware recent-NRT production path](nrt_gfs_operations.md) is implemented
and its integration acceptance passed (4551871). GFS is **mandatory**, including
when NRT is invoked by the repository cron commands. Cron installation itself is
still pending the separate operational reliability gates; this does not make GFS
optional. Disabling `config/nrt_gfs.toml` or removing its accepted activation
receipt blocks NRT scheduling instead of selecting the legacy HRRR-only path.

1. Select the primary meteorological source **per hour**: valid NLDAS-2 first,
   otherwise HRRR. Mixed-source days retain batched processing.
2. For HRRR-selected hours, acquire and validate indexed GFS short-forecast
   bundles before expensive baseline production. Prefer leads 1–6 from six-hourly
   cycles; at cycle boundaries use the preceding cycle's f006, not f000. The
   preceding cycle may supply a delayed bundle up to lead 12. These are short
   forecasts, not hourly GFS analyses.
3. Fill the gap outside native HRRR coverage within the approved static envelope
   with elevation-adjusted GFS meteorology from a consistent cycle. Preserve
   supported precipitation and use conservative GFS precipitation only as the
   final coverage fallback. The adopted native-donor repair cap is 40 km; it is
   not a substitute for GFS across the northern gap.
4. Apply eligible PRISM constraints, audit all eight final fields, and publish
   complete 00–23 UTC daily archives with per-hour primary-source and GFS cycle
   provenance. Sparse experimental patches are never model-ready LDASIN files.
5. When NLDAS-2 arrives, regenerate the affected baseline and constrained output;
   those hours no longer use or acquire GFS. Record replacement backlog and
   failures in `forcing/status/nrt-gfs/latest.json`.

If no acceptable GFS bundle is available for an HRRR-selected hour, fail the
affected update and preserve previously accepted output; do not silently publish
an incomplete northern domain. This policy does not add GFS to historical retro
or existing repair campaigns. See the [operational record](nrt_gfs_operations.md)
for acquisition, configuration, cache, replacement and publication details, the
[historical experiments](gfs_nrt_northern_gap.md) for method development, and
[reliability acceptance](nrt_reliability_acceptance.md) for current cron gates.

The blanket inactive masking policy `nldas2_rectangle_nwm_active_land_v2` is retired because
it also removed inland-water forcing. Jobs 4517940 and 4517941 were canceled; 41 published
days in January-April 1979 had already been filtered. Such files are explicitly rejected by
the v3 repair utility until their inactive values have been reconstructed from source data.
Job 4518020 reconstructs the four affected months after pilot 4518019 succeeds, reapplies
monthly PRISM with the existing acceptance thresholds, and restores only inactive in-domain
cells through `bin/restore_inactive_forcing_values.py`. Active forcing must remain byte-identical;
backups and reconstructed candidate data are retained under `forcing/work/restore-water-v3/`.
Reconstruction is not a claim of bit-for-bit recovery of erased inactive values.

For legacy v1 files, inactive values added by earlier rectangle-wide filling cannot be
distinguished from original valid values using the stored aggregate fill counts. Rule v3
preserves those existing values; removing only past synthetic inactive fills requires source
reconstruction. The v3 metadata certifies the present repair operation, not the absence of
historical inactive-cell filling. It also does not certify CNRFC recovery, which is recorded
separately by `forcing_recovery_policy`. No broad v3 archive sweep is scheduled while restoring
the v2-affected files and testing the new policy.

The `forcing_domain_policy` NetCDF attribute records provenance but is never accepted as proof of
completeness. `bin/repair_nwm_forcing_domain.py` rereads and audits file contents even when that
attribute is present, repairs any remaining gaps, and validates the temporary output before its
atomic replacement of the published file. Stable-baseline cleanup is fail-closed: it may proceed
only after the final file carries both the current domain-policy attribute and the versioned
`forcing_domain_content_audit` certificate written after the complete content audit. For dates
on or after 2020-07-01, production must also carry the CNRFC six-hour Stage-IV policy provenance
before final domain repair. This ordering prevents a later PRISM or precipitation transformation
from preserving stale repair metadata while reintroducing missing forcing values.

For existing files from 2020-10-14 onward, use
`bin/submit_post2020_forcing_rebuild.py` to rebuild the source baseline, reapply PRISM, and
perform final domain repair as one operation. It inventories existing retro and NRT files,
checks that both required PRISM windows exist, and submits a wet-day pilot (2026-04-12).
The remaining seven-day batches depend on successful completion of that pilot. Each worker
reserves 64 CPUs and 240 GB of scratch; eight simultaneous workers reserve 512 CPUs total.
Workers regenerate baseline halo days privately, retain all current publications until the
replacement batch passes PRISM acceptance and spatial audits, then replace calendar files
atomically. Rebuilt NRT baselines are retained for subsequent retrospective production;
temporary retro baselines remain on job scratch. A `forcing_recovery_policy` attribute and
per-day JSON log record identify successful publications. A failed task leaves any unpublished
days at their previous version; task failures must be resolved before declaring recovery complete.

This is a reconstruction using currently available source data, including revised PRISM inputs;
it does not recreate the exact source availability at the historical NRT issue time. The NRT and
retro directories remain separate. Missing six-hour Stage-IV data uses the non-Stage-IV
precipitation hierarchy inside CNRFC and still records the hourly Stage-IV exclusion policy.

#### Post-2020 retry and static-envelope rollout (2026-09-12)

Campaign 4517005 had 36 successful seven-day batches (252 published days), nine
failed batches, eight running and 255 pending at the retry checkpoint. Six failures
were hourly schema mismatches; three older failures were final publication-gate
rejections. The latter logs show accepted PRISM constraints and completed spatial
audits, but their scratch candidates are gone and the old exception did not name
the failed attribute. Their exact metadata failure is therefore not established.
Keep acceptance thresholds unchanged and verify through a gated real-data retry.

`precip_timing_source_id` is now always present in new precipitation outputs. It
is zero when there is no separate six-hour timing provenance. The daily archive
command also normalizes owned hourly staging files from already-running workers;
existing timing IDs and all physical fields are preserved. No published daily
file is edited by this normalization. All other schema differences remain errors.
Final calendar validation checks every timestamp against 00–23 UTC and logs named
failed checks plus their actual attributes. Missing CNRFC-policy metadata still
fails closed, including dry days without a six-hour constraint.

New rebuild submissions set `HYDRO_OPS_REBUILD_STATIC_ENVELOPE=1`. Their order is:

1. Rebuild source baseline with CNRFC hourly exclusion/six-hour reconciliation.
2. Apply accepted PRISM windows and publish a scratch 00–23 UTC candidate.
3. Repair active gaps, audit every record, and validate PRISM/CNRFC metadata.
4. Clip the candidate using the approved
   `forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc`.
5. Verify all eight fields: active cells complete, all retained values unchanged,
   and no valid values outside the envelope. Verify the permanent-copy checksum
   before replacement and update the manifest with its permanent file identity.

Mask hash: `d7ff57e87c3dfe709d3f165abfd289bc2761dc262f1be7379927a9fc1aafb4b5`.
Clipping adds no donors and performs no remapping or PRISM calculation. It still
requires a compressed rewrite and full readback; real-data canaries will measure
the extra time. The staged entry point does not extend the historical in-place
1979–2002 sweep: post-2020 candidates must be beneath the supplied job work root.
The clipper currently requires NLDAS-2-based input; HRRR-only/mixed NRT cases remain
separate and must not be relabeled to bypass this guard. Retained NRT baselines
remain pre-constraint v3 data; the final constrained outputs receive v4.

`bin/retry_post2020_forcing_campaign.py` snapshots accounting, cancels **pending
tasks only**, and excludes running/completed tasks from replacement. Its default
is a read-only plan; `--apply` records a frozen inventory and submits:

- **4520188**, two canaries: original indices 8 (2020-12-09–15, containing the
  December 13 publication rejection) and 45 (2021-08-25–31, containing the
  August 28 schema failure). Both started; successful completion is not yet claimed.
- **4520189**, the other 262 failed/pending batches, with `afterok:4520188` and
  at most eight concurrent workers. If a canary fails, these tasks stay blocked.

Each new task reserves 64 CPUs, 240 GB scratch and 48 hours. The two canaries
temporarily add 128 CPUs while the original eight workers finish. No healthy
running worker was canceled and no completed output was deleted. The 255 pending
original tasks were canceled to prevent duplicate publication. Existing completed
and still-running original batches are not retroactively v4-certified; their
outputs need a separately coordinated clipping-only pass, not another CNRFC/PRISM
rebuild. Persistent candidate audits are in
`forcing/work/post2020-static-envelope-audits/`; the retry inventory and commands
are in `forcing/work/post2020-retry-20260912T233610449730/submission.json`.
Regression verification: 242 tests passed, Ruff clean.

### Compression and archive layout

Domain-repair array logs are written to `forcing/logs/domain-repair-%A_%a.out`, and the
backlog submitter writes `forcing/logs/domain-repair-backlog-%j.out`. Historical root-level
`slurm-*.out` files can be consolidated with `python bin/archive_root_slurm_logs.py --apply`.
The utility excludes queued/running jobs and tracked files, verifies each archived file with
SHA-256, and removes only unchanged originals after the compressed archive is complete.
Archives and per-file checksum manifests live in `logs/archive/`; individual logs remain
recoverable with `tar -xzf ARCHIVE -C DESTINATION LOG_NAME`.

HRRR and Stage-IV GRIB conversions are stored as NetCDF4 using shuffle and DEFLATE level 2;
MRMS uses level 4 because its spatially sparse precipitation and quality fields gain materially
more storage reduction for a modest write-time cost. Conversion first produces a private
temporary NetCDF3 file, then `nccopy` creates a
compressed file. Publication is atomic and requires matching dimensions, variable types, and an
exact checksum of all unscaled stored values. `bin/compress_forcing_netcdf.py` applies the same
procedure in place to older files; already-compressed files are skipped, so interrupted migrations
are resumable.

Hourly source files remain the acquisition and late-revision staging unit. Final NWM baseline
forcing production, however, retains only daily collections with one shared coordinate grid.
Each daily task first stages its 24 hourly LDASIN records, atomically publishes and verifies
`YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1`, records cleanup state in the daily manifest, and then removes
the hourly files and manifests. The shorter timestamp distinguishes daily collections from hourly
NWM names; neither uses a `.nc` suffix. Readers continue accepting legacy `.nc` daily files.
This removes repeated latitude/longitude arrays and reduces metadata-server load while keeping
revision rewrites and failure recovery bounded. `bin/submit_forcing_days.py` enables daily-only
publication by default; `--keep-hourly` is a diagnostic escape hatch. Submitted arrays use
descriptive names such as `nwm-baseline-build-20250101-20251231` and
`nwm-baseline-repair-20250812-20250817`; `--job-name` can supply a more specific label (up to 64
characters). Each task requests 120,000 MB of node-local temporary disk by default (`--tmp-mb`),
because one full-CONUS day performs roughly 70-80 GB of scratch writes and CPU-only scheduling can
otherwise pack enough tasks onto one node to trigger CDO/NetCDF HDF write failures. The cluster's
300,000 MB `TmpDisk` nodes can schedule at most two default tasks per node, distributing larger
arrays across additional nodes. PRISM is already daily and
is not included in source-product compaction.

Raw HRRR, MRMS, and Stage-IV source artifacts are retained for a rolling 31-day revision and
recovery window. Older artifacts may be removed only when a verified daily archive records all
24 source files, contains 24 hourly records, and no partially deleted staging set is present.
The cleanup manifest records the daily-file checksum and completed removals so interrupted runs
are resumable. NLDAS-2 and PRISM do not have redundant persistent GRIB copies in this workflow.

Create daily collections with `bin/archive_forcing_daily.py`. Supported collections are `nldas2`, `hrrr`,
`mrms_pass1`, `mrms_pass2`, `mrms_quality`, and the `archive` or `realtime` Stage-IV hourly
stream. A day is published only when all 24 files have identical schemas, a common static grid,
strictly increasing times, and every archived time slice exactly matches its hourly source.
Publication uses an atomic rename and writes a JSON source manifest beside the daily file.
“Daily” describes file granularity, not data timestep. Daily chunks therefore remain in the same
product hierarchy as hourly data: `YYYY/MM/product.YYYYMMDD.nc`, while individual hourly files
remain below `YYYY/MM/DD`. No additional `daily` directory is used.
Daily MRMS chunks also use DEFLATE level 4. HRRR and Stage-IV daily chunks use level 2.
NLDAS-2 daily chunks use level 2 and are stored directly below the year as
`YYYY/NLDAS_FORA0125_H.AYYYYMMDD.020.nc`; their `YYYY/DDD` directories are hourly staging only
and are removed when empty after verified archival.

```bash
python bin/archive_forcing_daily.py hrrr --start 2025-01-01 --end 2025-01-31
python bin/archive_forcing_daily.py nldas2 --start 2025-01-01 --end 2025-01-31
python bin/archive_forcing_daily.py mrms_pass2 --start 2026-07-01 --end 2026-07-31
python bin/archive_forcing_daily.py stage4 --stream archive \
  --start 2026-07-01 --end 2026-07-31
```

Use `--jobs N` to process independent days in isolated worker processes. NetCDF/HDF5 access is
not thread-safe in this environment, so the implementation deliberately uses processes rather
than threads. Size Slurm CPU and memory requests to the worker count; the provided batch entry
point defaults to 32 workers and 64 GB. Reduce the worker count when source fields are larger or
the filesystem is under heavy load.

Under Slurm, large GRIB-conversion, CDO/remapping, and daily-NetCDF construction intermediates
use `/scratch/$SLURM_JOB_USER/job_$SLURM_JOB_ID`. Validated results are copied to a `.part` file
beside the permanent destination and atomically renamed, so readers never see partial products.
Network downloads and the final publication copy remain on permanent storage; moving those to
scratch would add an extra copy without accelerating the network transfer or improving atomicity.

A full-CONUS NWM smoke benchmark (`4445454`) aggregated 9.35 GB of hourly inputs in 4 minutes 32
seconds on a 16-CPU allocation. The verified daily collection occupied about 1.1 GB of allocated
storage and hourly cleanup completed successfully, an approximately 87% disk reduction. Against
the measured 11.66-minute daily production average, aggregation raises expected wall time to about
16 minutes per day, or roughly 39%. This cost is accepted because it substantially reduces both
capacity use and metadata load; remeasure under high concurrency because the shared filesystem is
the scaling limit. A later PRISM-constrained pass currently spends about eight additional minutes
extracting 24 records from compressed daily baselines to scratch (16:16 archive-input versus 8:30
hourly-input in the controlled test). Thus a baseline-plus-one-constraint pass is approximately
32 minutes rather than 20 minutes per day, about 60% slower end to end. Direct time-slice reads
from the daily NetCDF collection are the next optimization target; daily-only retention does not
preclude that improvement.

Hourly deletion is opt-in with `--delete-hourly` and is refused inside the configurable
`--minimum-age-days` window, which defaults to 31 days. `bin/cleanup_archived_forcing.py` removes
older hourly and raw artifacts only after the verified daily-archive checks above; its default is
a non-destructive report and deletion additionally requires `--apply`.

The primary forcing file follows the target NWM variable names, dimensions, units, coordinates,
fill value, and compression/chunking conventions. It contains exactly the expected time steps
for its publication interval. A sidecar provenance file or diagnostic group contains at least:

- Per-variable source IDs and QC bit masks.
- Input file identifiers, checksums or stable fingerprints, and product revisions.
- Analysis cycles and forecast hours where applicable.
- PRISM releases and correction diagnostics.
- Source and target terrain identifiers and elevation-adjustment diagnostics.
- Remapping-weight fingerprints and software versions.
- Production-method/configuration version and run timestamp.
- Counts of missing values, fallbacks, caps, rejected constraints, and source transitions.

Write data to a temporary path, validate the complete artifact, and publish by atomic rename.
Revisions create versioned artifacts or manifests; they are never overwritten without trace.

## Operational revision states

### Near-real-time

- Use the best eligible precipitation product currently available.
- Select NLDAS-2 per hour when available; otherwise use HRRR with required GFS
  northern-gap completion for meteorology and unsupported precipitation.
- Use the same spatial/source policy for the thermodynamic bundle and wind;
  GFS acquisition and completeness checks are mandatory for HRRR-selected hours.
- Apply early/provisional PRISM only to complete PRISM days.
- Rebuild affected intervals when MRMS Pass 2, Stage-IV revisions, NLDAS-2, or revised PRISM
  becomes available.
- Label the output mutable and report its latest complete hour.

### Stable retrospective

- Use final eligible precipitation sources and stable PRISM precipitation.
- Use NLDAS-2 as the large-scale baseline for temperature, pressure, humidity, radiation, and
  wind under the version-one policy, except documented gaps.
- Add calibrated HRRR mesoscale anomalies only for variable/region/season/regime strata that
  improve independent withheld validation; otherwise the anomaly weight is zero.
- Apply stable PRISM Tmin/Tmax.
- Publish only after complete-interval and cross-variable validation passes.

## Calibration and independent evaluation

The initial common study period is water years 2023-2025, from 2022-10-01 through 2025-09-30.
All seasons, elevation bands, climate regions, and day/night regimes must be represented.

Use either leave-one-water-year-out cross-validation or the initial split:

- WY2023-WY2024: development and calibration.
- WY2025: untouched independent temporal evaluation.

Where station coverage permits, also withhold spatial regions or station groups. Candidate
evaluation data include quality-controlled surface meteorological networks for temperature,
humidity, pressure, and wind; SURFRAD or comparable radiation observations; precipitation
gauges; and basin-scale hydrologic diagnostics. Calibration targets include lapse rate,
temperature range safeguards, RH tolerance, longwave emissivity/cloud-factor bounds, radiation
transition handling, and any future HRRR bias correction.

Assess NLDAS-2 and HRRR by variable, season, elevation, terrain class, region, and hour of day.
Metrics include bias, MAE, RMSE, correlation, diurnal amplitude/phase, distribution tails,
source-transition jumps, and relevant conservation or physical-consistency errors. A finer grid
is adopted as the preferred source only when independent evaluation demonstrates material
benefit without unacceptable discontinuities.

HRRR calibration is version-aware. The primary homogeneous archive begins with the first full
UTC day under HRRRv4, 2020-12-03; the 2020-12-02 transition day is excluded from homogeneous
training. Major HRRR versions must be recorded as separate strata and must not be pooled as one
stationary record. Backfilling older versions can support rare-event sensitivity work, but is
not needed to extend the core retrospective forcing because NLDAS-2 already supplies the long
baseline. The HRRRv4 backfill therefore has first priority.

The principal comparison is (A) elevation-downscaled NLDAS-2, (B) bias-corrected HRRR, and
(C) NLDAS-2 plus HRRR mesoscale anomalies. Evaluation uses calibration/withheld splits and
independent stations and radiation networks, stratified by region, season, elevation, terrain,
and event type. Hydrologic and snow-state verification is required in addition to meteorological
scores before a refinement becomes operational.

The daily PRISM AN archive begins 1981-01-01. Historical backfill therefore acquires `ppt`,
`tmin`, `tmax`, and derived `tmean` from that date onward at 4-km resolution. Stable historical
grids are downloaded once, while the normal updater continues checking the rolling six-month
mutable window.

For 1979-1980, NLDAS-2 remains the hourly source and PRISM AN monthly `ppt`, `tmin`, and `tmax`
provide lower-frequency constraints. This is a distinct, explicitly labelled forcing tier; a
monthly grid must never be copied or interpolated into synthetic daily PRISM grids. Monthly
precipitation constrains the sum of the hourly sequence over each calendar month while retaining
NLDAS-2 event timing. Monthly `tmin` and `tmax` constrain, respectively, the monthly mean of the
daily hourly minima and maxima. A spatially varying affine shift/range correction preserves each
cell's NLDAS-2 submonthly and diurnal evolution. Ratio/scale bounds, missing-target masks, and
correction diagnostics are required just as for the daily constraint. The resulting provenance
must identify `prism_constraint_frequency=monthly` so it cannot be confused with the daily
1981-present retrospective product.

An overlap check using all twelve months of 1981 found that monthly PRISM precipitation agrees
with the sum of daily PRISM to about 0.002 mm RMSE. Monthly temperature is not merely the
arithmetic aggregation of the downloaded daily grids: monthly-versus-daily-derived RMSE ranges
from about 0.45-0.60 degC for Tmin and 0.37-0.63 degC for Tmax. The monthly temperature grids are
therefore treated as authoritative independent monthly targets rather than reconstructed daily
constraints. This validation should be rerun if PRISM changes either archive version.

The implemented historical-month processor is `bin/produce_prism_constrained_month.py`. It uses
a two-pass, daily-archive workflow: the first pass accumulates monthly precipitation and the
monthly means of each day's hourly Tmin/Tmax; the second applies one bounded precipitation
factor and one affine temperature correction per grid cell while copying each daily archive.
Relative humidity is preserved through the temperature adjustment by recomputing `Q2D`, and
`LWDOWN` is reconstructed with the existing Cosgrove atmospheric-emission factor. Thus no full
month of CONUS hourly fields is held in memory and no intermediate hourly files are published.
The output remains one suffix-free `YYYYMMDD.LDASIN_DOMAIN1` file per calendar day, accompanied
by one monthly diagnostic file. Every output records `prism_constraint_frequency=monthly` and
the three source grids.

Monthly publication has four fractional precipitation gates plus an hourly-extreme guard. The fractions of constrained
PRISM cells that are numerically unconverged, materially unresolved, ratio/depth capped, or have
a wet PRISM target over a dry baseline must not exceed 0.5%, 0.5%, 2%, and 0.5%, respectively.
The unresolved fraction is evaluated from `abs(residual) / max(PRISM, 1 mm)` using the solver
tolerance. A cell excluded from numerical iteration because a correction is physically
infeasible therefore cannot disappear from the publication decision. Corrected precipitation
must also remain at or below 300 mm in every grid cell and hour. All observed values and limits
are stored in the monthly diagnostics and final daily global attributes.

An initial January 2021 diagnostic based on the modern combined precipitation baseline failed
the gates. Only
0.2365% of constrained cells carried the numerical `NOT_CONVERGED` flag, but 7.4470% remained
materially unresolved, 7.8108% encountered the 10x correction bound, and 1.0099% had a wet target
over a dry baseline. Uncapped feasible cells reproduced PRISM well (0.036 mm RMSE), while capped
cells delivered a median of only 3.5% of their target. A provenance audit showed that these cells
were dominated by MRMS-selected near-zero precipitation. That modern combined-source result is
therefore retained as a rejected test, but it is not representative of the NLDAS-2-only
1979-1980 baseline.

The production-representative control remapped NLDAS-2 alone for January 2021. With a 10x bound,
0.5178% remained unresolved, narrowly exceeding the 0.5% gate. A 100x bound passed every gate:
0.0006% was numerically unconverged, 0.2280% unresolved, 0.5218% capped, and 0.2122% had a wet
target over a dry baseline. Although the allowed ceiling is 100x, 99.9% of finite correction
factors were below 13.9x. The smallest tested passing bound, 100x, is therefore used together
with the independent 300 mm/hour publication guard. The NWM-to-PRISM conservative weights also
exclude inactive NWM cells at generation time; the accepted operator contains no links from
inactive source cells.

NLDAS-2 begins at 13 UTC on 1979-01-01. January 1979 cannot form a complete calendar-month
constraint and is intentionally excluded; unconstrained daily baseline production begins on
1979-01-02, and monthly constrained production covers 1979-02 through 1980-12.

For example, after unconstrained daily forcing exists below `forcing/outputs/conus/baseline/hourly`, run:

```bash
python bin/produce_prism_constrained_month.py \
  --year 1979 --month 2 \
  --complete-root forcing/outputs/conus/baseline/hourly \
  --output-root forcing/outputs/conus/retro/hourly \
  --maximum-ratio 100
```

### Implemented experimental hybrid

The target-grid hybrid machinery is implemented but disabled by default. Both sources first
pass independently through their native-grid normalization, terrain adjustment, vector
rotation, and direct NWM-grid remapping. A NaN-aware 33-cell box smoother then estimates the
coarse HRRR component on the 1-km grid, and the residual supplies the candidate mesoscale
anomaly. The initial 33-km window is configurable and must be calibrated; it is not a claim that
all NLDAS variables have one exact effective resolution.

The implemented anomaly spaces are:

| Field | Anomaly space and reconstruction |
|---|---|
| Temperature | additive kelvin anomaly |
| Pressure | additive log-pressure anomaly, then exponentiation |
| Humidity | additive bounded relative-humidity anomaly, then recompute specific humidity |
| Longwave | additive log anomaly of the Cosgrove longwave factor, then reconstruct from final T/P/RH |
| Shortwave | additive clear-sky-index anomaly using target solar geometry; preserve night zero |
| Wind | additive earth-relative U and V anomalies |

Every weight is constrained to `[0, 1]`, and variable-specific anomaly caps set QC bits when
invoked. Output provenance records the baseline and HRRR components, all weights, and smoothing
window. A hybrid hour requires both sources; it fails explicitly rather than silently dropping
an enabled refinement. With all weights at their default zero, the normal NLDAS-first workflow
is unchanged and does not incur the second HRRR remapping.

Real-overlap testing found rare HRRR cells with diagnosed RH between 110% and 116% when the
common over-liquid-water convention is applied at all temperatures (3 of about 1.9 million
finite source cells exceeded 110% in the initial test hour). HRRR therefore uses a provisional
20% supersaturation tolerance: accepted excursions are clipped to saturation and flagged.
Larger isolated HRRR outliers are masked and the hybrid retains NLDAS-2 locally rather than
rejecting the national hour. NLDAS-2 retains the strict 10% whole-input threshold. This
source-specific policy is subject to calibration review.

For development-only experiments, `bin/produce_forcing_hour.py` accepts separate thermodynamic,
radiation, and wind weights. For example:

```bash
python bin/produce_forcing_hour.py YYYYMMDDHH ... \
  --hybrid-temperature-weight 0.25 \
  --hybrid-pressure-weight 0.25 \
  --hybrid-humidity-weight 0.25 \
  --hybrid-longwave-weight 0.25 \
  --hybrid-shortwave-weight 0.25 \
  --hybrid-wind-weight 0.25 \
  --hybrid-window-cells 33
```

These values are diagnostic, not production recommendations. PRISM temperature reconciliation
must operate on the complete 24-hour hybrid sequence so daily extrema and thermodynamic
reconstruction remain correctly ordered. The single-hour command therefore rejects simultaneous
hybrid weights and a precomputed final-temperature constraint.

The implemented daily two-pass driver uses the half-open PRISM day
`[D-1 day 12:00 UTC, D 12:00 UTC)`. It first produces all 24 unconstrained hybrid hours. It then
remaps PRISM Tmin/Tmax to the NWM grid, applies the affine correction to the complete hybrid
temperature curve, and revises each hour. Relative humidity is diagnosed from the preliminary
`T2D/Q2D/PSFC`; final `Q2D` is recomputed at corrected temperature while preserving RH, and
`LWDOWN` is reconstructed while preserving the hour's Cosgrove longwave factor. Pressure is not
changed by PRISM because the daily surface-temperature constraint does not imply a change in
atmospheric column mass. Precipitation is composited afterward, and the 24 complete hourly files
are also written as one checksum-verified daily LDASIN collection.

`slurm/produce_hybrid_prism_hour.py` supplies the parallel preliminary-hour array and
`bin/produce_prism_constrained_day.py` performs the dependent constraint, reconstruction,
precipitation, and daily publication stage.

The first complete full-CONUS test used PRISM day 2023-01-22 and experimental 0.25 HRRR
weights. It published 24 complete hourly files plus a 24-record daily file. About 7.36 million
cells received an unconstrained affine Tmin/Tmax match; about 135 thousand valid-PRISM cells
invoked the configured temperature-range scale cap, so their extrema intentionally do not match
PRISM exactly. Cells without a valid PRISM constraint retained the hybrid baseline and were
flagged. This confirms the guardrails operate as designed; the 0.25 weights and scale bounds
remain experimental rather than calibrated production values.

A January 2026 scaling test separated precipitation production from PRISM temperature
revision. Reusing complete hourly LDASIN files reduced a temperature-only PRISM revision from
82 minutes to 11 minutes while preserving all precipitation fields exactly. Daily batched
precipitation processing then normalized 24 hours per available product and applied each static
CDO operator once per product, rather than once per product per hour. For PRISM day 2026-01-20,
the four precipitation candidates plus MRMS quality required five remap calls instead of 120,
completed in 8 minutes 28 seconds, and reproduced all 24 hourly `RAINRATE`, source ID,
confidence, QC, and mask fields exactly. Production integration should retain an hourly fallback
for days whose candidate availability changes within the 24-hour window.

The operational constraint driver now combines both corrections in one scratch-backed task and
publishes the final daily LDASIN collection directly. A full-CONUS test for PRISM day 2026-07-15
completed in 8 minutes 30 seconds with 12 allocated CPUs, including PRISM Tmin/Tmax preparation,
temperature/humidity/longwave reconstruction, bounded precipitation reconciliation, diagnostics,
and atomic daily publication. Its sample hours matched the independently produced temperature
and precipitation reference results. The driver accepts either persistent hourly LDASIN files or
daily baseline collections; for the latter it locates records by their actual NetCDF time values,
extracts them only to node-local scratch, and removes them at task completion.

A rolling scheduler classifies PRISM days as early, provisional, or stable and checks that the
PRISM inputs and time-addressable baseline records exist. PRISM's physical 1200-1200 UTC
constraint window remains an internal scientific interval, never a production file boundary.
`bin/submit_prism_calendar_batches.py` groups missing UTC dates into bounded contiguous batches.
Each worker keeps only two adjacent PRISM constraint windows on node-local scratch, publishes the
corresponding `00-23 UTC` calendar file atomically, validates it, and advances by one day. Scratch
cleanup removes the internal windows when the job exits. Early and provisional output is retained
in the `nrt` stream; stable output is published independently in `retro`. Separate
concurrency-bounded SLURM arrays and version-controlled cron entries prevent stable publication
from replacing the near-real-time record.

The canonical on-disk hierarchy is:

```text
forcing/outputs/conus/baseline/hourly/        unconstrained reusable baseline
forcing/outputs/conus/nrt/hourly/             retained early/provisional operational record
forcing/outputs/conus/retro/hourly/           stable daily or historical-month retrospective record
```

Both published streams use `YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1` below their root. Stream schedulers
derive these roots from `--stream`; an explicit override must end in `/nrt` or `/retro` as
appropriate, and both the submitter and worker reject crossed paths. Retrospective publication
never searches the NRT tree for an output to replace. The baseline tree is source material, not
a third published quality tier.

Baseline retention is transitional. After stable retrospective output is accepted, the baseline
archive is deleted to avoid retaining a second multi-terabyte copy. Cleanup is deliberately
coverage-aware: parallel calendar publishers never delete baseline input because adjacent batches
temporarily share boundary dates. After the complete controller array converges, a separate serial
audit removes UTC baseline day `D` only when the stable, accepted retro file for that date contains
all 24 records and no active controller can still depend on it. A failed batch is resumed by
rerunning the idempotent submitter, which forms new contiguous batches from the remaining baseline
inventory. For the 1979-1980 monthly
method, a baseline month is removed only after its monthly diagnostic is accepted and every
calendar-day retro file is present. Missing, provisional, rejected, or partial retro coverage
always retains the baseline. The independent audit/cleanup command remains
`bin/cleanup_stable_baseline.py`.

Operational scheduling separates prompt updates from deeper revision repair:

`bin/update_nwm_forcing.py` is the single operational entry point. It records a run manifest,
submits source refreshes, attaches baseline work to those downloads, and launches the calendar-day
PRISM continuation only after its prerequisites leave the queue. NRT cycles retain baseline.
Monthly retro cycles build only the three-day dependency halo around missing stable targets and
attach a serial stable-baseline cleanup after successful PRISM completion. Existing accepted retro
dates are terminal and do not cause baseline reconstruction.

```bash
python bin/update_nwm_forcing.py --cycle six-hourly
python bin/update_nwm_forcing.py --cycle daily
python bin/update_nwm_forcing.py --cycle monthly-retro
```

An explicit historical retrospective window uses the same baseline → stable PRISM → cleanup
dependency chain without refreshing already archived external sources:

```bash
python bin/update_nwm_forcing.py --cycle monthly-retro \
  --start 1982-01-01 --end 1982-12-31 --skip-source-refresh
```

Explicit windows are restricted to retrospective mode. Their date-specific job names allow a
historical backfill to coexist with a non-overlapping retrospective array already in progress.
Adjacent years may be queued safely with `--dependency afterany:JOB_ID`, where `JOB_ID` is the
preceding year's cleanup job. The later baseline audit then runs after the shared boundary is
released and regenerates the boundary halo if cleanup removed it.
The continuation is a bounded convergence controller rather than a one-shot submission. After
each array leaves the queue it audits accepted calendar outputs, rebuilds only missing D-1/D/D+1
baseline dependencies, and resubmits only unresolved PRISM dates. Four attempts are allowed by
default. The cycle manifest records each stage, attempt, job ID, and unresolved-date sample.
Stable-baseline cleanup runs only after every requested retro day passes the final content audit;
otherwise the manifest ends in a machine-readable blocked state and baseline is retained.

Long historical spans use `bin/update_nwm_forcing_multi_year.py`. It shards the baseline calendar
at the cluster's 1,001-task array limit, divides one global baseline worker budget across those
non-overlapping shards, and launches one convergence controller for the entire span. PRISM has a
separate global worker limit. Because cleanup occurs once after the full span validates, adjacent
year boundaries cannot be deleted while a neighboring year still needs them.
Workers are apportioned by shard-day count so unequal final shards finish at approximately the
same time. The production defaults are 42 baseline workers at 12 CPUs each (504 CPUs) and 32
PRISM workers at 12 CPUs each (384 CPUs). Multi-year cleanup defaults to `deferred`; completed
blocks report `complete_pending_cleanup` and retain baseline until all concurrently processed
neighboring blocks have converged and a global cleanup audit is run.

Baseline arrays are submitted by a lightweight staging controller. Every array element counts
against SLURM's `MaxSubmitJobsPerAccount` limit, so the controller records each accepted shard in
the cycle manifest and retries later shards as quota becomes available. Submission is therefore
resumable after partial acceptance, and convergence is released only after every baseline shard
has been submitted and finished.

The next, not-yet-activated scheduling design and its under-one-hour acceptance
criteria are documented in [incremental NRT operations](incremental_nrt_operations.md).
The following remains the existing schedule until that rollout passes real-data tests.

- Run a 10-day NRT scan every six hours. This covers Stage-IV regeneration during its first day
  and at approximately 1, 3, 5, and 7 days, plus the usual 3-4-day NLDAS-2 latency and PRISM's
  first and five-day runs.
- Run a 200-day NRT scan once daily. This covers PRISM's full rolling six-month mutable period,
  with a buffer for delayed downloads and missed schedules.
- Run a 45-day retrospective scan monthly on the 18th, after PRISM's usual mid-month modeling
  cycle. The scheduler offsets this window to end 183 days behind the current date, so it targets
  newly stable days rather than recent NRT dates. Stable outputs that are already current are
  skipped.
- Periodically audit the complete retrospective inventory separately. This is a completeness
  check, not a reason to regenerate unchanged stable forcing.

PRISM daily grids are produced about one day and five days after the target day, then approximately
monthly through the final six-month run. They are daily products, but their later provisional and
stable updates follow this monthly modeling cycle. These rules are encoded in the scheduler and
the canonical cron file rather than relying on operator memory.

The complete archive-only operational path was subsequently tested for 2026-07-15. Two verified
calendar-day baseline collections were produced in parallel in 5 minutes each, then exposed to
the scheduler in an isolated root containing no hourly LDASIN files. The scheduler selected
exactly the one complete PRISM day and submitted job 4439541_0 with the expected provisional
revision. The initial implementation extracted 24 compressed records to scratch and completed in
16 minutes 16 seconds on 12 allocated CPUs with 20.3 GB peak memory.

Job 4445630 repeated that full-CONUS case by passing each daily archive and its NetCDF time-record
index directly through temperature adjustment, precipitation reconciliation, and final daily
publication. It completed in 10 minutes 57 seconds on 12 CPUs with about 3.4 GB peak memory: 5
minutes 19 seconds (32.7%) faster than extraction and only 2 minutes 27 seconds slower than the
8-minute-30-second hourly-input reference. Dimensions, variables, and time coordinates matched;
all eight forcing fields matched exactly over three separated 256-by-256 windows for all 24
hours; and every complete PRISM diagnostic field matched exactly. Synthetic tests additionally
verify exact arbitrary-record selection and publication. Direct indexed access is therefore the
default; `--archive-access extract` remains available as a diagnostic fallback.

## Implementation phases

1. Complete and inventory the common NLDAS-2, HRRR, and PRISM archive; acquire static native
   terrain for each grid definition.
2. Implement shared source adapters, unit/time normalization, masks, grid fingerprints, and
   provenance manifests.
3. Generate and validate direct native-to-NWM remapping weights.
4. Implement thermodynamic primitives: saturation vapor pressure, RH conversion, lapse-rate
   temperature, hydrostatic pressure, and Cosgrove longwave adjustment.
5. Implement the coupled temperature-pressure-humidity-longwave pipeline and PRISM temperature
   constraint.
6. Implement shortwave remapping, solar-geometry QC, wind rotation/remapping, and source
   transition diagnostics.
7. Implement precipitation compositing and PRISM reconciliation according to its detailed
   workflow.
8. Assemble complete NWM forcing files with cross-variable QC and atomic publication.
9. Run calibration and withheld validation; freeze version-one parameters and decision rules.
10. Add rolling near-real-time revision and stable retrospective production schedules.

## Current implementation status

The design above is the production target. As of 2026-08-25, the following foundations are
implemented and tested:

- Extraction of the NWM target grid and SCRIP description from the sample LDASIN file.
- Chunked sampling of GMTED2010 onto the active NWM grid, producing the reusable target
  elevation file.
- Acquisition of NASA's authoritative NLDAS mean-elevation grid.
- Extraction of native HRRR `HGT:surface:anl` and preservation of its complete GRIB grid
  description. Inspection confirms that HRRR U/V records are grid-relative.
- Structural validation and coordinate fingerprinting for individual NLDAS-2, HRRR, and PRISM
  files.
- Fast date-range completeness reports for NLDAS-2, HRRR, and PRISM variables.
- Lazy normalized source readers with canonical variable names and units. HRRR longitudes are
  normalized to `[-180, 180)` while its wind orientation remains explicitly grid-relative.
- Unit-tested fixed-lapse-rate temperature, hydrostatic pressure, exact humidity/RH conversion,
  PRISM Tmin/Tmax curve adjustment, vector rotation, and Cosgrove longwave primitives.
- Direct NLDAS-2, HRRR, and PRISM bilinear weights plus conservative precipitation weights,
  each with a source/target grid-fingerprint manifest. HRRR conservative weights use native
  GRIB geometry because the wgrib2 NetCDF conversion lacks cell corners.
- Full-grid NLDAS-2 and HRRR temperature remapping smoke tests on the 17,694,720-cell NWM grid.
- A coupled hourly temperature-pressure-humidity-longwave processor that moves the four-field
  state to a common reference elevation before one bilinear remap, restores target elevation,
  optionally accepts final PRISM-constrained temperature, reconstructs humidity and longwave,
  applies the NWM mask, and writes provenance/QC diagnostics atomically. A full-grid NLDAS-2
  smoke test completed on 10,762,839 active cells, and a projected-grid HRRR smoke test
  completed on 10,322,743 covered active cells.
- An hourly shortwave/wind processor that rotates HRRR grid-relative vectors before bilinear
  remapping, preserves NLDAS-2 earth-relative vectors, enforces target-cell nighttime shortwave
  zeros, pairs missing wind components, and writes time/provenance/QC diagnostics atomically.
  Full-grid NLDAS-2 and HRRR smoke tests completed with no positive shortwave below the
  configured solar horizon.
- Exact-time whole-hour source selection that prefers structurally valid NLDAS-2 and falls back
  to HRRR without mixing thermodynamic, radiation, or wind products within an hour.
- Experimental NLDAS-2-plus-HRRR target-grid hybrid transformations for all non-precipitation
  fields. Zero weights reproduce the NLDAS-2 baseline; nonzero weights and the smoothing scale
  remain uncalibrated and are not enabled in routine production.
- Runtime rejection of stale bilinear weights using source and NWM-grid fingerprints, plus
  terrain coverage/range validation and SHA-256 provenance for source and target elevations.
- Elevation-aware PRISM Tmin/Tmax preparation and guarded 24-hour affine reconciliation. A real
  PRISM day remapped successfully with no Tmin/Tmax inversions; incomplete PRISM coverage
  retains the hourly baseline and is flagged.
- Atomic seven-field LDASIN assembly matching the sample variable names, dimensions, bounds,
  units, and fill value. This reusable intermediate deliberately marks `RAINRATE` pending until
  the precipitation component is atomically added; a real NLDAS-2 hour produced successfully.
- Quality-aware precipitation adapters and deterministic target-cell selection across MRMS
  Pass 2/Pass 1, Stage-IV archive/realtime, NLDAS-2, and HRRR. Negative MRMS no-coverage codes
  remain missing, and outputs retain confidence, source IDs, QC, and exact hourly bounds.
- Fingerprinted MRMS and Stage-IV conservative weights and MRMS-quality bilinear weights. A
  real five-source hour filled all active NWM cells without negative precipitation.
- Atomic complete eight-variable LDASIN publication, resumable range production, rolling SLURM
  arrays, per-hour manifests, and a version-controlled six-hour cron submission entry.
- Reusable continuous/stratified metrics and MRMS threshold-sweep scaffolding. These tools do
  not constitute calibration results while the archive and independent references are incomplete.
- Bounded conservative PRISM daily precipitation reconciliation using a sparse NWM-to-PRISM
  operator, with explicit zero/missing cases, synthetic-timing flags, correction caps,
  residuals, and atomic revision-labeled output.
- Deterministic whole-storm/day calibration-withheld splitting, categorical precipitation
  scores, and regional Stage-IV override sweeps. Rule selection uses calibration samples only;
  the selected rule is subsequently scored on withheld samples.
- Direct daily publication of the combined PRISM temperature and precipitation corrections,
  including daily-baseline input support that avoids retaining persistent hourly LDASIN files.
- Revision-aware rolling PRISM scheduling with completeness, staleness, revision-transition,
  active-job, and bounded-concurrency guards plus a version-controlled cron entry.

Generated static and remapping artifacts live below `forcing/static` and are intentionally ignored
by Git; the scripts, tests, and documentation required to recreate them are tracked.

The following production components remain incomplete and must not be inferred from the
implemented primitives:

- Generation and full-grid validation of the reverse NWM-to-PRISM conservative operator,
  followed by an archive-scale PRISM reconciliation run.
- Assembly of genuinely independent gauge and basin samples, execution of the implemented
  calibration/withheld workflow, transition diagnostics, and freezing of production thresholds.

The WY2023-WY2025 source backfill is still running. Implementation and synthetic/short-window
testing can continue during the backfill, but calibration claims require a complete audited
archive.

## Operational PRISM cutover validation (November 6, 2026)

Cron remains paused. The repaired archive does not imply that all operational
entry points have adopted the repair strategy. The coupled window producer now
accepts `support-aware-v2` (CLI or `HYDRO_OPS_PRISM_STRATEGY`) for controlled tests:
bounded 200-iteration fitting, the validated conservative fallback, and optional
NLDAS timing (baseline → NLDAS → uniform). It retains the existing coupled
temperature/humidity/longwave corrections. Unselected jobs retain their existing
strategy until acceptance. NRT window-cache signatures include the strategy, so
legacy windows cannot satisfy a v2 cache lookup.

The adapter may write only beneath project `forcing/work/` or the current SLURM
job's scratch directory; it cannot write directly to production streams. The
integration test freezes PRISM inputs, assembles 00–23 UTC files, applies and
fully verifies the static envelope for all eight fields, reduces an interval-end
daily summary, and subsets parent-first to all four domains. Live production
files and revision acknowledgements are untouched.

Jobs **5188418** (October 25–26 NRT, 76m50s) and **5188419** (April 12–13
stable Retro, 69m16s) passed this path. Five PRISM windows were accepted strictly;
October 27 used the accepted bounded fallback. Reports:
`forcing/work/operational-prism-revision/<job>/`.
These are integration tests, not whole daily/monthly cron-cycle acceptance.
Tests at submission: 702 passed, four skipped.

Stage two uses `slurm/test_operational_prism_revision.sh --case monthly
--full-month-nrt`: stable April PRISM revises two isolated NRT calendar days,
rebuilds all three affected interval-end daily summaries (including the preceding
day), recomputes the complete April monthly summary using unchanged canonical
daily records for the other days, and propagates changed hourly/daily/monthly
files to all four domains. Unchanged context is linked read-only; production is
not modified. Repeat summary/subset calls must skip without rewriting. Isolated
acknowledgement tests reject incomplete and superseded work, and accept only
exact-source completed work. This is a monthly revision propagation test, not
a rebuild of all April hours or acceptance of the live cron/replay executor.
April NRT provides a complete month and retained baselines; March Retro baseline
rebuilding remains a separate future gate.
Stage-two job **5188554** was submitted with 64 CPUs, 120 GB requested scratch,
and a four-hour limit after 712 tests passed (four skipped). Its acceptance report
is `forcing/work/operational-prism-revision/5188554/acceptance.json`.
It passed in 1h13m41s: three affected interval-end days, the complete April
monthly summary, and all four domains were produced in isolation. Monthly and
all 24 subset repeat calls skipped unchanged outputs; exact-source/incomplete/
superseded acknowledgement checks passed. No chunk-layout fallback was logged.

The next missing-baseline gate is `slurm/test_prism_replay_baseline.sh`.
It regenerates March 11–13, 2026 using the existing native-source baseline
engine, into `forcing/work/operational-prism-replay/JOB/`, never the live baseline
archive. Each day deliberately raises an exception after an accepted baseline
write; retry must reuse that exact file, then the durable stage checkpoint must
skip a second retry. Source signatures and artifact identities are pinned, and
changed signatures/artifacts fail closed. This is an injected interruption test,
not a SLURM/node-failure test or complete live revision replay acceptance.
`prism_replay_execution.run_stage` provides the isolated checkpoint primitive;
it does not acknowledge the live queue or enable publication. Baseline rebuilding
must pass before connecting these stages to complete PRISM/summary/subset replay.
Job **5190262** runs this gate with 64 CPUs, 120 GB requested scratch, and a
six-hour limit. Tests before submission: 719 passed, four skipped. Its report is
`forcing/work/operational-prism-replay/5190262/acceptance.json`.

The v2 timing diagnostic now uses the RAINRATE chunk layout rather than fixed
128×128 spatial chunks. Stage-one 256×256 coupled windows had fallen back to
decoded calendar assembly because of that diagnostic mismatch. Unit tests cover
matching chunks for both ordinary and compact v2 output; stage-two logs provide
the production-scale check. No numeric algorithm is changed by this adjustment.

Monthly revision discovery now also writes immutable replay plans. Each plan
pins all six PRISM inputs for the two affected windows, records three required
baseline dates, daily/monthly summary halos, and domain ordering. Missing
baselines must be rebuilt; already-constrained Retro files must not be substituted
as unconstrained temperature baselines. Superseded queued signatures block replay.
The planner does not clear pending work or enable automatic production writes.

Remaining activation gates: integration of
the final-envelope publisher on every live path; baseline-rebuild/revision replay
execution with exact-source acknowledgement;
and end-to-end daily/monthly cycle tests including retries/no-op repeats. Keep
automatic replay disabled and do not reinstall cron before those gates pass.

### Persistent replay tests (November 6, 2026)

Baseline recovery job **5190262** passed in 53m20s. The next isolated tests are
**5192523** (April 12 NRT revision) and **5192524** (March 12 Retro revision),
using `slurm/test_isolated_revision_replay.sh --case daily|monthly`. Each requests
64 CPUs, 120 GB scratch, and six hours, without NRTRES. The cases can run in
parallel because all writes are confined to their own workspaces.

Each test rebuilds three baseline days, creates two PRISM windows, assembles one
00–23 UTC day, and fully verifies its static envelope. It deliberately exits
after accepted hourly output, then resumes in a fresh process to create both
affected interval-end daily summaries, the complete monthly summary, and all
four subdomain products. Only then may its **isolated** revision queue be
acknowledged against the exact pinned source signatures. A third invocation
must reuse all accepted stages without rewriting outputs. Reports are under
`forcing/work/isolated-revision-replay/JOB/`: `interruption.json`,
`acceptance.json`, `noop.json`, and `stages.json`.

These tests snapshot already-downloaded PRISM inputs; they do not exercise live
network acquisition, the newest HRRR/GFS extension, live publication, or live
queue acknowledgement. The NRT case uses historical stable PRISM. Unchanged
monthly context and hourly boundary files are linked read-only. The deliberate
interruption tests a completed-stage boundary, not arbitrary node loss during
window creation.

Mocked scheduler tests verify source/baseline/continuation dependency ordering,
latest-hour-before-revision ordering, same-stream overlap rejection, and
submission-lock contention. A separate checkpoint test rejects simultaneous
stage writers. These are not proof of cross-cycle live publication safety.
Before submission the full suite passed **726 tests, four skipped**; Ruff and
shell syntax checks passed. Cron and automatic live replay remain disabled
pending the remaining activation gates above.

Both initial replay jobs stopped at the first PRISM window: **5192523** after
43m55s and **5192524** after 52m06s. All three baseline days in each workspace
passed, but the test's temporary `windows` path violated the stream-root guard.
No intentional interruption, downstream summaries, or acknowledgement occurred.
The harness now validates `windows/<stream>/hourly` and
`calendar/<stream>/hourly` before expensive processing. `--resume-job JOB`
reuses an existing isolated checkpoint while scratch follows the new SLURM job;
changed pinned sources/artifacts still fail closed. Resume IDs cannot escape
the isolated workspace root.

Retries **5194799** (NRT, `--resume-job 5192523`) and **5194800** (Retro,
`--resume-job 5192524`) retain the same resources and test stages. Accepted
baseline identities and pinned context were rechecked unchanged before
submission. Reports remain in the original workspaces; retry logs use the new
job IDs. Sixteen focused regression tests, Ruff, shell syntax, and diff checks
passed. These retries are submitted, not yet accepted.

The retries subsequently **passed**: 5194799 in 1h00m56s and 5194800 in
59m01s, excluding earlier baseline rebuilding. Both accepted all 13 stages,
resumed after the intentional hourly-stage interruption, acknowledged only their
isolated queues after downstream success, and repeated without data rewrites.

### Bounded live publication bridge

`bin/publish_bounded_prism_replay.py` promotes an already accepted isolated
replay, not arbitrary files. It requires explicit `--workspace`, `--stream`, and
`--day`. Its default is a read-only preflight, for example:

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/publish_bounded_prism_replay.py \
  --workspace forcing/work/isolated-revision-replay/5192523 \
  --stream nrt --day 2026-04-12
```

The initial bridge deliberately supports one **existing** calendar day away
from month boundaries, two affected interval-end daily summaries, and one
complete month. It verifies all accepted stage identities, successful unchanged
repeat, six current PRISM signatures, unchanged context, and exactly 20 regular
production destinations across CONUS and the four subdomains. Both accepted
NRT/Retro workspaces passed this preflight. It does not refresh sources, build
new candidates, extend the latest hour, or enable automatic revision discovery.

Explicit `--publish` additionally requires SLURM. The entry point is
`slurm/publish_bounded_prism_replay.sh`; it forwards arguments without adding
`--publish` and repeats the invocation to verify the unchanged path. Publication
holds coordinator/NRT-cycle/rain/month/subset locks and retains hard-linked
original data and metadata under the replay's `publication/originals/`.
The hourly transfer uses checksum-verified recoverable intent. Summaries are
reduced from **canonical published inputs**, rather than copying isolated
summaries with private source paths; subsets are then rebuilt parent-first with
canonical source receipts. The CONUS manifest/NRT receipt describes the coupled
replay (not a rain-only repair) and marks downstream work pending until complete.

Only after all downstream stages and a final source check can the publisher
acknowledge an existing live queue event whose signatures exactly match the
accepted inputs. It does not invent events or acknowledge unrelated days.
An accepted repeat verifies recorded output identities and makes no writes.
Changed inputs or destinations fail closed. A downstream interruption normally
resumes from recorded progress; a crash between replacing a downstream file and
recording its identity deliberately requires inspection, not blind overwrite.

This is **not a multi-file atomic transaction**. Keep cron and consumers of the
selected dates paused until `publication/acceptance.json` reports `passed`.
Individual files are atomically replaced, but files may represent mixed revision
generations while a transaction is incomplete. Existing legacy writers/readers
are not universally transaction-aware. No automatic daily/monthly coordinator
cutover is made by adding this bridge. Real-data live-publication acceptance
remains the next gate before connecting it to routine cycles.

Live canaries were submitted on November 7, 2026: **5202214** publishes NRT
April 12 from replay workspace 5192523; **5202241** publishes Retro March 12
from workspace 5192524 with `afterok:5202214`. They are serialized because the
publisher holds shared operational lock paths exclusively. Both request 64 CPUs,
120 GB scratch and a four-hour limit on shared-128, without NRTRES. SLURM
records 125 GiB requested memory through the CPU allocation; allocation still
needs checking when execution starts.

Preflight verified the accepted artifacts, 20 existing destinations per test,
passed subset receipts, and matching pending live revision events. Eighteen
focused tests passed. Installed cron entries on login1 remain commented out;
the active UCRB model run is in 2016, outside these 2026 targets. Each job runs
publication followed by an unchanged repeat. Acceptance lives at
`forcing/work/isolated-revision-replay/<original-workspace>/publication/acceptance.json`;
logs are `forcing/logs/bounded-prism-publication-<new-job>.out`.
Submission is not acceptance: inspect both the publication report and the
unchanged-repeat log before enabling any routine cycles. These tests exercise
live publication/acknowledgement, not live acquisition or newest-hour extension.

Both live canaries passed: **5202214** in 14m07s and **5202241** in 13m56s.
Each published all 20 data products, acknowledged its matching live queue event,
retained originals and completed an unchanged repeat. Published artifact
identities were rechecked. These timings exclude baseline/window rebuilding.

### Coordinator canaries and newest-hour acquisition

The coordinator now supports an explicit bounded canary branch:
`--cycle daily|monthly-retro --replay-workspace PATH --replay-day YYYY-MM-DD`.
`--dry-run` only validates/plans; without it, exactly one bounded publisher job
is submitted. `--replay-after JOB` serializes canaries using `afterok`. This
branch runs under the coordinator submission lock, obeys existing stream
overlap checks, and does **not** launch broad catch-up, source refresh, model
jobs or the ordinary daily revision continuation. Automatic routine-cycle
cutover remains disabled. Replaying already-published accepted workspaces tests
the real cron environment/submission path and unchanged publication, not fresh
revision discovery.

`slurm/test_latest_acquisition.sh` tests the operational `run_latest_nrt.py`
loop with real source acquisition. It freezes a date at execution (UTC now
minus three hours), then processes at most 00–05 UTC on that date, plus the
five preceding HRRR boundary hours required by the existing acquisition loop.
The runner acquires HRRR and available Stage-IV/MRMS inputs normally and requires
GFS northern fallback to have executed before accepting the test. It uses the
normal acquisition/cycle lock and canonical source cache, but directs baselines,
forcing, and status reports to `forcing/work/latest-acquisition-test/JOB/`.
No canonical forcing or latest-extension status is overwritten. A bounded
repeat must preserve the accepted output identity. Missing all usable hours or
failure to exercise GFS is a failed test, not a successful no-op.

The isolated runner arguments (`--test-workspace`, `--test-day`,
`--test-end-hour`) must be supplied together; paths outside `forcing/work`,
invalid hour limits, future dates, and mismatched existing test output are
rejected. Normal no-argument operations retain their existing publication roots
and catch-up behavior. This acquisition test is **not** a daily/monthly summary
test, live forcing extension, or validation of the unrestricted backlog cycle.

November 7 submissions: **5203262** is the daily bounded coordinator canary;
**5203263** is monthly-retro with `afterok:5203262`; **5203264** is the isolated
newest-hour acquisition test with `afterok:5203263`. The coordinator submissions
used `env -i PATH=/usr/bin:/bin /usr/bin/bash bin/run_cron.sh ...` on login1.
Each coordinator canary requests 64 CPUs/120 GB scratch/four hours on shared-128
and should find its already-published outputs unchanged. Acquisition requests
128 CPUs/240 GB scratch/three hours on compute-128. The initial acquisition
submission on shared-128 was rejected by its per-job CPU limit (no job created);
the script now defaults to compute-128. No NRTRES reservation is used.
The full suite passed 752 tests, four skipped, before submission. Cron stays
paused. Inspect job-specific reports and logs before treating these submissions
as acceptance; broad routine replay remains a separate activation gate.

All three subsequently passed: coordinator canaries 5203262/5203263 each took
four seconds and reported unchanged publication. Acquisition test 5203264 took
9m54s, produced November 7 00–05 UTC with six HRRR/GFS hours, successfully
refreshed MRMS/Stage-IV, and repeated unchanged in about two seconds. Canonical
source caches were updated, but production forcing was not extended.

### Bounded real-queue batch and month-boundary commissioning

`bin/run_bounded_revision_queue.py --stream nrt|retro --start DATE --end DATE`
selects at most two pending events in an explicit window of at most seven days.
The default only plans. `--execute` builds accepted isolated candidates and
checks unchanged repeats; additional `--publish` is required to invoke the
bounded live publisher. Each event is rechecked before execution and missing
or superseded PRISM inputs block it. A batch lock serializes these controllers;
publication retains its own operational locks and exact-source acknowledgement.
Processing is chronological, and in publish mode each day's downstream work
finishes before building the next day's context. No cron changes or unrestricted
backlog submissions are implicit.

The replay harness now accepts an explicit `--day` and numeric `--workspace-id`.
For a day on the first of a month it pins context for both affected months,
produces the preceding day's summary and current day's summary, and reduces
both months. The publisher likewise requires both accepted monthly stages,
locks both months and updates **25 data products** rather than 20. Existing
single-month checkpoint formats remain compatible. This extends the earlier
month-boundary exclusion in the initial bridge; production acceptance of the
new boundary path passed in the live publication trial below.

Job **5203609**, 64 CPUs/120 GB scratch/eight hours, tests the pending NRT
**April 30–May 1, 2026** pair with `--execute` only. April/May have complete
canonical daily context. March 31–April 1 was rejected as a test choice because
the NRT archive lacks March 1 daily context. Both selected dates have current
PRISM signatures and existing baseline coverage; the harness nevertheless
rebuilds isolated baselines for this commissioning run. Results are recorded in
`forcing/work/bounded-revision-queue/5203609/acceptance.json`, with per-day
workspaces `forcing/work/isolated-revision-replay/520360901` and `520360902`.
No production forcing or live queue acknowledgement is authorized by this
candidate-only job. Full suite: 756 passed, four skipped; Ruff/shell/diff checks
passed. Cron remains paused; model-history refresh is deferred while forcing
operations are commissioned.

### Revision queue reconciliation before historical replay

Queue discovery marks both changed and previously untracked PRISM sources as
pending. A pending day is not proof of obsolete forcing, and `needs_baseline`
in a replay plan describes execution requirements, not scientific necessity.
Historical precipitation-only repair receipts do not establish temperature
source freshness, but must be reconciled before repeating rainfall repairs.

`bin/reconcile_prism_revision_records.py --output REPORT.json` inventories queued
days against current hourly publication identities, accepted rain-repair records,
and available exact source pins. It is read-only with respect to production and
the queue. It never acknowledges obligations or implies downstream/payload
acceptance. Missing version evidence remains unresolved, not a confirmed change.

The November 8 local-date inventory is saved at
`forcing/work/revision-backlog-audit/reconciliation-20261108.json` (UTC November 9).
All 200 queued Retro days had accepted rainfall-repair records matching current
publication identities, but their older receipts lacked exact PRISM source-version
pins. Of 241 pending NRT days, 228 matched every queued rainfall source to prior
accepted repair pins. Temperature obligations remained unresolved. Running NRT
replay 5219540 selected March 4–5, two such rainfall-current days; no Retro replay
was running. This inventory does not establish that the entire coupled replay is
unnecessary. Review temperature provenance before authorizing further broad
historical replay; no queue entries or jobs were changed by the reconciliation.

Following user approval, backlog job **5219540** was canceled after 43m40s,
while building isolated baseline candidates, before publication. Its March 4–5
queue obligations and earlier accepted publications remain intact. Campaign
`nrt-commissioning-20261108/PAUSED.json` blocks new invocations and successor
submission after a running batch finishes. Removing that guard requires an
explicitly reviewed restart; no cron or unrelated model/observation jobs changed.

The follow-up metadata report
`forcing/work/revision-backlog-audit/reconciliation-temperature-20261108.json`
found current temperature releases later than the retained original build date
for at least one window on 22 queued Retro days and 187 NRT days. On 178 Retro
days the releases were not later than the build, while 54 NRT days lacked usable
build chronology. These are investigation priorities, not content comparisons:
neither chronology nor absent provenance authorizes queue acknowledgement or
automatically proves that numerical fields need rebuilding. Rain-only repair
publication dates must not be mistaken for temperature rebuild dates.

Read-only numerical follow-up **5220132** runs seven diagnostic windows with
two concurrent 64-CPU tasks (120 GB requested scratch per task): March 2,
March 5, April 12, December 15 (2025), September 15, October 25, and April 5
(otherwise 2026). The April 5 NRT window is a recently coupled-replayed control.
`bin/audit_prism_temperature_midpoint.py` remaps current PRISM Tmin/Tmax using
the existing elevation-aware operator and compares their midpoint with the
archived T2D extrema over the exact preceding-12 UTC through current-11 UTC
window. The affine correction preserves this midpoint even when range scaling
is clipped or the baseline range is small. It reads existing forcing without
rebuilding baselines or writing production. Reports are under
`forcing/work/temperature-reconciliation/<task-job-id>/report.json`.

Midpoint disagreement establishes numerical inconsistency with the current
target, not whether a changed release, prior processing, or gap filling caused
it. Midpoint agreement alone cannot certify the range, hourly curve, Q2D or
LWDOWN. The diagnostic does not clear queue entries or authorize model reruns;
source and forcing identities are checked for changes during each comparison.

Initial numerical results found mismatches for Retro March 2 (maximum 2.91 K)
and April 12 (4.75 K), and NRT March 5 (3.04 K), April 12 (2.90 K), and
September 15 (4.84 K). These are differences in the min/max midpoint, not
differences at an individual hour. Retro March 5 and December 15 and NRT
March 2 and October 25 matched to about 0.0000153 K. Thus neither blanket
replay nor blanket queue dismissal is supported.

Read-only array **5220139** extends the diagnostic to all 401 distinct PRISM
temperature windows referenced by the frozen queue, two 64-CPU tasks at a time.
Its plan and submission record are under
`forcing/work/temperature-reconciliation/scan-20261109T011704Z/`.
`bin/submit_prism_temperature_reconciliation.py` freezes the queue snapshot and
submits only diagnostic jobs; it never changes forcing or acknowledges revisions.
The historical replay campaign remains explicitly paused pending the results.

### Targeted thermal-only refresh commissioning

All 401 tasks in diagnostic array 5220139 completed. The frozen refresh plan at
`forcing/work/temperature-refresh/refresh-plan-5220139.json` selects 23 Retro
constraint windows affecting 26 calendar-day files, and 210 NRT windows affecting
215 calendar-day files. Window-to-calendar mapping must include both halves;
adjacent windows must be assembled into a single writer per calendar file.
The plan is not publication authorization independent of the acceptance gates.

Candidate-only job **5221714** tests the April 12 window for both streams,
preserving the original April 11–12 collections by hard link. The new
`bin/trial_temperature_only_refresh.py` obtains fresh baseline temperatures and
uses the established PRISM affine constraint instead of reapplying that affine
map to already-constrained temperatures. It then reconstructs Q2D and LWDOWN
from each archived hour's relative humidity and longwave emission multiplier,
using the existing physical formulas. Only T2D/Q2D/LWDOWN are overwritten in
isolated copies. PSFC, rainfall, wind, shortwave, timing/QC and other datasets
must pass exact unchanged-payload checks; untouched hours of the three thermal
variables are also checked exactly. Active coverage and the retained static
envelope are enforced. PRISM and original-file identities are pinned.

This trial requests 64 CPUs, 120 GB scratch and four hours. Its acceptance report
is `forcing/work/temperature-refresh/5221714/acceptance.json`. Candidate success
does not publish production, update receipts, acknowledge the revision queue,
regenerate summaries/subsets or restart models. Those are subsequent required
steps before declaring the refresh complete. Before Retro publication, coordinate
the pending CNRFC 2026 run's pinned inputs; earlier active model years are not
affected. Preserve the precipitation-repair provenance when adding the new
temperature source-version records.

Trial **5221714** passed candidate acceptance in 44m18s (64 allocated CPUs,
about 20 GiB peak memory). Four candidates retained all nonthermal payloads and
untouched thermal hours exactly; production remained unchanged at this stage.

`bin/publish_temperature_refresh_trial.py` now provides the bounded publication
test for that accepted workspace. It holds the established global/month/domain
publication locks, snapshots all affected production files and sidecars, checks
candidate hashes and pinned PRISM sources, and uses recoverable per-file atomic
replacement transactions. It refreshes three potentially affected daily summaries
per stream (April 10–12), April monthly summaries, and all four child domains
for hourly/daily/monthly resolution. Summary nonthermal values, including
RAINRATE, must remain exactly equal. The explicit repeat invocation verifies a
no-op using published file and sidecar identities. Queue entries remain pending:
one refreshed constraint window does not settle both windows and all three PRISM
elements behind a calendar-day obligation.

CNRFC 2026 model job **5202844** was explicitly placed on hold before this
publication test. Its dependency on 2025 is retained; earlier years continue.
The publisher requires that hold, and the model acceptance reader recognizes
thermal-only replacements only after complete downstream publication, preserved
rainfall, and exact current identities are recorded. Refresh the 2026 preflight
pins after the planned Retro replacements settle before releasing the model job.
Do not interpret accepted candidate generation or successful hourly replacement
alone as completed downstream publication.

Publication/no-op test **5221920** was submitted with 64 CPUs, 120 GB scratch,
and a four-hour limit after **866 tests passed (4 skipped)**. Its durable report
will be `forcing/work/temperature-refresh/5221714/publication/acceptance.json`;
the unchanged-repeat proof is `publication/noop-5221920.json` in that workspace.
Submission is not acceptance; the remaining refresh plan is not launched by
this test.

Job **5221920** stopped at preflight after 3 seconds, before snapshots or any
production replacement: Retro April 2026 is incomplete and has no monthly
product. The publisher now explicitly defers an absent incomplete month, while
still rejecting an existing monthly file whose daily context is incomplete.
Retro daily context is missing April 18–30; no partial-month product is invented.
All planned hourly/daily files and NRT April monthly targets were rechecked.
Retry **5221940** publishes the same accepted hourly candidates, updates daily
summaries for both streams, and updates the complete NRT monthly summary and
corresponding child products. Its repeat proof will be
`forcing/work/temperature-refresh/5221714/publication/noop-5221940.json`.
The CNRFC 2026 hold remains in place. Six focused tests passed after this fix.

Publication **5221940 passed** in 46m35s with 64 CPUs. Its 105 published artifact
identities and unchanged-repeat report passed. Four CONUS hourly files, affected
daily summaries, the complete NRT April monthly summary and all four domain
subsets were updated. Retro April monthly remains explicitly deferred.

The remaining thermal refresh uses `bin/submit_temperature_refresh_campaign.py`
with explicit refresh-plan, accepted pilot-publication and held-model-job inputs.
It excludes the accepted April 12 window, freezes source/original identities and
retains hard-linked originals. Ten month-owned candidate tasks cover 22 remaining
Retro and 209 NRT windows (26 and 215 calendar files). Adjacent windows share one
calendar writer; an unchanged pilot half remains untouched when its other half
is refreshed. Baseline days and corrected temperature windows are cached under
per-date locks and shared across streams. Accepted production precipitation is
preserved exactly; fresh baseline generation is used only for the temperature curve.

The array permits eight concurrent 64-CPU tasks (512 CPUs requested), each with
120 GB scratch and a 48-hour limit. A dependent collector verifies every candidate
hash, scope, pinned input and unchanged production identity before publication.
Only `afterok` acceptance enables the publisher. Publication requests 64 CPUs and
uses eight workers for independent summaries/subsets, with daily-before-monthly
and parent-before-child barriers. Hourly replacement remains transaction-gated;
all affected hourly files are settled before reductions begin. Each summary and
subset is generated once, and nonthermal summary values must remain unchanged.
Failures stop the chain for review; an incomplete array is never partial approval.

Reports live under `forcing/work/temperature-refresh/campaign-<UTC>/`: `plan.json`,
`batches/`, `acceptance.json`, and `publication/acceptance.json`. Submission,
candidate acceptance and completed downstream publication are distinct states.
Neither this campaign nor its repeat check clears revision obligations, removes
originals, releases the CNRFC 2026 model hold, or re-enables cron. Source-level
queue reconciliation and refreshed model input pins follow completed publication.

Campaign `campaign-20261109T055015Z` was submitted as candidate array **5223298**,
acceptance collector **5223299**, and publication **5223300**, with explicit
`afterok` dependencies. Eight candidate tasks started with 64 CPUs/125 GiB each;
the two remaining tasks wait for array slots. These are execution/submission
states, not completed publication. The full suite passed 869 tests (four skips),
and focused boundary/preservation tests, Ruff, shell syntax and diff checks passed.

### Independent May diagnostic and publication hold

Publication job **5223300** is explicitly user-held pending investigation of the
widespread temperature mismatch. Candidate generation and its acceptance collector
may continue; neither is permission to release publication. CNRFC 2026 and cron
remain paused. File replacement/download timestamps alone do not prove that
PRISM revisions caused the archived numerical differences.

Read-only diagnostic array **5228688** checks May 3, 10 and 15, 2026. It requests
three concurrent 64-CPU tasks with two-hour limits and 120 GB scratch each.
`bin/diagnose_temperature_refresh.py` constructs an independent target by
interpolating temperatures and elevations separately in double precision, then
applying the explicit -6.5 K/km correction. It does not invoke the production
temperature-target builder or affine adjustment function. It checks exact
timestamps, original/candidate/window midpoints, independent hourly affine
values, calendar assembly, complete active-cell coverage and immutable inputs.
The numerical tolerance is 0.001 K; spatial error fields are retained.

Reports/maps are in `forcing/work/temperature-refresh-diagnostics/<task-job>/`.
Matching current PRISM establishes present consistency, not the cause of the
old mismatch: identifying source revisions conclusively requires historical
source values. These jobs cannot publish data or release any hold. Seven focused
diagnostic tests, Ruff, shell syntax and diff checks passed before submission.

The first diagnostic array completed its calculations but reported mismatches:
the repaired midpoint maximum errors were below 0.000054 K, while 15,874 missing
cells were outside the operational active mask. Its broader grid mask incorrectly
required those cells. NaNs also made the reported zero hourly maximum errors
unreliable; those zeros must not be interpreted as successful hourly checks.

Diagnostic version 2 uses the static envelope's `active` variable for required
coverage, tests coverage across all required cells (including cells without PRISM
targets), and rejects nonfinite values inside each comparison domain. It compares
candidate/window hours across the full operational mask and independent affine
values where valid PRISM targets exist. Empty comparison domains also fail closed.
Rerun array **5228762** checks the same three May windows; nine focused tests,
including masked values, NaN/Inf rejection and mask selection, passed. Publication
**5223300 remains held**; no diagnostic can release it automatically.

All three version-2 tasks **5228762** passed in 1m34s–1m39s. Across 10,315,371
required active cells, original/baseline/window/candidate temperature coverage
was complete. The independent affine comparison covered 7,350,314 active cells
with valid PRISM targets; maximum hourly discrepancies were 0.00005888,
0.00005860 and 0.00005942 K for May 3, 10 and 15. Candidate values matched the
corrected window exactly across every required active cell/hour. Midpoint maximum
errors stayed below 0.000054 K. This validates present temperature adjustment and
calendar assembly for the three cases, not the historical cause of old mismatches
or all campaign dates. Publication is still held for review.

Read-only array **5228930** investigates the original hourly curves for the same
three May dates. Retained baseline collections still exist and their creation
metadata places them on September 20, shortly before the original constrained
collections. `bin/diagnose_original_temperature_curve.py` compares original versus
retained baseline, original versus rebuilt baseline, rebuilt versus retained
baseline, and repaired candidate versus rebuilt baseline as a positive control.
It fits an independent per-cell affine relation over the exact 24-hour window,
reports maximum hourly differences, fitted residuals, midpoint shifts and slopes,
and verifies input identities remain unchanged. Coverage is the operational
active mask; some of those cells lack PRISM targets and should remain baseline.
An affine signature demonstrates a compatible adjustment, not the exact old
PRISM release. Reports are under
`forcing/work/original-temperature-diagnostics/<task-job>/report.json`.
Three concurrent 32-CPU diagnostic tasks were submitted after four focused tests,
Ruff, shell syntax and diff checks passed. Publication remains held.

Array **5228930** completed all three curve diagnostics in 1m22s–1m27s. Original
versus retained baseline was affine at every operational active cell, with
maximum fit residuals below 0.000034 K and positive slopes capped at 4. Roughly
7.35 million cells differed from baseline. Rebuilt and retained baselines agreed
within 0.001 K at about 99.44% of active cells; this localized baseline difference
cannot explain the widespread original/current target midpoint mismatch.

Read-only job **5230153** compares the saved original-minus-current target
midpoint maps across May 3, 10 and 15. It quantifies cross-date correlations,
per-cell temporal spread and the fraction of squared error represented by a
fixed spatial component. Separately remapped PRISM terrain tests the specific
omitted-lapse and doubled-lapse hypotheses. It verifies accepted version-2 report
hashes and immutable inputs, retains spatial fields, and does not publish or
release holds. A varying pattern alone would not prove PRISM source revisions.
Reports are under `forcing/work/temperature-target-comparison/5230153/`.
Two focused statistical tests, Ruff, shell syntax and diff checks passed.

Job **5230153** completed the cross-date statistics but failed before the terrain
hypothesis check because raw static terrain did not supply the coordinate
metadata expected by CDO. Retry **5230154** stages the elevation values with the
normalized PRISM coordinates already used by target construction; errors now
retain CDO stderr. The failed job's partial statistics are evidence, not an
accepted complete diagnostic. Neither job changes production or releases holds.

Retry **5230154** completed successfully in 15 seconds. Across 7,350,314 common
active cells, cross-date error-map correlations were 0.519–0.576; about 74% of
cells changed by more than 0.1 K across the three dates. The mean spatial pattern
represented 69.4% of squared error, so shared geography exists, but a fixed
additive offset alone does not explain the mismatch. Original midpoint RMS
errors were 0.302–0.317 K. Correlation with the independently calculated lapse
correction was approximately zero (−0.0003 to 0.026); omitted/doubled correction
hypotheses worsened residual RMS to 0.422–0.435 K. These simple elevation-error
hypotheses are not supported. This does not establish historical PRISM revisions:
historical source values or field-dependent remapping differences remain possible.
Evidence is `forcing/work/temperature-target-comparison/5230154/report.json`
and `target-patterns.nc` in the same directory. Input identities remained intact;
production publication remains held pending the historical-target investigation.

Additional read-only alignment job **5230167** completed in 49 seconds with
32 allocated CPUs. It verified the accepted map hashes and pinned diagnostic
inputs before/after comparison. One-cell shifts in all eight directions and
four-cell cardinal shifts did not improve agreement: the best shifted RMS
errors were 0.411, 0.405 and 0.402 K for May 3, 10 and 15, respectively,
versus approximately 0.317, 0.310 and 0.302 K on the same valid comparison cells.
Using the preceding/following PRISM date instead produced 2.59–3.39 K RMS error.
These tests do not support the tested integer-grid or one-day offsets; they do
not exhaust all possible historical interpolation differences. Evidence:
`forcing/work/temperature-target-alignment/5230167/report.json`. Two focused
unit tests passed, including no cyclic grid wrap and same-cell comparisons;
Ruff and shell syntax checks passed.

The current six May Tmin/Tmax files have source-grid fingerprint
`2e02e599c92a2569dc6d29a82fb7036a64efe917dec1acaf2169c510b947f26d`, matching
the August 22 bilinear-weight manifest. Their provider history records October
22 NetCDF creation and their release sidecars record that release and October
23 acquisition. This establishes newer source artifacts, not changed values.
The original September 20 assembly manifests identify temporary scratch window
files, not immutable historical PRISM inputs. The inspected retained September
revision-test workspace contains September 14–16 sources, not the May cases.
No historical May values were recovered from these inspected records. Relevant
normalization and temperature-target code history predates original production;
there are no current worktree edits to those modules. Consequently, the original
cause remains unproven: do not label these differences confirmed PRISM value
revisions or automatically release publication job **5223300**.

### PRISM source-change evidence

Thermal candidate acceptance **5223299** passed for all 241 files. Publication
**5223300** failed during initial planning (7 seconds), before creating a plan
or replacing production data: it assumed that Retro April 18's daily summary
already existed. That summary cannot yet be created because April 19 00 UTC
is absent. The publisher now preflights the exact 01–00 UTC timestamps and
explicitly defers an incomplete day only when no corresponding daily product
exists in CONUS or any downstream domain. Existing products with missing context
still fail closed; duplicate/non-hourly timestamps are rejected. Incomplete
months remain deferred without fabricating partial monthly summaries.

Missing downstream products with complete context may be created; their frozen
plan records a null original identity rather than attempting a nonexistent
backup. Existing products retain identity checks, backups and nonthermal-value
preservation checks. New products use normal validated summary/subsetting paths;
the final report distinguishes newly created products and deferred dates. A
changed target set on retry requires explicit review. Sixteen focused thermal
publication/campaign/transaction tests passed, along with Ruff and diff checks.

Retry **5232861** uses 64 CPUs/eight workers and the same accepted candidates.
Read-only full-scope preflight found 26 Retro plus 215 NRT hourly files, 25 Retro
plus 215 NRT daily summaries, and two Retro plus eight NRT monthly summaries
ready for publication and all four domain subsets. Explicit deferrals are Retro
April 18 daily, Retro April monthly and NRT November monthly. No ready target
is currently absent. SLURM rejected a dependency on the aged completed acceptance
job; after confirming **5223299 COMPLETED 0:0**, the retry was submitted without
that scheduler dependency. Application-level acceptance and identity gates remain
mandatory. The campaign's `publication-retry-5232861.json` records the submission;
this is not yet publication acceptance. CNRFC 2026 and cron remain held/paused.

Read-only historical spot check **5232559** was submitted for Retro May 3, 10
and 15, 2025 using `audit_independent_temperature_target.py`. It independently
remaps temperature/elevation in double precision, checks the 24-hour PRISM
windows against the operational active mask, and records midpoint errors and
descriptive extrema differences. Missing active-cell values fail the comparison;
range clipping means individual extrema differences alone are not failures.
It pins source and forcing identities and writes only diagnostic evidence under
`forcing/work/independent-temperature-audit/5232559/report.json`. Submission is
not scientific acceptance and does not alter the authorized 2026 publication.

Following user authorization, the investigative hold on publication **5223300**
was released on November 9. Its `afterok:5223299` acceptance dependency remains
unchanged; acceptance itself waits for candidate array **5223298**. Release is
not publication acceptance: pinned-source, candidate-scope and production-identity
checks and publication locks remain mandatory. CNRFC 2026 job **5202844** remains
held and cron remains paused. The historical discrepancy remains unresolved;
the decision proceeds on the independent validation against current PRISM.

Daily PRISM acquisition now retains a compact `<source-stem>.history.json`
alongside each normalized source and its existing `.release.json`. Each actual
download records the previous/new release metadata, whole-file SHA-256,
decoded-value SHA-256, dimensions, latitude/longitude hashes, units and finite
cell counts. For compatible grids/units it also records changed finite cells,
newly finite/missing cells, mean/RMS/maximum absolute change in source units
(degrees Celsius for temperature, millimeters for precipitation). This separates
release/encoding/history-attribute changes from actual decoded-value changes.
Grid or unit changes are explicitly marked incomparable rather than reporting a
misleading numerical difference. Skipped current releases add no event.

If an existing source cannot be decoded (for example, a truncated NetCDF or a
missing expected variable), a valid replacement is still allowed. The history
retains the old byte-level SHA-256 and inspection error, with comparison kind
`old_grid_unreadable` and `values_changed: null`. This is not a numerical match
or proof of a provider revision. The new grid must pass normal inspection; failure
to read the old bytes for hashing still stops replacement. Malformed history or
release metadata is not silently discarded by this recovery path.

Evidence is atomically saved as `prepared` before source replacement, then marked
`published` only after the data and release sidecar are installed. An interrupted
`prepared` event is not proof of publication: compare its recorded hashes with
the files before resolving it. A stable per-source `.download.lock` serializes
downloaders; do not delete lock files while a downloader could be using them.
History is cumulative within one JSON file per source, not one file per release.
Malformed existing evidence fails closed rather than discarding its history.

This is prospective evidence for daily PRISM acquisition, not a reconstruction
of old releases or a claim that all providers/monthly PRISM acquisition already
use it. No full old grids are retained and hashes cannot recover old values.
The additional comparisons read only the old/new PRISM source grids, not CONUS
forcing archives; unchanged-source skips do not scan the data values. Source
history does not change revision selection, authorize downstream publication,
release existing holds or restart cron.

### Routine bounded revision integration

Candidate test **5203609** passed in 3:02:00. Live publication test **5203912**
passed in 3:36:55: both dates were published, their immediate unchanged repeats
made no writes, and both exact-source queue obligations were acknowledged.
The final, latest-version identity check covered 67 data/metadata artifacts with
zero mismatches (the second day legitimately superseded shared summaries).
Originals remain retained. This timing includes isolated baseline reconstruction,
constraints, summaries, subsets, publication and repeats, not just file transfer.

The routine convergence controller now submits the following dependency chain:

```text
normal hourly production + summaries
  -> successful PRISM revision scan
  -> bounded replay/publication (at most two eligible dates)
  -> downstream domain synchronization and enabled model cycles
```

Daily cycles consume NRT obligations; monthly-retro cycles consume Retro
obligations. Monthly source refresh still discovers changes affecting both
streams; NRT obligations remain for a daily cycle. Six-hourly latest extension
does not drain this historical queue. This addition does not replace normal
recent-source revisions, Retro gap filling or source acquisition.

`bin/run_bounded_revision_queue.py --routine --stream nrt|retro` selects at
execution time, oldest eligible dates first, with a hard two-date maximum.
Explicit commissioning dates remain supported separately. A routine invocation
without `--execute --publish` is a read-only plan. Both routine and explicit
execution retain candidate acceptance, unchanged-repeat checks, exact-source
acknowledgement, atomic per-file publication, original retention and locking.

Routine replay currently requires existing target products, adjacent hourly
files, complete daily context for affected months, and all six PRISM inputs.
Incomplete context (including a current unfinished month) or unavailable/stale
PRISM sources is deferred, never silently acknowledged. Normal cycles remain
responsible for creating missing products. A no-work batch succeeds, but reports
`pending_remaining` and `backlog_complete` separately; success does not assert
the historical backlog has been drained. Execution failures stop downstream
dependencies and retain unacknowledged events for investigation/retry.

Worker requests remain 64 CPUs, 120 GB scratch and eight hours. This conservative
commissioning cap is not a claim that historical replay fits a one-hour NRT
latency budget. Catalog scanning is a separate two-CPU job; all dependencies use
`afterok`, and inherited `SBATCH_RESERVATION` is removed for these submissions.
Coordinator manifests distinguish normal publication from submitted replay;
the job-specific replay report is `forcing/work/bounded-revision-queue/JOB/acceptance.json`.
Scanner `automatic_replay_enabled=false` means the scanner itself never publishes;
the explicit `replay_policy` identifies the separate bounded consumer.

Cron schedules/installations and model enablement flags are unchanged. Cron
remains paused pending an integrated routine-path check; this is not authorization
to launch an unrestricted backlog or refresh historical model runs.

Integration verification: 763 tests passed, four skipped; changed Python files
passed Ruff, shell entry points passed syntax checks, and `git diff --check`
passed. A clean-environment `bin/run_cron.sh ... --cycle daily --dry-run` showed
the latest-hour-first branch plus bounded replay policy without submitting jobs.
Read-only routine selection found NRT April 2–3 (31 earlier events deferred for
missing context; 239 pending stream-days total) and Retro September 30–October 1,
2025 (200 pending stream-days total). These are inspection-time snapshots, not
published repairs. The next acceptance gate is an integrated live routine run;
unit/dry-run success does not by itself authorize cron installation.

### Full daily cycle commissioning with model submissions suppressed

`bin/update_nwm_forcing.py --cycle daily --skip-model-runs` uses normal source
acquisition, latest extension, revisions, summaries and domain synchronization,
but persists model suppression into the daily continuation manifest and launcher.
Shared domain model configuration is unchanged. CNRFC, B120 and Yampa automatic
model submission branches are skipped for this cycle; existing Retro chains are
unaffected. The flag does not install or enable cron.

The November 8 clean-environment trial submitted latest-hour job **5209456**,
revision launcher **5209457** and hourly CNRFC catch-up **5209458**. The first job
stopped in six seconds at the existing seven-day catch-up guard: the latest
accepted NRT hour was October 31, 19 UTC. No new forcing was produced by that
job. The blocked revision launcher was canceled, while the independent CNRFC
catch-up completed in three seconds. This was a failed full-cycle attempt, not
an acceptance result. A reviewed bounded catch-up is needed before retrying;
the routine guard remains unchanged and cron remains paused.

The model-suppression change passed the full suite (764 passed, four skipped),
plus an additional focused test of all model branches (nine tests in that file
passed). Ruff and diff checks passed. Launcher output is retained at
`forcing/logs/daily-full-cycle-forcing-only-20261108.log`.

The latest-hour runner now accepts `--catchup-through YYYY-MM-DD` for explicitly
reviewed catch-up jobs. It resumes the latest accepted contiguous hour, publishes
only through that completed UTC date, and limits each invocation to seven
calendar days. The normal routine seven-day age guard is unchanged. Catch-up
fails if unavailable sources prevent reaching the requested date at 23 UTC,
so `afterok` successors cannot mistake a partial result for completion. Accepted
partial progress remains restartable. Isolated test-workspace options cannot be
combined with this canonical-publication mode. Catch-up jobs must hold the same
NRT cycle lock and use normal acquisition, GFS fallback and publication checks.

### Calendar PRISM publication receipt repair (November 8 commissioning)

Catch-up jobs 5209579/5209580 passed through November 7 at 23 UTC; latest-hour
job 5211838 then published through November 8 at 08 UTC. The subsequent daily
summary job 5214178 failed on October 29 because the calendar PRISM batch
publisher replaced the hourly file but retained an older, unconstrained NRT
receipt. A metadata scan identified October 29 and 30 as the two stale receipts.
This failure correctly blocked summaries, bounded revision replay and domain sync.

`produce_prism_calendar_batch.py` now assembles NRT calendar candidates in job
scratch and calls `nrt_calendar_publication.publish` after PRISM/domain-repair
validation. The finalizer takes the shared NRT cycle lock, requires a current
complete baseline receipt, performs the existing full NRT active-cell audit and
static-envelope application/readback, verifies inputs remained unchanged, then
transfers the candidate and writes matching NRT receipt/manifest sidecars.
It does not reuse the old unconstrained fingerprint or claim the old receipt
validated the new file. Its explicit calendar-finalizer input fingerprint is
distinct from normal recent-NRT source selection; a later ordinary cycle may
rebuild it when comparing inputs. Retro publication is unchanged.

Existing targets and their sidecars are retained before replacement. File and
sidecars are individually atomic, not a multi-file transaction: readers retain
their strict identity gates, and a publication interrupted before sidecar
completion must be recovered rather than treated as accepted.

Repair job **5216895** runs the same finalizer for October 29–30, reusing the
already reconciled calendar values (no new remapping/PRISM solve). Originals and
job-specific acceptance evidence are under
`forcing/work/nrt-calendar-receipt-repair/5216895/`. Summary retry **5216896**
depends on repair success. Existing revision scan **5214179** was reconnected to
that retry, preserving revision replay **5214180** and domain-sync jobs
**5214181–5214185** downstream. Submission details are saved in
`forcing/work/nrt-calendar-receipt-repair-submission.json`.
These are submitted recovery jobs, not yet a completed operational acceptance.
Cron remains paused and model-run suppression remains in effect.
The full suite passed **811 tests with four skips**, including new tests for
static-envelope application/current receipt publication and active-hole rejection
without replacing the original. Ruff, shell syntax and diff checks passed.
The repair requested 32 CPUs but SLURM allocated a full 128-CPU compute node;
submission resources and actual allocation are not interchangeable.

### Receipt recovery outcome and remaining queue work (November 8)

Receipt repair **5216895 passed** for October 29–30 (4m41s); summary retry
**5216896 completed** (15m36s). Revision scan **5214179** and replay **5214180**
passed; the latter published/acknowledged April 4–5 with complete downstream
acceptance in 3h35m22s. NRT domain sync jobs **5214181–5214184** passed with zero
failures, as did CNRFC Retro sync **5214185**. This completed the blocked chain,
not the historical backlog: 243 NRT and 200 Retro stream-days remained queued.

March NRT revisions were deferred because the archive starts March 2: March 1
hourly/daily and the complete March monthly summary were absent. Do not fabricate
a partial March monthly file or silently copy Retro data into NRT. Authorized
context repair **5217629** uses normal source-aware NRT production for March 1,
then accepted March daily/monthly reductions and all four domain subsets. It
checks publication context for March 2 and the March 31/April 1 boundary before
declaring success. It requests 128 CPUs, 300 GB scratch and eight hours. Per-domain
subset scratch names allow B120, UCRB and Yampa sync within the same allocation.
Evidence: `forcing/work/nrt-march-context/5217629/acceptance.json`.

Read-only audit **5217628** classifies every queued event using exact source
signatures and required hourly/daily/monthly context. Its report is
`forcing/work/revision-backlog-audit/5217628/acceptance.json`; no queue entries
are acknowledged by an inventory audit.

After context repair, **5217630** starts the explicit offline NRT backlog campaign
`forcing/work/revision-backlog-drain/nrt-commissioning-20261108/`. The plan freezes
the 243 authorized keys and limits work to 128 two-date batches. Each job requests
64 CPUs, 120 GB scratch and eight hours, invoking the already validated bounded
replay/publication/no-op/exact-source acknowledgement path. Only a successful
batch with acknowledged selected keys submits its next `afterok` job. Failures,
uncertain submissions, new out-of-scope queue obligations, or the batch cap stop
automatic progression; existing receipts and originals are retained. This is not
an automatic retry of scientific failures.

If no eligible work remains, `final.json` explicitly distinguishes an empty
backlog from deferred obligations. Incomplete current-month context and missing
sources remain pending, never silently accepted. A terminal deferred report does
not mean all forcing is current. Routine cron retains its two-date cap and remains
paused; this separate historical campaign can take days at observed replay rates.
Retro publication is not launched while active model reruns depend on pinned
Retro files; the read-only audit informs its separate scheduling. Model jobs,
cron installation and old-file cleanup are unchanged.

## References

- Cosgrove, B. A., et al. (2003), *Real-time and retrospective forcing in the North American
  Land Data Assimilation System (NLDAS) project*,
  <https://doi.org/10.1029/2002JD003118>.
- NASA NLDAS-2 forcing dataset information,
  <https://ldas.gsfc.nasa.gov/nldas/v2/forcing>.
- NOAA HRRR documentation, <https://rapidrefresh.noaa.gov/hrrr/>.
- NOAA RAP/HRRR near-surface diagnostic variable documentation,
  <https://rapidrefresh.noaa.gov/RAP_var_diagnosis.html>.
- PRISM dataset documentation,
  <https://www.prism.oregonstate.edu/documents/PRISM_datasets.pdf>.
- PRISM time-series revision policy, <https://prism.oregonstate.edu/data/>.
