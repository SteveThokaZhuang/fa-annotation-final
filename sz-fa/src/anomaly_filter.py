#!/usr/bin/env python3
"""Split complete speech FA records from auditable anomalous speech records."""
import argparse, json, re
from pathlib import Path


def read_jsonl(path):
    if not path or not Path(path).exists(): return []
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def load_mfa_background(path):
    if not path or not Path(path).exists(): return []
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    return value if isinstance(value, list) else value.get('background_audio', [])


def load_list(path):
    if not path or not Path(path).exists(): return []
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    return value if isinstance(value, list) else []


def speech_key(row):
    return row.get('speech_id') or row.get('conversation_id') or row.get('session_id')


def safe_name(value):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--speech-archive', type=Path, required=True)
    p.add_argument('--output-data', type=Path, required=True)
    p.add_argument('--output-anomalies', type=Path, required=True)
    p.add_argument('--background-audio', type=Path)
    p.add_argument('--failure-speeches', type=Path)
    p.add_argument('--qwen-predictions', type=Path)
    a = p.parse_args()
    speeches = json.loads(a.speech_archive.read_text(encoding='utf-8'))
    background = load_mfa_background(a.background_audio)
    failed_speeches = load_list(a.failure_speeches)
    background_by_id = {x.get('id'): x for x in background if x.get('id')}
    qwen = {x.get('id'): x for x in read_jsonl(a.qwen_predictions)}
    a.output_data.mkdir(parents=True, exist_ok=True); a.output_anomalies.mkdir(parents=True, exist_ok=True)
    clean, anomalous, counts = [], [], {}
    for speech in speeches:
        sid = speech_key(speech); reasons = []; chunk_flags = {}
        chunks = speech.get('chunks', [])
        speakers = {x.get('speaker') for x in chunks if x.get('speaker')}
        declared_speakers = speech.get('speakers', {})
        if len(speakers) < 2 or len([k for k, v in declared_speakers.items() if v]) < 2 or speech.get('speaker_count', 0) < 2:
            reasons.append('only_one_speaker_dialogue')
        # A mixed-language speech is an expected Qwen-FA route.  It is not an
        # anomaly by itself; only the route's alignment errors are anomalous.
        if speech.get('route') == 'failed by asr messy code':
            reasons.append('asr_messy_code')
        for chunk in chunks:
            flags = []
            langs = set(chunk.get('languages') or []) - {'unknown'}
            if len(langs) > 1: flags.append('multi_language')
            if chunk.get('id') in background_by_id:
                flags.append('missing_fa_alignment')
            if chunk.get('messy_code') or chunk.get('alignment_status') == 'failed':
                flags.append('asr_messy_code' if chunk.get('messy_code') else 'alignment_error')
            q = qwen.get(chunk.get('id'))
            if q and (q.get('error') or (q.get('text','').strip() and not q.get('words'))):
                flags.append('qwen_fa_alignment_error')
            if q and any(abs(float(w.get('end', 0)) - float(w.get('start', 0))) <= 1e-7 for w in q.get('words', [])):
                flags.append('zero_duration_word')
            if flags:
                chunk_flags[chunk.get('id')] = sorted(set(flags))
                reasons.extend(flags)
        if any(x.get('conversation_id') == sid for x in background): reasons.append('missing_fa_alignment')
        reasons = sorted(set(reasons))
        record = dict(speech)
        record['anomaly'] = bool(reasons)
        record['anomaly_reasons'] = reasons
        record['anomaly_chunks'] = chunk_flags
        if reasons:
            anomalous.append(record); counts[sid] = reasons
            (a.output_anomalies / f'{safe_name(sid)}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        else:
            clean.append(record)
            (a.output_data / f'{safe_name(sid)}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    for speech in failed_speeches:
        sid = speech_key(speech)
        record = dict(speech, anomaly=True,
                     anomaly_reasons=['fa_failure_speech'],
                     anomaly_chunks={})
        anomalous.append(record)
        counts[sid] = ['fa_failure_speech']
        (a.output_anomalies / f'{safe_name(sid)}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    summary = dict(status='complete', total_speeches=len(speeches), clean_speeches=len(clean),
                   anomalous_speeches=len(anomalous), anomaly_counts={
                       key: sum(key in reasons for reasons in counts.values())
                       for key in sorted({r for reasons in counts.values() for r in reasons})},
                   clean_dir=str(a.output_data), anomaly_dir=str(a.output_anomalies),
                   anomaly_speech_ids=sorted(counts))
    (a.output_anomalies / 'anomaly_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    (a.output_data / 'clean_summary.json').write_text(json.dumps({k: summary[k] for k in ('status','total_speeches','clean_speeches')}, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
