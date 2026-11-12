# Calendar-month forcing percentiles

`bin/produce_monthly_forcing_climatology.py` builds full-domain CNRFC and UCRB
tables from accepted retro monthly summaries. Reference: **October 1979 through
September 2025**, 46 water years, 552 files, 46 samples per calendar month.
There is no running window or boundary padding for this monthly product.

- `T2D`: monthly mean air temperature (K), not daily extrema or their midpoint.
- `RAINRATE`: monthly mean precipitation rate (kg m-2 s-1), including zero months.
- Percentiles: 0.5, 1.5, …, 99.5 using exact Hyndman–Fan type 8
  (`median_unbiased`), consistent with our land climatology. Calendar-month
  grouping matches the purpose of CDO `ymonpctl`, but is not a claim of numerical
  equivalence to CDO's default histogram-based calculation.
- Missing/nonfinite samples are excluded; each variable has a sample-count grid.
  At least 30 valid years are required. A dense percentile table does not create
  more than 46 independent observations; extreme-tail ranks remain uncertain.
- Keep the source forcing coverage and both subset masks. Do not restrict this
  forcing climatology to model-active cells or spatially fill missing values.

One compressed NetCDF table per domain has dimensions month/percentile/y/x.
It retains latitude/longitude, bounds and masks. Processing loads one month and
one variable at a time, with bounded in-memory quantile blocks; **no spatial tile
files** are created. Reads do not shift already reduced monthly time coordinates.
Precipitation rate and total ranks are identical for a fixed month length, but
February total ranks can differ because leap years have an extra day. These
tables deliberately rank rates, not totals.

Submission from the project root:

```bash
source bin/project_environment.sh
sbatch --job-name=forcing-cnrfc-monthly-percentiles slurm/produce_monthly_forcing_climatology.sh cnrfc
sbatch --job-name=forcing-ucrb-monthly-percentiles slurm/produce_monthly_forcing_climatology.sh ucrb
```

Each job requests 16 CPUs (approximately 32 GB on this cluster) for memory
headroom, not 16-way quantile parallelism. Each uses a unique output directory:
`forcing/analysis/<domain>/retro/monthly-climatology/wy1980-2025/job_<id>/`.
`report.json` must say `passed` before using `monthly_forcing_percentiles.nc`.
No existing forcing or climatology is overwritten. Checks cover accepted subset
receipts, file identities, grid/mask consistency, complete monthly bounds, units,
nonnegative rain, monotonic quantiles, sampled independent quantile calculations,
NetCDF readback, and unchanged inputs through completion. Failures leave an
unpublished `.partial` file for diagnosis. This is a statistical/structural audit,
not a new scientific validation of all historical monthly forcing values.

Tables may be inverted with the existing quantile-table rank utility
`hydro_ops.wrf_hydro.climatology.percentile_rank` after selecting the matching
month and variable. It uses midranks for tied thresholds and clips tails to
0.5–99.5. No SWE-like threshold is applied to zero precipitation. New source
revisions require a new climatology build; these are fixed-reference products,
not automatically updated by cron.

## Example rank maps

`slurm/map_monthly_forcing_climatology.sh` runs six isolated preview tasks using
`bin/map_monthly_forcing_climatology.py`: April 2015, April 2023 and September
2025 for both domains. The current preview pins accepted tables 5263647 (CNRFC)
and 5263648 (UCRB); it verifies the selected monthly source against the table's
history manifest. This is a campaign preview, not an automatic cron product.
Each PNG pairs monthly values with percentile ranks. Temperature is displayed in
°C and precipitation in mm/day (mean rate, not accumulated monthly depth).
The companion NetCDF retains native K and kg m-2 s-1 values and percentile ranks.
Outputs and individual acceptance reports are under the table's `maps-<job>/`
directory. These reference-period examples include their own year in the
climatology; they are not leave-one-out validation.
