"""Reject company CAD/data and local development records from release trees."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import PurePosixPath


PRIVATE_DIRECTORIES = {
    'output', 'outputs', 'models', 'company_data', 'data', 'benchmark_data', 'benchmarks',
    'experiment', 'experiments', 'results', 'reports', 'snapshots', 'project_knowledge',
    '.codex_runtime', '.agents', '.codex', '.aws', 'node_modules', 'pyoccenv', '__pycache__',
}
PRIVATE_EXTENSIONS = {
    '.stp', '.step', '.stl', '.dxf', '.dwg', '.iges', '.igs', '.brep', '.obj', '.3mf',
    '.db', '.sqlite', '.sqlite3', '.sql', '.dump', '.bak', '.pkl', '.pickle', '.parquet',
    '.csv', '.xlsx', '.xls', '.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.zip', '.log',
    '.pem', '.key', '.pfx', '.p12',
}
PRIVATE_FILES = {'AGENTS.md', 'CHANGELOG.md', 'current_progress.md', 'commit_msg.txt', 'examples_out.json', 'test_single_out.txt'}
PUBLIC_JSON = {
    'docs/generated/api_graph.json',
    'experimental_lab/tolerance_extraction_audit/DRAWING_CURRENT_FINAL/ENGINE_MANIFEST.json',
    'auto_2d_drawing/templates/housing_standard_preset.json',
    'auto_2d_drawing/templates/impeller_standard_preset.json',
    'auto_2d_drawing/templates/shaft_standard_preset.json',
    'web_app/frontend/package.json', 'web_app/frontend/package-lock.json',
    'web_app/frontend/tsconfig.json', 'web_app/frontend/tsconfig.app.json', 'web_app/frontend/tsconfig.node.json',
}
SECRET_PATTERN = re.compile(
    r'(?:AKIA|ASIA)[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY'
    r'|(?:postgres(?:ql)?|mysql)://[^\s:]+:[^\s@]+@'
)


def private_path(path: str) -> bool:
    path = path.replace('\\', '/')
    value = PurePosixPath(path)
    parts = [part.lower() for part in value.parts]
    if any(part in PRIVATE_DIRECTORIES for part in parts[:-1]):
        return True
    if value.suffix.lower() in PRIVATE_EXTENSIONS or re.search(r'\.(?:db|sqlite3?)-(?:wal|shm|journal)$', value.name, re.I):
        return True
    if value.name in PRIVATE_FILES or (value.name.startswith('.env') and path != '.env.example'):
        return True
    if value.suffix.lower() == '.json' and path not in PUBLIC_JSON:
        return True
    if path.startswith('docs/') and path not in {'docs/deployment-docker.md', 'docs/generated/api_graph.json'}:
        return True
    if path.startswith('experimental_lab/'):
        if value.suffix.lower() == '.md':
            return True
        if '/CURRENT_FINAL/' in path or '/common/config/' in path or '/common/configs/' in path or '/common/templates/' in path:
            return True
    return False


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', *args])


def check_tree(tree: str) -> list[str]:
    failures = []
    entries = git('ls-tree', '-r', '-z', tree).split(b'\0')
    for entry in entries:
        if not entry:
            continue
        metadata, raw_path = entry.split(b'\t', 1)
        path = raw_path.decode('utf-8', errors='replace')
        if private_path(path):
            failures.append(f'private path: {path}')
            continue
        _mode, kind, oid = metadata.split()
        if kind != b'blob':
            continue
        content = git('cat-file', 'blob', oid.decode()).decode('utf-8', errors='replace')
        for line, value in enumerate(content.splitlines(), 1):
            if SECRET_PATTERN.search(value):
                failures.append(f'credential signature: {path}:{line}')
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', default='HEAD', help='commit/tree to inspect')
    parser.add_argument('--staged', action='store_true', help='inspect exact staged tree')
    args = parser.parse_args()
    tree = git('write-tree').decode().strip() if args.staged else args.tree
    failures = check_tree(tree)
    for failure in failures[:30]:
        print(failure)
    print(f'Release content: {"FAIL" if failures else "PASS"}; {len(failures)} findings.')
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
