#!/usr/bin/env python3
"""Build speech archives and speech-level MFA/Qwen-FA route inputs."""
import argparse
import collections
import hashlib
import json
import re
import shutil
from pathlib import Path
from english_backchannel_mapping import load_mapping, map_english_backchannels, mapping_summary

REPO_ROOT = Path(__file__).resolve().parents[2]

LANG_RANGES = {
    'zh': re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]'),
    'ja': re.compile(r'[\u3040-\u30ff\u31f0-\u31ff]'),
    'ko': re.compile(r'[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]'),
    'th': re.compile(r'[\u0e00-\u0e7f]'),
    'vi': re.compile(r'[ăâđêôơưĂÂĐÊÔƠƯ]'),
    'ar': re.compile(r'[\u0600-\u06ff]'),
    'ru': re.compile(r'[\u0400-\u04ff]'),
    'hi': re.compile(r'[\u0900-\u097f]'),
}
LANG_ALIASES = {
    'english': 'en', 'en': 'en', 'en-us': 'en', 'en_us': 'en',
    'chinese': 'zh', 'mandarin': 'zh', '中文': 'zh', '普通话': 'zh',
    'zh': 'zh', 'zh-cn': 'zh', 'zh_cn': 'zh',
    'japanese': 'ja', '日语': 'ja', 'ja': 'ja', 'ja-jp': 'ja',
    'korean': 'ko', '韩语': 'ko', 'ko': 'ko', 'ko-kr': 'ko',
    'vietnamese': 'vi', '越南语': 'vi', 'vi': 'vi',
    'thai': 'th', '泰语': 'th', 'th': 'th',
    'arabic': 'ar', 'ar': 'ar', 'russian': 'ru', 'ru': 'ru',
    'hindi': 'hi', 'hi': 'hi',
}
MESSY = re.compile(r'[\ufffd\x00]|(?:[ÃÂÐ][\x80-\xbf])')


def normalize_language(value):
    if value is None:
        return None
    text = str(value).strip().casefold()
    if not text:
        return None
    if text in LANG_ALIASES:
        return LANG_ALIASES[text]
    for token in re.split(r'[\s,()/|:_-]+', text):
        if token in LANG_ALIASES:
            return LANG_ALIASES[token]
    return None


def languages(text, evidence=()):
    """Return all languages found in one chunk.

    Han is shared by Chinese/Japanese; kana wins unless Chinese evidence is
    explicit. Plain Latin is English only when Vietnamese evidence is absent.
    """
    text = text or ''
    evidence_codes = {code for value in evidence if (code := normalize_language(value))}
    found = {key for key, pattern in LANG_RANGES.items() if pattern.search(text)}
    if 'ja' in found and 'zh' not in evidence_codes:
        found.discard('zh')
    found.update(evidence_codes)
    if re.search(r'[A-Za-z]', text) and not evidence_codes.intersection({'vi'}):
        found.add('en')
    return sorted(found) or ['unknown']


def messy(text):
    return bool(MESSY.search(text or ''))


def hypothesis_languages(row):
    values = []
    for key in ('language', 'input_language', 'language_verified', 'lang'):
        value = row.get(key)
        values.extend(value if isinstance(value, (list, tuple)) else [value] if value else [])
    for hypothesis in row.get('hypotheses', []) or []:
        if isinstance(hypothesis, dict):
            values.extend(hypothesis.get(key) for key in ('language', 'lang') if hypothesis.get(key))
    return values


def words_for(row):
    words = row.get('words') or row.get('force_align', {}).get('words') or []
    if words and isinstance(words[0], dict):
        return words
    text = str(row.get('text') or '').strip()
    if not text:
        return []
    tokens = re.findall(r'\S+', text)
    start = float(row.get('start', row.get('chunk_start', 0)))
    end = float(row.get('end', row.get('chunk_end', start)))
    step = max(0.0, end - start) / max(1, len(tokens))
    return [dict(text=token, start=start + i * step, end=start + (i + 1) * step,
                 source='asr_fallback') for i, token in enumerate(tokens)]


def materialize_chunk(row, destination):
    import numpy as np
    import soundfile as sf
    source = row.get('audio_path') or row.get('path') or row.get('audio')
    if not source:
        raise ValueError('missing_audio_path:' + row['id'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(source) as handle:
        start = round(float(row['start']) * handle.samplerate)
        end = round(float(row['end']) * handle.samplerate)
        handle.seek(start)
        data = handle.read(max(0, end - start), dtype='float32', always_2d=True)
        channel = int(row.get('channel', 0))
        if channel < 0 or channel >= data.shape[1]:
            raise ValueError('invalid_audio_channel:' + row['id'])
        samples = data[:, channel]
        if len(samples) != max(0, end - start):
            raise ValueError('short_audio_chunk:' + row['id'])
        if not np.isfinite(samples).all():
            raise ValueError('nonfinite_audio_chunk:' + row['id'])
        sf.write(destination, samples, handle.samplerate)
    return str(destination)


def load_recordings(root):
    by = {}
    for path in sorted(Path(root).glob('*.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for index, item in enumerate(payload.get('chunks', payload.get('results', []))):
            chunk = item.get('chunk', item) if isinstance(item, dict) else {}
            if not isinstance(chunk, dict):
                continue
            rid = chunk.get('recording_id') or path.stem
            row = dict(item) if isinstance(item, dict) else {}
            row.update(chunk)
            row['_chunk'] = dict(chunk)
            row['id'] = item.get('id') or chunk.get('id') or f'{path.stem}:{index}'
            row['recording_id'] = rid
            row['text'] = item.get('text', row.get('text', ''))
            row['start'] = float(chunk.get('start', chunk.get('chunk_start', 0)))
            row['end'] = float(chunk.get('end', row['start'] + chunk.get('duration', 0)))
            row['duration'] = max(0., row['end'] - row['start'])
            by.setdefault(rid, []).append(row)
            by.setdefault(path.name, by[rid]); by.setdefault(path.stem, by[rid])
    return by


def read_reruns(path):
    if not path or not Path(path).exists():
        return {}
    values = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            value = json.loads(line)
            values[(value.get('scope', 'chunk'), value.get('speech_id'), value.get('chunk_id'))] = value
    return values


def safe_filename(value):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value))[:160] or 'speech'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--recordings', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rerun-results', type=Path)
    parser.add_argument('--english-backchannel-map', type=Path,
                        default=REPO_ROOT / 'mapping_flexible_v2.json',
                        help='JSON mapping for Chinese backchannels in English-labelled chunks')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    backchannel_mapping = load_mapping(args.english_backchannel_map)
    backchannel_mapping_sha256 = hashlib.sha256(args.english_backchannel_map.read_bytes()).hexdigest()
    mapping = json.loads(args.mapping.read_text(encoding='utf-8'))
    recordings = load_recordings(args.recordings)
    index = {}
    for key, rows in recordings.items():
        index[key] = rows; index[Path(key).name] = rows; index[Path(key).stem] = rows
    reruns = read_reruns(args.rerun_results)
    speeches, background, counts, qwen_input, rerun_rows = [], [], collections.Counter(), [], []
    backchannel_chunks = 0
    backchannel_events = 0
    backchannel_occurrences = 0
    route_inputs = args.output / 'route_inputs'
    if route_inputs.exists():
        shutil.rmtree(route_inputs)
    mfa_recordings = route_inputs / 'mfa_recordings'; qwen_audio = route_inputs / 'qwen_audio_chunks'
    rerun_audio = route_inputs / 'messy_rerun'
    mfa_recordings.mkdir(parents=True); qwen_audio.mkdir(parents=True)

    for manifest_index, manifest in enumerate(mapping):
        speech_id = manifest.get('conversation_id') or manifest.get('session_id') or str(manifest_index)
        all_chunks = []
        for speaker in ('speaker_1', 'speaker_2'):
            ref = manifest.get('rover_' + speaker)
            if not ref:
                continue
            rows = index.get(Path(ref).name) or index.get(Path(ref).stem) or index.get(ref, [])
            for source in rows:
                row = dict(source); row['speaker'] = speaker
                row['language_evidence'] = hypothesis_languages(row)
                old_text = str(row.get('text') or '')
                replacement = reruns.get(('speech', speech_id, row['id'])) or reruns.get(('chunk', speech_id, row['id']))
                if replacement and replacement.get('text') is not None:
                    row.setdefault('asr_rerun_history', []).append(dict(
                        scope=replacement.get('scope', 'chunk'), text_before=old_text,
                        text_after=replacement.get('text', '')))
                    row['text'] = replacement.get('text', '')
                    if replacement.get('language'):
                        row['language'] = replacement['language']
                        row['language_evidence'] = list(row['language_evidence']) + [replacement['language']]
                row['languages'] = languages(row.get('text', ''), row['language_evidence'])
                # Language marking is intentionally completed before this
                # correction. Only chunks carrying English evidence are
                # eligible; ordinary Chinese/Japanese text remains untouched.
                if 'en' in row['languages']:
                    corrected, mapping_events = map_english_backchannels(row.get('text', ''), backchannel_mapping)
                    if mapping_events:
                        row['text_before_english_backchannel_mapping'] = row.get('text', '')
                        row['text'] = corrected
                        row['english_backchannel_mapping'] = mapping_events
                        stats = mapping_summary(mapping_events)
                        backchannel_chunks += 1
                        backchannel_events += stats['replacement_events']
                        backchannel_occurrences += stats['replacement_occurrences']
                        # Recompute the final label after correction so MFA
                        # receives English text and the audit reflects it.
                        row['languages'] = languages(row['text'], row['language_evidence'])
                row['messy_code'] = messy(row.get('text', '')); row['words'] = words_for(row)
                counts.update(row['languages']); all_chunks.append(row)
        if not all_chunks:
            continue
        speech_languages = sorted({lang for row in all_chunks for lang in row['languages'] if lang != 'unknown'}) or ['unknown']
        has_messy = any(row.get('messy_code') for row in all_chunks)
        if has_messy and args.rerun_results:
            route, status = 'failed by asr messy code', 'failed by asr messy code'
        else:
            route, status = ('qwen-fa' if len(speech_languages) > 1 else 'mfa'), ('pending_asr_messy_review' if has_messy else 'complete')
        for row in all_chunks:
            row['speech_language_set'] = speech_languages; row['speech_route'] = route; row['route'] = route
        speech = dict(speech_id=speech_id, route=route, languages=speech_languages,
                      speakers={sp: [r['id'] for r in all_chunks if r['speaker'] == sp] for sp in ('speaker_1', 'speaker_2')},
                      chunks=all_chunks, speaker_count=len({r['speaker'] for r in all_chunks}), status=status)
        if status == 'failed by asr messy code':
            speech['failure_reason'] = 'persistent messy code after chunk and whole speech ASR reruns'
        speeches.append(speech)

        if has_messy and not args.rerun_results:
            for scope, selected in (('chunk', [r for r in all_chunks if r.get('messy_code')]), ('speech', all_chunks)):
                for row in selected:
                    path = rerun_audio / scope / safe_filename(speech_id) / (safe_filename(row['id']) + '.wav')
                    try:
                        audio = materialize_chunk(row, path)
                    except Exception as exc:
                        row['rerun_materialization_error'] = str(exc); continue
                    rerun_rows.append(dict(scope=scope, speech_id=speech_id, chunk_id=row['id'],
                                           id=f'{scope}:{speech_id}:{row["id"]}', audio=audio,
                                           duration=row['duration'], chunk_start=0., chunk_end=row['duration'],
                                           text=row.get('text', ''), language='auto'))
        if route == 'mfa':
            payload = dict(speech_id=speech_id, chunks=[dict(id=row['id'], text=row.get('text', ''),
                              chunk=row['_chunk'], speaker=row['speaker'], language=row['languages'][0] if row['languages'] else 'unknown') for row in all_chunks])
            (mfa_recordings / f'{len(speeches):08d}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        elif route == 'qwen-fa':
            for row in all_chunks:
                qwen_path = qwen_audio / (safe_filename(row['id']) + '.wav'); materialize_chunk(row, qwen_path)
                qwen_input.append(dict(id=row['id'], speech_id=speech_id, speaker=row['speaker'], audio=str(qwen_path),
                                       text=row.get('text', ''), duration=row['duration'], chunk_start=0., chunk_end=row['duration'],
                                       channel=row.get('channel'), language=row['languages'][0] if len(row['languages']) == 1 else 'auto',
                                       source_chunk_start=row['start'], source_chunk_end=row['end'],
                                       source_audio_path=row.get('audio_path') or row.get('path')))

    total_words = qwen_words = qwen_zero_words = zero_words = zero_chunks = zero_speeches = 0
    for speech in speeches:
        speech_zero = False
        for row in speech['chunks']:
            words = row.get('words', []); total_words += len(words)
            zeros = sum(abs(float(w.get('end', 0)) - float(w.get('start', 0))) <= 1e-7 for w in words)
            qwen_words += len(words) if speech['route'] == 'qwen-fa' else 0; qwen_zero_words += zeros if speech['route'] == 'qwen-fa' else 0
            zero_words += zeros; zero_chunks += bool(zeros); speech_zero |= bool(zeros)
        zero_speeches += speech_zero
    rerun_chunks = [row for row in rerun_rows if row['scope'] == 'chunk']; rerun_speech_rows = [row for row in rerun_rows if row['scope'] == 'speech']
    summary = dict(status='complete', speeches=len(speeches), chunks=sum(len(s['chunks']) for s in speeches),
                   routes={'mfa': sum(s['route'] == 'mfa' for s in speeches), 'qwen_fa': sum(s['route'] == 'qwen-fa' for s in speeches),
                           'failed_by_asr_messy_code': sum(s['route'] == 'failed by asr messy code' for s in speeches)},
                   language_counts=dict(counts), qwen_fa_zero_duration_word_rate=qwen_zero_words / qwen_words if qwen_words else 0.,
                   qwen_fa_zero_duration_words=qwen_zero_words, qwen_fa_total_words=qwen_words, zero_duration_words=zero_words,
                   total_words=total_words, zero_duration_chunk_rate=zero_chunks / max(1, sum(len(s['chunks']) for s in speeches)),
                   zero_duration_speech_rate=zero_speeches / max(1, len(speeches)), background_audio_chunks=0,
                   messy_code_chunks=sum(row.get('messy_code', False) for s in speeches for row in s['chunks']),
                   english_backchannel_mapping=dict(enabled=True, mapping_file=str(args.english_backchannel_map),
                                                    mapping_version=backchannel_mapping.get('version'),
                                                    mapping_sha256=backchannel_mapping_sha256,
                                                    chunks_changed=backchannel_chunks,
                                                    replacement_events=backchannel_events,
                                                    replacement_occurrences=backchannel_occurrences),
                   rerun_policy='chunk, then whole speech; persistent messy code => failed by asr messy code',
                   rerun_chunk_count=len(rerun_chunks), rerun_speech_count=len(rerun_speech_rows),
                   route_input_files={'mfa_recordings': str(mfa_recordings), 'qwen_fa_jsonl': str(route_inputs / 'qwen_fa_input.jsonl')})
    (args.output / 'speech_archive.json').write_text(json.dumps(speeches, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'background_audio.json').write_text(json.dumps(background, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'messy_code_rerun.json').write_text(json.dumps({'chunks': rerun_chunks, 'speeches': rerun_speech_rows}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (route_inputs / 'qwen_fa_input.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in qwen_input), encoding='utf-8')
    if rerun_rows:
        (args.output / 'rerun_inputs.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rerun_rows), encoding='utf-8')
    corpus = [
        {'language': 'zh', 'clean': '你好世界', 'messy': '你好�世界'}, {'language': 'en', 'clean': 'hello world', 'messy': 'hello � world'},
        {'language': 'ja', 'clean': 'こんにちは世界', 'messy': 'こんにちは�世界'}, {'language': 'ko', 'clean': '안녕하세요 세계', 'messy': '안녕�하세요 世界'},
        {'language': 'vi', 'clean': 'xin chào thế giới', 'messy': 'xin chào � thế giới'}, {'language': 'th', 'clean': 'สวัสดีโลก', 'messy': 'สวัสดี�โลก'},
        {'language': 'ar', 'clean': 'مرحبا بالعالم', 'messy': 'مرحبا �العالم'}, {'language': 'ru', 'clean': 'привет мир', 'messy': 'привет � мир'},
        {'language': 'hi', 'clean': 'नमस्ते दुनिया', 'messy': 'नमस्ते � दुनिया'},
    ]
    (args.output / 'messy_code_corpus.json').write_text(json.dumps(corpus, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
