"""Read-only workspace inventory for manual cleanup review; never authorizes deletion."""
import argparse
import json
import os
import stat
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


def category(path, links):
    if any(token in path.lower() for token in ('restart', 'recovery', 'originals', 'backup')):
        return 'protected_recovery'
    if links > 1:
        return 'hardlinked_review'
    if Path(path).suffix in {'.json', '.jsonl', '.md', '.log', '.out', '.txt', '.csv'}:
        return 'retain_evidence'
    if Path(path).suffix in {'.nc', '.npy', '.npz', '.grb', '.grb2', '.grib', '.grib2'} or 'DOMAIN1' in path:
        return 'array_review'
    return 'unclassified'


def inventory(base, root, ledger):
    counts, sizes = Counter(), Counter()
    seen, allocated, newest, errors = set(), 0, 0, []
    def error(exc):
        errors.append(str(exc))
    for parent, dirs, files in os.walk(base, followlinks=False, onerror=error):
        # Do not follow directory links to production; record their existence.
        for name in list(dirs):
            if (Path(parent)/name).is_symlink():
                counts['directory_symlink'] += 1
                dirs.remove(name)
        for name in files:
            path = Path(parent)/name
            try:
                s = path.lstat()
                relative = str(path.relative_to(root))
                kind = category(relative, s.st_nlink) if stat.S_ISREG(s.st_mode) else 'symlink_or_special'
                row = {'path': relative, 'category': kind, 'device': s.st_dev,
                       'inode': s.st_ino, 'bytes': s.st_size, 'mtime_ns': s.st_mtime_ns,
                       'links': s.st_nlink, 'allocated_bytes': s.st_blocks*512}
                ledger.write(json.dumps(row)+'\n')
                counts[kind] += 1
                sizes[kind] += s.st_size
                newest = max(newest, s.st_mtime)
                key = (s.st_dev, s.st_ino)
                if stat.S_ISREG(s.st_mode) and key not in seen:
                    seen.add(key)
                    allocated += s.st_blocks*512
            except OSError as exc:
                errors.append(str(exc))
    return {'path': str(base.relative_to(root)), 'counts': dict(counts),
            'logical_bytes_by_category': dict(sizes), 'unique_allocated_bytes': allocated,
            'newest_mtime_utc': datetime.fromtimestamp(newest, UTC).isoformat() if newest else None,
            'errors': errors, 'decision': 'manual_review_required'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    # Source-only reference search: runtime JSON references require separate review.
    sources = {}
    for folder in ('bin', 'slurm', 'src', 'tests', 'docs', 'config', 'cron'):
        for path in (root/folder).rglob('*'):
            if path.is_file() and not path.is_symlink() and path.suffix in {'.py', '.sh', '.md', '.toml', '.in', '.env'}:
                sources[str(path.relative_to(root))] = path.read_text(errors='replace')
    bases = []
    for folder in ('forcing/work', 'nwm/work', 'nwm/outputs/tests', 'observations/work'):
        parent = root/folder
        if parent.exists():
            bases.extend(p for p in parent.iterdir() if p.is_dir() and not p.is_symlink())
    for name in ('tests', 'wrf_hydro_daily_oracle', 'nwm_subset_croton',
                 'conus_spinup_benchmark', 'nwm_subset_mid_atlantic'):
        path = root/'nwm/runs'/name
        if path.is_dir() and not path.is_symlink():
            bases.append(path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ledger_path = args.output.with_suffix('.files.jsonl')
    report = {'mode': 'inventory_only_no_deletion', 'created': datetime.now(UTC).isoformat(),
              'scheduler': subprocess.check_output(
                  ['squeue', '--me', '--noheader', '--format=%i|%j|%T|%o|%Z'], text=True),
              'limitations': 'References are lexical, not a complete dependency graph. Sizes across roots may double-count hard links. No eligibility inferred from age/name.',
              'file_ledger': str(ledger_path), 'workspaces': [], 'scripts': []}
    with ledger_path.open('x') as ledger:
        for base in sorted(bases):
            row = inventory(base, root, ledger)
            row['source_references'] = [p for p, text in sources.items() if base.name in text]
            report['workspaces'].append(row)
            print(json.dumps(row), flush=True)
    for path in sorted(sources):
        p = Path(path)
        if p.parts[0] in ('bin', 'slurm') and any(w in p.name for w in ('test', 'benchmark', 'pilot', 'profile')):
            report['scripts'].append({'path': path, 'references': [other for other, text in sources.items()
                                      if other != path and p.name in text], 'decision': 'manual_review_required'})
    with args.output.open('x') as out:
        json.dump(report, out, indent=2)
        out.write('\n')
    print(json.dumps({'status': 'inventory_complete_review_required', 'report': str(args.output)}), flush=True)


if __name__ == '__main__':
    main()
