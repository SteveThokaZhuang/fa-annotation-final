#!/usr/bin/env python3
"""Find foreign-language ASR chunks and run the matching MFA resources.

The ASR language field is not trusted here: the existing run contains short
Japanese/Korean/Thai/Vietnamese phrases inside chunks labelled English or
Chinese.  Script detection is therefore performed on transcript text.  The
script emits a manifest and one resumable MFA result tree per language.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys

try:
    import nagisa
except ImportError:  # Japanese tokenization is optional for non-Qwen callers.
    nagisa = None


ROOT = Path(__file__).resolve().parents[1]
LANGUAGE_PATTERNS = {
    'Japanese': re.compile(r'[\u3040-\u30ff]'),
    'Korean': re.compile(r'[\uac00-\ud7af\u1100-\u11ff]'),
    'Thai': re.compile(r'[\u0e00-\u0e7f]'),
    'Vietnamese': re.compile(r'[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]'),
}
RESOURCES = {
    'Japanese': ('models/mfa-japanese/dictionary/japanese_mfa.dict',
                 'models/mfa-japanese/japanese_mfa.zip'),
    'Korean': ('models/mfa-korean/dictionary/korean_mfa.dict',
               'models/mfa-korean/korean_mfa.zip'),
    'Thai': ('models/mfa-thai/dictionary/thai_mfa.dict',
             'models/mfa-thai/thai_mfa.zip'),
    'Vietnamese': ('models/mfa-vietnamese/dictionary/vietnamese_mfa.dict',
                   'models/mfa-vietnamese/vietnamese_mfa.zip'),
}


def detect_languages(text):
    return [name for name, pattern in LANGUAGE_PATTERNS.items() if pattern.search(text)]


def scan(asr_roots):
    rows = {}
    files = 0
    for root in asr_roots:
        root = Path(root)
        for directory in sorted(root.rglob('batch_results')):
            if '/files/asr' not in str(directory):
                continue
            for path in sorted(directory.rglob('*.json')):
                files += 1
                try:
                    payload = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                for value in payload.get('results', []):
                    text = str(value.get('text', ''))
                    languages = detect_languages(text)
                    if not languages:
                        continue
                    chunk = dict(value.get('chunk') or {})
                    chunk.setdefault('audio_path', chunk.get('path'))
                    chunk.setdefault('audio_start_sample', chunk.get('start_sample'))
                    chunk.setdefault('audio_end_sample', chunk.get('end_sample'))
                    item = dict(id=chunk.get('id'), text=text, chunk=chunk,
                                language=languages[0], detected_languages=languages,
                                asr_language=value.get('language'), source=str(path))
                    if not item['id']:
                        continue
                    # Keep the candidate with the most script evidence when
                    # ASR1/ASR2/ASR3 saved the same chunk more than once.
                    previous = rows.get(item['id'])
                    score = sum(len(pattern.findall(text)) for pattern in LANGUAGE_PATTERNS.values())
                    old_score = (sum(len(pattern.findall(previous['text']))
                                     for pattern in LANGUAGE_PATTERNS.values())
                                 if previous else -1)
                    if previous is None or score > old_score:
                        rows[item['id']] = item
    return files, rows


def write_recordings(rows, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for row in rows:
        chunk = dict(row['chunk'])
        # run_recordings_mfa.py uses these explicit sample bounds when present.
        text = row['text']
        original_text = text
        if row['language'] == 'Japanese' and nagisa is not None:
            text = ' '.join(word for word in nagisa.tagging(text).words if word.strip())
        item = dict(id=row['id'], text=text, original_text=original_text,
                    language=row['language'], chunk=chunk)
        (directory / f"{row['id']}.json").write_text(
            json.dumps(dict(recording_channel=row['id'], text=text, chunks=[item],
                            detected_languages=row['detected_languages']),
                       ensure_ascii=False, indent=2) + '\n')


def compress_cross_language(words, epsilon=0.020, minimum_duration=0.9):
    """Resolve a small cross-language overlap by moving the shared boundary.

    This is the local form of ``docs/compress_algorithm.md``: never move an
    endpoint more than 20 ms and retain at least 90% of each original word.
    Larger overlaps stay flagged for review instead of being silently warped.
    """
    edits = []
    ordered = sorted(words, key=lambda value: (value['start'], value['end']))
    for left, right in zip(ordered, ordered[1:]):
        if left.get('language') == right.get('language') or left['end'] <= right['start']:
            continue
        overlap = left['end'] - right['start']
        if overlap > epsilon:
            continue
        left_duration = left['end'] - left['start']
        right_duration = right['end'] - right['start']
        boundary = (left['end'] + right['start']) / 2
        if (boundary - left['start'] >= minimum_duration * left_duration and
                right['end'] - boundary >= minimum_duration * right_duration and
                abs(boundary - left['end']) <= epsilon and
                abs(boundary - right['start']) <= epsilon):
            left['end'], right['start'] = boundary, boundary
            edits.append(dict(left=left.get('text'), right=right.get('text'),
                              overlap_seconds=overlap, boundary=boundary,
                              mode='bounded_compression'))
    return ordered, edits


def compress_results(output):
    all_rows = []
    audit = dict(cross_language_pairs=0, small_overlaps=0,
                 bounded_compressions=0, large_overlaps=0)
    for path in sorted((output / 'batch_results').glob('*.jsonl.gz')):
        rows = []
        with gzip.open(path, 'rt') as stream:
            for line in stream:
                row = json.loads(line)
                for word in row.get('words', []):
                    word['language'] = row.get('language')
                rows.append(row)
                all_rows.append(row)
        # Rows from separate recordings are independent; only compare words
        # whose absolute coordinates share a recording.
        by_recording = {}
        for row in rows:
            by_recording.setdefault(row.get('recording_id'), []).extend(row.get('words', []))
        edits = []
        for words in by_recording.values():
            ordered = sorted(words, key=lambda value: (value['start'], value['end']))
            for left, right in zip(ordered, ordered[1:]):
                if left.get('language') != right.get('language') and left['end'] > right['start']:
                    overlap = left['end'] - right['start']
                    audit['cross_language_pairs'] += 1
                    audit['small_overlaps' if overlap <= .020 else 'large_overlaps'] += 1
            _, changed = compress_cross_language(words)
            audit['bounded_compressions'] += len(changed)
            edits.extend(changed)
        if edits:
            with gzip.open(path, 'wt', compresslevel=3) as stream:
                for row in rows:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    (output / 'boundary_audit.json').write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    return all_rows, audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--asr-root', action='append', type=Path,
                        default=[Path('/fdd_data/ap3/machines')])
    parser.add_argument('--output', type=Path,
                        default=ROOT.parent / 'run_files/sz-run/multilingual_fa_20261116')
    parser.add_argument('--mfa-python', type=Path,
                        default=ROOT.parent / 'models/mfa/bin/python')
    parser.add_argument('--mfa-jobs', type=int, default=8)
    parser.add_argument('--no-tokenization', action='store_true', default=True,
                        help='Use ASR whitespace exactly; useful for mixed-language chunks')
    args = parser.parse_args()
    files, candidates = scan(args.asr_root)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = dict(asr_files_scanned=files, chunks=len(candidates), languages={})
    for language in LANGUAGE_PATTERNS:
        selected = [row for row in candidates.values() if language in row['detected_languages']]
        manifest['languages'][language] = dict(chunks=len(selected), ids=[row['id'] for row in selected])
        write_recordings(selected, args.output / 'inputs' / language)
    (args.output / 'scan.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))

    runner = ROOT / 'sz-fa/src/run_recordings_mfa.py'
    for language, (dictionary, acoustic) in RESOURCES.items():
        selected = manifest['languages'][language]['chunks']
        if not selected:
            continue
        dictionary, acoustic = ROOT / dictionary, ROOT / acoustic
        if not dictionary.is_file() or not acoustic.is_file():
            manifest['languages'][language]['status'] = 'resources_missing'
            continue
        command = [sys.executable, str(runner), '--recordings',
                   str(args.output / 'inputs' / language), '--output',
                   str(args.output / language), '--mfa-python', str(args.mfa_python),
                   '--dictionary', str(dictionary), '--acoustic-model', str(acoustic),
                   '--mfa-jobs', str(args.mfa_jobs)]
        if args.no_tokenization:
            command.append('--no-tokenization')
        if language == 'Japanese':
            # MFA 3.4's Japanese word-interval export can race its temporary
            # SQLite table under threaded collection; multiprocessing is
            # slower but deterministic for this small follow-up set.
            command.append('--no-threading')
        completed = subprocess.run(command, check=False)
        manifest['languages'][language]['status'] = 'complete' if completed.returncode == 0 else 'failed'
        manifest['languages'][language]['returncode'] = completed.returncode
        if completed.returncode != 0:
            break
        _, audit = compress_results(args.output / language)
        manifest['languages'][language]['boundary_audit'] = audit
    (args.output / 'scan.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return 0 if all(v.get('status') in ('complete', 'resources_missing')
                     for v in manifest['languages'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())

