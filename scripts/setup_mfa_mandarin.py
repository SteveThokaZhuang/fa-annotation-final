#!/usr/bin/env python3
"""Install the official MFA Mandarin resources into this checkout."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def mfa_command(python: Path, *args: str) -> list[str]:
    return [str(python), '-c',
            'from montreal_forced_aligner.command_line.mfa import mfa_cli; mfa_cli()',
            *args]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mfa-python', type=Path,
                        default=ROOT / 'models/mfa/bin/python')
    parser.add_argument('--mfa-root', type=Path,
                        default=ROOT / 'models/mfa-mandarin/cache')
    args = parser.parse_args()
    python = args.mfa_python.resolve()
    if not python.exists():
        parser.error('MFA runtime does not exist: ' + str(python))

    root = args.mfa_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, MFA_ROOT_DIR=str(root))
    for resource_type, name in [('acoustic', 'mandarin_mfa'),
                                ('dictionary', 'mandarin_china_mfa')]:
        subprocess.run(mfa_command(python, 'model', 'download', resource_type, name),
                       env=env, check=True)

    acoustic = next((p for p in root.rglob('mandarin_mfa*')
                     if p.is_file() and p.suffix == '.zip'), None)
    dictionary = next((p for p in root.rglob('mandarin_china_mfa*')
                       if p.is_file() and p.suffix in {'.dict', '.txt'}), None)
    if acoustic is None or dictionary is None:
        raise RuntimeError('Downloaded resources were not found under ' + str(root))

    acoustic_target = ROOT / 'models/mfa-mandarin/mandarin_mfa.zip'
    dictionary_target = ROOT / 'models/mfa-mandarin/dictionary/mandarin_china_mfa.dict'
    acoustic_target.parent.mkdir(parents=True, exist_ok=True)
    dictionary_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(acoustic, acoustic_target)
    shutil.copy2(dictionary, dictionary_target)
    print('acoustic_model=' + str(acoustic_target))
    print('dictionary=' + str(dictionary_target))
    return 0


if __name__ == '__main__':
    sys.exit(main())
