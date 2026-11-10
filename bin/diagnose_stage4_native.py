#!/usr/bin/env python3
"""Read-only comparison of archived GRIB1 hourly/six-hour precipitation and conversion."""

import argparse
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.download.stage4_legacy import _decompress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("day")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    day = args.day
    source = root / f"forcing/inputs/noaa/stage4/archive/{day[:4]}/stage4.{day[:6]}.tar"
    converted = root / (
        f"forcing/inputs/noaa/stage4/netcdf/archive/{day[:4]}/{day[4:6]}/"
        f"stage4_archive_01h.{day}.nc"
    )
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    scratch.mkdir(parents=True, exist_ok=True)
    report = {"day": day, "archive": str(source), "converted": str(converted)}
    with tarfile.open(source) as monthly:
        stream = monthly.extractfile(f"ST4.{day}")
        if stream is None:
            raise ValueError("Daily archive missing")
        payload = stream.read()
    fields = {}
    with tempfile.TemporaryDirectory(dir=scratch) as temporary, tarfile.open(
        fileobj=io.BytesIO(payload)
    ) as daily:
        for member in daily.getmembers():
            parts = Path(member.name).name.split(".")
            if len(parts) != 4 or parts[0] != "ST4" or parts[1][:8] != day:
                continue
            if parts[2] not in ("01h", "06h"):
                continue
            stream = daily.extractfile(member)
            if stream is None:
                continue
            work = Path(temporary)
            raw = work / "native.grb"
            binary = work / "native.bin"
            raw.write_bytes(_decompress(member.name, stream.read(), work))
            executable = shutil.which("wgrib")
            inventory = subprocess.run(
                [executable, str(raw), "-s"], check=True, capture_output=True, text=True
            ).stdout
            if "APCP" not in inventory or len(inventory.strip().splitlines()) != 1:
                raise ValueError(f"Unexpected inventory: {inventory}")
            subprocess.run(
                [executable, str(raw), "-d", "1", "-bin", "-nh", "-o", str(binary)],
                check=True, capture_output=True,
            )
            values = np.fromfile(binary, dtype=np.float32)
            values[values >= 9e20] = np.nan
            fields[(parts[2], int(parts[1][8:]))] = values
    with Dataset(converted) as data:
        lon = np.asarray(data["longitude"][:]).reshape(-1)
        lon = (lon + 180) % 360 - 180
        lat = np.asarray(data["latitude"][:]).reshape(-1)
        west = (lon >= -125) & (lon <= -114) & (lat >= 32) & (lat <= 43)
        report["region"] = "California bounding box, not exact CNRFC mask"
        report["hourly_conversion"] = []
        for hour in range(24):
            native = fields[("01h", hour)]
            local = np.ma.filled(data["APCP_surface"][hour], np.nan).reshape(-1)
            valid = np.isfinite(native) & np.isfinite(local)
            report["hourly_conversion"].append({
                "hour": hour,
                "max_abs_difference_mm": float(np.max(np.abs(native[valid] - local[valid]))),
                "missing_mask_differences": int(np.count_nonzero(
                    np.isfinite(native) != np.isfinite(local)
                )),
            })
    report["native_six_hour_comparison"] = []
    for end in (6, 12, 18):
        six = fields[("06h", end)]
        hourly = np.sum([fields[("01h", h)] for h in range(end - 5, end + 1)], axis=0)
        valid = west & np.isfinite(six) & np.isfinite(hourly)
        report["native_six_hour_comparison"].append({
            "end_hour": end, "valid_cells": int(valid.sum()),
            "hourly_sum_mean_mm": float(hourly[valid].mean()),
            "six_hour_mean_mm": float(six[valid].mean()),
            "hourly_dry_six_hour_wet_cells": int(np.count_nonzero(
                valid & (hourly < .01) & (six > 1)
            )),
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
