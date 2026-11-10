#!/usr/bin/env python3
"""Historical, read-only tracing for Nantucket and Quebec pilot exceptions.

Retained for scientific reproducibility, not routine operations. Fixed pilot
locations and campaign inputs must be reviewed before executing under SLURM.
"""

import gzip
import io
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator


def selected_raw(path, wanted):
    with tarfile.open(path) as monthly:
        for day in sorted({name.split(".")[1][:8] for name in wanted}):
            with tarfile.open(
                fileobj=io.BytesIO(monthly.extractfile(f"ST4.{day}").read())
            ) as daily:
                for member in daily:
                    if Path(member.name).name in wanted:
                        yield member.name, gzip.decompress(daily.extractfile(member).read())


def points(ds, name, ys, xs):
    y0, y1, x0, x1 = min(ys), max(ys) + 1, min(xs), max(xs) + 1
    var = ds[name]
    values = np.ma.asarray(var[..., y0:y1, x0:x1], dtype="f8").filled(np.nan)
    return values[..., np.asarray(ys) - y0, np.asarray(xs) - x0]


def main():
    root = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    output = root / "forcing/work/prism-local-exceptions" / os.environ["SLURM_JOB_ID"]
    output.mkdir(parents=True, exist_ok=False)
    remap = root / "forcing/static/remapping/nwm_conus_1km"
    mask_path = root / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc"
    report = {"production_modified": False, "nantucket": {}, "quebec": {}}
    op = ConservativeOperator.from_cdo(remap / "nwm_to_prism_conservative_masked.nc")
    with Dataset(mask_path) as ds:
        width = ds["active"].shape[1]
    target_ids = [578775, 578776, 580180, 580181]
    sources = np.unique(op.source_index[np.isin(op.target_index, target_ids)])
    ys, xs = sources // width, sources % width
    with Dataset(mask_path) as ds:
        active = points(ds, "active", ys, xs).astype(bool)
        keep = points(ds, "keep", ys, xs).astype(bool)
        lat, lon = points(ds, "lat", ys, xs), points(ds, "lon", ys, xs)
    report["nantucket"]["sources"] = [
        {
            "index": int(s),
            "y": int(y),
            "x": int(x),
            "active": bool(a),
            "keep": bool(k),
            "lat": float(la),
            "lon": float(lo),
        }
        for s, y, x, a, k, la, lo in zip(sources, ys, xs, active, keep, lat, lon, strict=True)
    ]
    report["nantucket"]["target_links"] = {
        str(t): op.source_index[op.target_index == t].tolist() for t in target_ids
    }
    report["nantucket"]["days"] = []
    for day in range(14, 18):
        row = {"day": day}
        for label, folder in (
            ("baseline", "forcing/work/prism-conus-support-pilot-v1/baseline"),
            ("retro", "forcing/outputs/conus/retro/hourly"),
        ):
            with Dataset(root / folder / f"2014/01/201401{day}.LDASIN_DOMAIN1") as ds:
                rain = points(ds, "RAINRATE", ys, xs) * 3600
                missing = np.any(~np.isfinite(rain), axis=0)
                row[label] = {
                    "missing_sources": sources[missing].tolist(),
                    "missing_active_sources": sources[missing & active].tolist(),
                    "daily_totals_mm": rain.sum(axis=0).tolist(),
                }
        report["nantucket"]["days"].append(row)
    del op
    op = ConservativeOperator.from_cdo(remap / "stage4_conservative.nc")
    index = 3159 * width + 4220
    links = op.target_index == index
    native, weight = op.source_index[links], op.weight[links]
    with Dataset(remap / "stage4_conservative.nc") as ds:
        nx = int(ds["src_grid_dims"][0])
    ny, xx = native // nx, native % nx
    report["quebec"]["cached_weight_link_count"] = len(native)
    if not len(native):
        with Dataset(mask_path) as ds:
            qlat, qlon = float(ds["lat"][3159, 4220]), float(ds["lon"][3159, 4220])
        with Dataset(
            root
            / "forcing/inputs/noaa/stage4/netcdf/archive/2014/01/stage4_archive_01h.20140116.nc"
        ) as ds:
            la, lo = np.asarray(ds["latitude"][:]), np.asarray(ds["longitude"][:])
            lo = (lo + 180) % 360 - 180
            closest = np.argmin((la - qlat) ** 2 + ((lo - qlon) * np.cos(np.deg2rad(qlat))) ** 2)
            y, x = np.unravel_index(closest, la.shape)
            ny, xx = np.array([y]), np.array([x])
            native = ny * nx + xx
            weight = np.ones(1)
        report["quebec"]["comparison_method"] = (
            "nearest native Stage-IV cell; cached operator has no links"
        )
    report["quebec"]["stage4_links"] = [
        {"source_index": int(s), "y": int(y), "x": int(x), "weight": float(w)}
        for s, y, x, w in zip(native, ny, xx, weight, strict=True)
    ]
    report["quebec"]["days"] = []
    for day in range(14, 18):
        row = {"day": day}
        with Dataset(
            root
            / f"forcing/inputs/noaa/stage4/netcdf/archive/2014/01/stage4_archive_01h.201401{day}.nc"
        ) as ds:
            rain = points(ds, "APCP_surface", ny, xx)
            row["native_stage4_mm"] = rain.tolist()
            row["stage4_weighted_mm"] = (rain @ (weight / weight.sum())).tolist()
            row["native_lat"] = points(ds, "latitude", ny, xx).tolist()
            row["native_lon"] = points(ds, "longitude", ny, xx).tolist()
            row["stage4_attrs"] = {k: str(ds.getncattr(k)) for k in ds.ncattrs()}
        with Dataset(
            root
            / f"forcing/work/prism-conus-support-pilot-v1/baseline/2014/01/201401{day}.LDASIN_DOMAIN1"
        ) as ds:
            row["baseline_mm"] = (
                np.asarray(ds["RAINRATE"][:, 3159, 4220], dtype="f8") * 3600
            ).tolist()
            row["precip_source_id"] = ds["precip_source_id"][:, 3159, 4220].tolist()
            qlat, qlon = float(ds["lat"][3159, 4220]), float(ds["lon"][3159, 4220])
        with Dataset(
            root
            / f"forcing/inputs/nasa/nldas2/fora0125_hourly_v2.0/2014/NLDAS_FORA0125_H.A201401{day}.020.nc"
        ) as ds:
            y = int(np.argmin(abs(ds["lat"][:] - qlat)))
            x = int(np.argmin(abs(ds["lon"][:] - qlon)))
            row["nearest_nldas_mm"] = np.ma.asarray(ds["Rainf"][:, y, x]).filled(np.nan).tolist()
            row["nearest_nldas_lat_lon"] = [float(ds["lat"][y]), float(ds["lon"][x])]
        report["quebec"]["days"].append(row)
    (output / "diagnosis.json").write_text(json.dumps(report, indent=2) + "\n")
    # Verify selected suspect hours against original GRIB, not just its NetCDF conversion.
    raw = []
    wanted = {
        "ST4.2014011408.01h.gz",
        "ST4.2014011611.01h.gz",
        "ST4.2014011705.01h.gz",
        "ST4.2014011412.06h.gz",
        "ST4.2014011612.06h.gz",
        "ST4.2014011706.06h.gz",
    }
    if True:
        for member_name, data in selected_raw(
            root / "forcing/inputs/noaa/stage4/archive/2014/stage4.201401.tar", wanted
        ):
            scratch = Path(
                f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
            )
            scratch.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=scratch, suffix=".grb") as stream:
                stream.write(data)
                stream.flush()
                command = [
                    "grib_get",
                    "-p",
                    "edition,shortName,units,dataDate,dataTime",
                    "-l",
                    f"{qlat},{qlon % 360},1",
                    stream.name,
                ]
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                indexed = subprocess.run(
                    ["grib_get", "-i", str(int(native[0])), stream.name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                locator = subprocess.run(
                    ["grib_ls", "-l", f"{qlat},{qlon % 360},4", stream.name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                grid = subprocess.run(
                    [
                        "grib_get",
                        "-p",
                        "gridType,Nx,Ny,latitudeOfFirstGridPointInDegrees,longitudeOfFirstGridPointInDegrees,scanningMode,DxInMetres,DyInMetres",
                        stream.name,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                raw.append(
                    {
                        "member": member_name,
                        "nearest_stdout": result.stdout,
                        "nearest_stderr": result.stderr,
                        "nearest_returncode": result.returncode,
                        "indexed_stdout": indexed.stdout,
                        "indexed_stderr": indexed.stderr,
                        "indexed_returncode": indexed.returncode,
                        "locator": locator.stdout,
                        "grid": grid.stdout,
                    }
                )
            if len(raw) == len(wanted):
                break
    if len(raw) != len(wanted):
        raise ValueError("Not all requested original GRIB samples were found")
    report["quebec"]["original_grib_samples"] = raw
    (output / "diagnosis.json").write_text(json.dumps(report, indent=2) + "\n")
    print(output / "diagnosis.json", flush=True)


if __name__ == "__main__":
    main()
