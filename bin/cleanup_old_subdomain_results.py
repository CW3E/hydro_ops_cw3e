"""Inventory and remove superseded model data, preserving new spinups and evidence."""
import json
import os
from pathlib import Path

from run_repaired_retro import CAMPAIGN, GATES

from hydro_ops.wrf_hydro.production import write_json


def targets(root):
    for domain in GATES:
        for kind in ('outputs', 'restarts'):
            for stream in ('retro', 'nrt'):
                base = root/'nwm'/kind/domain/stream
                if not base.exists():
                    continue
                for child in sorted(base.iterdir()):
                    allowed = (child.name in ('hourly', 'daily', 'production_v1') if kind == 'outputs'
                               else child.name == 'production_v1' or
                               (child.name.isdigit() and len(child.name) == 4))
                    if allowed:
                        if child.is_symlink():
                            raise ValueError(f'Refusing symlink cleanup root: {child}')
                        yield child


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    state = root/'nwm/status'/CAMPAIGN
    state.mkdir(parents=True, exist_ok=True)
    report_path = state/'cleanup.json'
    if report_path.exists():
        raise ValueError('Cleanup already attempted; inspect inventory before retry')
    for domain, job in GATES.items():
        record = json.loads((root/'nwm/outputs'/domain/'tests'/
                             f'repaired-retro-start-{job}'/'acceptance.json').read_text())
        if record.get('status') != 'passed' or record.get('restart_equivalence') != 'passed':
            raise ValueError(f'Startup test not passed: {domain}')
    plan = state/'deletion-plan.jsonl'
    report = {'status': 'inventory', 'job_id': os.environ['SLURM_JOB_ID'],
              'files': 0, 'bytes': 0, 'deleted': 0, 'roots': list(map(str, targets(root)))}
    write_json(report_path, report)
    try:
        with plan.open('x') as inventory:
            for directory in map(Path, report['roots']):
                for current, dirs, files in os.walk(directory, followlinks=False):
                    if any((Path(current)/d).is_symlink() for d in dirs):
                        raise ValueError('Symlink directory inside deletion scope')
                    for name in files:
                        if not any(t in name for t in ('LDASOUT', 'CHRTOUT', 'HYDRO_RST.', 'RESTART.',
                                                       'RTOUT', 'LAKEOUT', 'GWOUT', 'LSMOUT', 'CHANOBS')):
                            continue
                        if name.endswith(('.json', '.jsonl', '.log', '.txt')):
                            continue
                        p = Path(current)/name
                        if p.is_symlink() or not p.is_file():
                            raise ValueError(f'Unexpected deletion candidate: {p}')
                        st = p.stat()
                        row = {'path': str(p), 'identity': [st.st_ino, st.st_size, st.st_mtime_ns]}
                        inventory.write(json.dumps(row)+'\n')
                        report['files'] += 1
                        report['bytes'] += st.st_size
            inventory.flush()
            os.fsync(inventory.fileno())
        report['status'] = 'deleting'
        write_json(report_path, report)
        with plan.open() as inventory, (state/'deleted.jsonl').open('x') as journal:
            for line in inventory:
                row = json.loads(line)
                p = Path(row['path'])
                st = p.stat()
                if p.is_symlink() or [st.st_ino, st.st_size, st.st_mtime_ns] != row['identity']:
                    raise ValueError(f'Deletion target changed: {p}')
                p.unlink()
                journal.write(line)
                report['deleted'] += 1
                if report['deleted'] % 10000 == 0:
                    journal.flush()
                    write_json(report_path, report)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write_json(report_path, report)


if __name__ == '__main__':
    main()
