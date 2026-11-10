#!/usr/bin/env python3
"""Historical read-only drainage screen for the Quebec precipitation anomaly.

Fixed pilot coordinates, not a generic watershed tool or routine operation.
Retained for reproducibility; review domain/grid assumptions before using SLURM.
"""

import json
import os
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def main():
    root = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    domain = root / "nwm/static/operational/nwm.v3.1.6/domain"
    output = root / "forcing/work/prism-quebec-drainage" / os.environ["SLURM_JOB_ID"]
    output.mkdir(parents=True, exist_ok=False)
    # Forcing rows run south-to-north; Fulldom rows run north-to-south.
    sy0, sx0 = (3840 - 3159 - 1) * 4, 4220 * 4
    cy, cx = sy0 + 2, sx0 + 2
    with Dataset(domain / "Fulldom_CONUS_FullRouting.nc") as ds:
        y0, x0 = cy - 512, cx - 512
        yy, xx = slice(y0, cy + 512), slice(x0, cx + 512)
        flow = np.ma.asarray(ds["FLOWDIRECTION"][yy, xx]).filled(-9999)
        channels = np.ma.asarray(ds["CHANNELGRID"][yy, xx]).filled(-9999)
        lat = np.asarray(ds["LATITUDE"][yy, xx])
        lon = np.asarray(ds["LONGITUDE"][yy, xx])
    if abs(float(lat[512, 512]) - 48.449043) > 0.05 or abs(float(lon[512, 512]) + 69.585632) > 0.05:
        raise ValueError("Routing/forcing grid alignment check failed")
    with Dataset(domain / "RouteLink_CONUS.nc") as ds:
        links, downstream = np.asarray(ds["link"][:]), np.asarray(ds["to"][:])
        rl_lat, rl_lon = np.asarray(ds["lat"][:]), np.asarray(ds["lon"][:])
    sort = np.argsort(links)
    ordered = links[sort]
    north = 1 if lat[513, 512] > lat[512, 512] else -1
    delta = {
        1: (0, 1),
        2: (-north, 1),
        4: (-north, 0),
        8: (-north, -1),
        16: (0, -1),
        32: (north, -1),
        64: (north, 0),
        128: (north, 1),
    }
    paths = []
    for sy in range(sy0, sy0 + 4):
        for sx in range(sx0, sx0 + 4):
            y, x = sy - y0, sx - x0
            visited = set()
            trail = []
            route = []
            reason = "step_limit"
            for _ in range(10000):
                if not (0 <= y < flow.shape[0] and 0 <= x < flow.shape[1]):
                    reason = "exited_local_tile"
                    break
                if (y, x) in visited:
                    reason = "terrain_cycle"
                    break
                visited.add((y, x))
                trail.append([float(lat[y, x]), float(lon[y, x])])
                reach = int(channels[y, x])
                idx = np.searchsorted(ordered, reach)
                if idx < len(ordered) and ordered[idx] == reach:
                    seen = set()
                    while reach > 0 and reach not in seen:
                        seen.add(reach)
                        idx = np.searchsorted(ordered, reach)
                        if idx == len(ordered) or ordered[idx] != reach:
                            reason = "unresolved_reach"
                            break
                        j = sort[idx]
                        route.append(
                            {"link": int(reach), "lat": float(rl_lat[j]), "lon": float(rl_lon[j])}
                        )
                        reach = int(downstream[j])
                    else:
                        reason = "route_outlet" if reach <= 0 else "route_cycle"
                    break
                direction = int(flow[y, x])
                if direction not in delta:
                    reason = f"terrain_terminal_flow_{direction}_channel_{int(channels[y, x])}"
                    break
                dy, dx = delta[direction]
                y += dy
                x += dx
            paths.append(
                {
                    "start_routing_yx": [sy, sx],
                    "terrain_cells": len(trail),
                    "terrain_start": trail[0],
                    "terrain_end": trail[-1],
                    "terrain_min_lat": min(v[0] for v in trail),
                    "terminal": reason,
                    "downstream_reaches": route,
                }
            )
    report = {
        "status": "completed",
        "production_modified": False,
        "scope": "one representative anomalous 1-km cell; not a domain-wide connectivity mask",
        "land_cell_yx": [3159, 4220],
        "routing_to_land_ratio": 4,
        "north_row_step": north,
        "paths": paths,
    }
    (output / "drainage.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
