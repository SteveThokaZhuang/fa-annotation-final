#!/usr/bin/env python3
"""Download official MFA resources for the supported non-English languages.

The archives are intentionally kept outside git.  Run this once in the MFA
environment before selecting a Japanese, Korean, Thai, or Vietnamese dataset.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = {
    'Japanese': ('japanese_mfa', 'japanese_mfa'),
    'Korean': ('korean_mfa', 'korean_mfa'),
    'Thai': ('thai_mfa', 'thai_mfa'),
    'Vietnamese': ('vietnamese_mfa', 'vietnamese_mfa'),
}
HF_ACOUSTIC_FILES = ('acoustic/final.alimdl', 'acoustic/final.mdl',
                     'acoustic/lda.mat', 'acoustic/meta.json',
                     'acoustic/phones.txt', 'acoustic/tree')


def mfa_command(python: Path, *args: str) -> list[str]:
    return [str(python), '-c',
            'from montreal_forced_aligner.command_line.mfa import mfa_cli; mfa_cli()',
            *args]


def find_resource(root: Path, name: str, suffixes: set[str]) -> Path:
    candidates = [p for p in root.rglob(name + '*')
                  if p.is_file() and p.suffix.casefold() in suffixes]
    if not candidates:
        raise RuntimeError(f'Could not find {name} resource under {root}')
    # MFA may retain several versions in its cache. Prefer the shortest path,
    # then the newest file, while keeping the selection deterministic.
    return sorted(candidates, key=lambda p: (len(p.parts), -p.stat().st_mtime_ns, str(p)))[0]


def download_from_huggingface(cache: Path, language: str) -> None:
    """Fallback for MFA's GitHub API rate limit using the HF model mirror."""
    acoustic_name, dictionary_name = LANGUAGES[language]
    repo = f'MontrealCorpusTools/{acoustic_name}'
    staging = cache / 'huggingface' / acoustic_name
    for relative in HF_ACOUSTIC_FILES:
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f'https://huggingface.co/{repo}/resolve/main/{relative}'
        if not target.exists():
            with urllib.request.urlopen(url, timeout=300) as response:
                target.write_bytes(response.read())
    # The current Vietnamese HF model publishes dialect dictionaries; the
    # Hanoi pair is the closest official default and is stored under the
    # stable pipeline resource name.
    dictionary_file = f'{dictionary_name}.dict'
    if language == 'Vietnamese':
        dictionary_file = 'vietnamese_hanoi_mfa.dict'
    target = staging / 'dictionary' / dictionary_file
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f'https://huggingface.co/{repo}/resolve/main/dictionary/{dictionary_file}'
    if not target.exists():
        with urllib.request.urlopen(url, timeout=300) as response:
            target.write_bytes(response.read())
    acoustic = cache / f'{acoustic_name}.zip'
    if not acoustic.exists():
        with zipfile.ZipFile(acoustic, 'w', zipfile.ZIP_DEFLATED) as archive:
            for relative in HF_ACOUSTIC_FILES:
                path = staging / relative
                archive.write(path, f'{acoustic_name}/{path.name}')
    # Present the downloaded dictionary in the same stable name used by the
    # pipeline configuration.
    dictionary = cache / dictionary_file
    if not dictionary.exists():
        shutil.copy2(target, dictionary)


def install_one(python: Path, cache: Path, language: str) -> tuple[Path, Path]:
    acoustic_name, dictionary_name = LANGUAGES[language]
    env = dict(os.environ, MFA_ROOT_DIR=str(cache))
    for resource_type, name in [('acoustic', acoustic_name),
                                ('dictionary', dictionary_name)]:
        subprocess.run(mfa_command(python, 'model', 'download', resource_type, name),
                       env=env, check=True)
    acoustic = find_resource(cache, acoustic_name, {'.zip'})
    dictionary = find_resource(cache, dictionary_name, {'.dict', '.txt'})
    target = ROOT / f'models/mfa-{language.casefold()}'
    acoustic_target = target / f'{acoustic_name}.zip'
    dictionary_target = target / 'dictionary' / f'{dictionary_name}.dict'
    acoustic_target.parent.mkdir(parents=True, exist_ok=True)
    dictionary_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(acoustic, acoustic_target)
    shutil.copy2(dictionary, dictionary_target)
    return acoustic_target, dictionary_target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mfa-python', type=Path,
                        default=ROOT / 'models/mfa/bin/python')
    parser.add_argument('--mfa-root', type=Path,
                        default=ROOT / 'models/mfa-multilingual/cache')
    parser.add_argument('--language', action='append', choices=sorted(LANGUAGES),
                        dest='languages',
                        help='Download only this language; repeat for multiple languages')
    args = parser.parse_args()
    python = args.mfa_python.resolve()
    if not python.exists():
        parser.error('MFA runtime does not exist: ' + str(python))
    cache = args.mfa_root.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    languages = args.languages or list(LANGUAGES)
    for language in languages:
        try:
            acoustic, dictionary = install_one(python, cache, language)
        except subprocess.CalledProcessError as error:
            # MFA 3.x downloads metadata through the GitHub API, which is
            # commonly rate-limited on shared machines.  HF hosts the same
            # official model artifacts and does not require a GitHub token.
            print(f'MFA model CLI failed for {language} ({error}); trying Hugging Face',
                  file=sys.stderr)
            download_from_huggingface(cache, language)
            acoustic_name, dictionary_name = LANGUAGES[language]
            acoustic = cache / f'{acoustic_name}.zip'
            dictionary_name = ('vietnamese_hanoi_mfa' if language == 'Vietnamese'
                               else dictionary_name)
            dictionary = cache / f'{dictionary_name}.dict'
            target = ROOT / f'models/mfa-{language.casefold()}'
            target.mkdir(parents=True, exist_ok=True)
            target.joinpath('dictionary').mkdir(exist_ok=True)
            shutil.copy2(acoustic, target / acoustic.name)
            shutil.copy2(dictionary, target / 'dictionary' / f'{LANGUAGES[language][1]}.dict')
            acoustic = target / acoustic.name
            dictionary = target / 'dictionary' / f'{LANGUAGES[language][1]}.dict'
        print(f'{language}: acoustic_model={acoustic}')
        print(f'{language}: dictionary={dictionary}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

