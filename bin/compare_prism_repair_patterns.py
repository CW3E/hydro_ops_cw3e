#!/usr/bin/env python3
"""Read-only, tiled comparison of accepted CONUS PRISM candidate rainfall."""

import json
import os
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from netCDF4 import Dataset


def main():
    root = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    campaign = root / "forcing/work/prism-conus-production"
    output = root / "forcing/work/prism-pattern-comparison" / os.environ["SLURM_JOB_ID"]
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    for job in ("5081424", "5081425"):
        if json.loads((campaign / job / "acceptance.json").read_text())["status"] != "passed":
            raise ValueError("Unaccepted candidate")
    with Dataset(
        root / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc"
    ) as ds:
        active = np.asarray(ds["active"][:], bool)
        lat, lon = np.asarray(ds["lat"][:]), np.asarray(ds["lon"][:])
    results = []
    for kind, stamps in (
        ("windows", ("20140115", "20140116", "20140117")),
        ("hourly", ("20140115", "20140116")),
    ):
        for stamp in stamps:
            suffix = Path(kind) / "2014/01" / f"{stamp}.LDASIN_DOMAIN1"
            maps = {
                k: np.full(active.shape, np.nan, dtype="f4")
                for k in ("reference", "repair", "difference", "timing_shift")
            }
            stats = {
                "kind": kind,
                "stamp": stamp,
                "common_active_cells": 0,
                "mask_difference_cell_hours": 0,
                "active_mask_difference_cell_hours": 0,
                "total_abs_difference_sum_mm": 0.0,
                "max_abs_total_difference_mm": 0.0,
                "total_difference_over_001mm_cells": 0,
                "hourly_abs_difference_sum_mm": 0.0,
                "max_hourly_difference_mm": 0.0,
                "wet_common_cells": 0,
                "timing_shift_sum": 0.0,
                "timing_shift_over_10pct_cells": 0,
                "reference_active_volume_mm_cells": 0.0,
                "repair_active_volume_mm_cells": 0.0,
            }
            transition = np.zeros((5, 5), dtype="i8")
            with (
                Dataset(campaign / "5081424" / suffix) as ref,
                Dataset(campaign / "5081425" / suffix) as rep,
            ):
                for y in range(0, active.shape[0], 128):
                    sl = slice(y, min(y + 128, active.shape[0]))
                    a = np.ma.asarray(ref["RAINRATE"][:, sl, :], dtype="f8").filled(np.nan) * 3600
                    b = np.ma.asarray(rep["RAINRATE"][:, sl, :], dtype="f8").filled(np.nan) * 3600
                    mismatch = np.isfinite(a) != np.isfinite(b)
                    stats["mask_difference_cell_hours"] += int(mismatch.sum())
                    stats["active_mask_difference_cell_hours"] += int((mismatch & active[sl]).sum())
                    sa, sb = a.sum(axis=0), b.sum(axis=0)
                    good = active[sl] & np.isfinite(sa) & np.isfinite(sb)
                    delta = sb - sa
                    n = int(good.sum())
                    stats["common_active_cells"] += n
                    stats["total_abs_difference_sum_mm"] += float(abs(delta[good]).sum())
                    stats["total_difference_over_001mm_cells"] += int(
                        (abs(delta[good]) > 0.01).sum()
                    )
                    if n:
                        stats["max_abs_total_difference_mm"] = max(
                            stats["max_abs_total_difference_mm"], float(abs(delta[good]).max())
                        )
                        stats["max_hourly_difference_mm"] = max(
                            stats["max_hourly_difference_mm"],
                            float(abs(b[:, good] - a[:, good]).max()),
                        )
                    stats["hourly_abs_difference_sum_mm"] += float(
                        abs(b[:, good] - a[:, good]).sum()
                    )
                    stats["reference_active_volume_mm_cells"] += float(sa[good].sum())
                    stats["repair_active_volume_mm_cells"] += float(sb[good].sum())
                    wet = good & (sa >= 0.1) & (sb >= 0.1)
                    shift = np.full(sa.shape, np.nan)
                    shift[wet] = 0.5 * np.sum(
                        abs(a[:, wet] / sa[wet] - b[:, wet] / sb[wet]), axis=0
                    )
                    stats["wet_common_cells"] += int(wet.sum())
                    stats["timing_shift_sum"] += float(shift[wet].sum())
                    stats["timing_shift_over_10pct_cells"] += int((shift[wet] > 0.1).sum())
                    if kind == "windows":
                        ta = np.asarray(ref["prism_timing_source_id"][0, sl, :], dtype=int)
                        tb = np.asarray(rep["prism_timing_source_id"][0, sl, :], dtype=int)
                        transition += np.bincount((5 * ta[wet] + tb[wet]), minlength=25).reshape(
                            5, 5
                        )
                    for name, values in (
                        ("reference", sa),
                        ("repair", sb),
                        ("difference", delta),
                        ("timing_shift", shift),
                    ):
                        maps[name][sl] = np.where(good, values, np.nan)
            n, wetn = stats["common_active_cells"], stats["wet_common_cells"]
            stats["mean_absolute_total_difference_mm"] = stats["total_abs_difference_sum_mm"] / max(
                n, 1
            )
            stats["mean_absolute_hourly_difference_mm"] = stats[
                "hourly_abs_difference_sum_mm"
            ] / max(24 * n, 1)
            stats["mean_wet_timing_shift_fraction"] = stats["timing_shift_sum"] / max(wetn, 1)
            stats["timing_transition_wet_active"] = transition.tolist()
            magnitude = np.nan_to_num(abs(maps["difference"]), nan=-1).ravel()
            top = np.argpartition(magnitude, -20)[-20:]
            top = top[np.argsort(magnitude[top])[::-1]]
            stats["largest_total_differences"] = [
                {"y": int(i // active.shape[1]), "x": int(i % active.shape[1]),
                 "lat": float(lat.ravel()[i]), "lon": float(lon.ravel()[i]),
                 "reference_mm": float(maps["reference"].ravel()[i]),
                 "repair_mm": float(maps["repair"].ravel()[i]),
                 "difference_mm": float(maps["difference"].ravel()[i])}
                for i in top if magnitude[i] > .01
            ]
            stats["timing_codes"] = [
                "unconstrained",
                "archived_or_baseline",
                "nldas",
                "uniform",
                "dry",
            ]
            if kind == "windows":
                diag = Path("windows/2014/01") / f"{stamp}.prism_precipitation_diagnostics.nc"
                with (
                    Dataset(campaign / "5081424" / diag) as a,
                    Dataset(campaign / "5081425" / diag) as b,
                ):
                    ha, hb = (
                        np.asarray(a["hard_mask"][:], bool),
                        np.asarray(b["hard_mask"][:], bool),
                    )
                    wa, wb = (
                        np.asarray(a["weak_mask"][:], bool),
                        np.asarray(b["weak_mask"][:], bool),
                    )
                indices = np.flatnonzero((ha != hb) | (wa != wb))
                prism = (
                    root
                    / "forcing/inputs/oregon_state/prism/an/4km/daily/ppt/2014/01"
                    / f"prism_ppt_us_25m_{stamp}.nc"
                )
                with Dataset(prism) as ds:
                    width = ds["ppt"].shape[-1]
                    stats["target_scope_differences"] = [
                        {"index": int(i),
                         "lat": float(ds["lat"][i // width]),
                         "lon": float(ds["lon"][i % width]),
                         "reference_hard": bool(ha[i]),
                         "repair_hard": bool(hb[i]),
                         "reference_weak": bool(wa[i]),
                         "repair_weak": bool(wb[i])}
                        for i in indices
                    ]
            fig, axes = plt.subplots(2, 2, figsize=(15, 9), constrained_layout=True)
            for ax, name in zip(axes.flat, maps, strict=True):
                limits = (
                    (-2, 2)
                    if name == "difference"
                    else (0, 1)
                    if name == "timing_shift"
                    else (0, 20)
                )
                im = ax.pcolormesh(
                    lon[::8, ::8],
                    lat[::8, ::8],
                    maps[name][::8, ::8],
                    shading="auto",
                    cmap="RdBu_r" if name == "difference" else "viridis",
                    vmin=limits[0],
                    vmax=limits[1],
                    rasterized=True,
                )
                ax.set(xlim=(-125, -67), ylim=(25, 50), title=name.replace("_", " "))
                fig.colorbar(
                    im,
                    ax=ax,
                    label="fraction redistributed in time" if name == "timing_shift" else "mm",
                )
            fig.suptitle(
                f"{stamp}: {'PRISM 12–12 window' if kind == 'windows' else '00–23 calendar records'}; active cells only\nDifference = repair − reference; map sampled every 8 grid cells, statistics use full grid"
            )
            fig.savefig(output / f"{kind}-{stamp}.png", dpi=140)
            plt.close(fig)
            results.append(stats)
            print(json.dumps(stats), flush=True)
            (output / "comparison.json").write_text(
                json.dumps({"status": "running", "comparisons": results}, indent=2) + "\n"
            )
    (output / "comparison.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "production_modified": False,
                "elapsed_seconds": time.monotonic() - started,
                "reference_job": "5081424",
                "repair_job": "5081425",
                "comparisons": results,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
