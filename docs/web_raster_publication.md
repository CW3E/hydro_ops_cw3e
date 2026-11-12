# Web raster pilot publication

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
