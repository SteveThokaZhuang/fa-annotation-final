#!/usr/bin/env python3
"""Merge MFA/Qwen alignment output and isolate failed chunks/speeches."""
import argparse, gzip, json
from pathlib import Path

def rows(root):
    out = {}
    for path in sorted(Path(root).glob('batch_results/*.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line); out[row['id']] = row
    return out


def qwen_rows(path):
    if not path or not Path(path).exists():
        return {}
    out = {}
    with Path(path).open(encoding='utf-8') as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                out[row['id']] = row
    return out

def main():
    p = argparse.ArgumentParser(); p.add_argument('--speech-archive', type=Path, required=True)
    p.add_argument('--mfa-output', type=Path, required=True); p.add_argument('--qwen-output', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); speeches = json.loads(a.speech_archive.read_text(encoding='utf-8'))
    result = rows(a.mfa_output); qwen = qwen_rows(a.qwen_output)
    background, failures, kept_speeches, kept_total = [], [], [], 0
    for speech in speeches:
        original_chunks = [dict(chunk) for chunk in speech.get('chunks', [])]
        kept = []
        speech_failure_reasons = []
        failed_chunks = []
        for chunk in speech.get('chunks', []):
            is_mfa = speech.get('route') == 'mfa'
            aligned = result.get(chunk['id'], {}) if is_mfa else qwen.get(chunk['id'], {})
            backend = 'MFA' if is_mfa else 'Qwen FA'
            reason = aligned.get('error') or (f'Missing {backend} alignment'
                     if chunk.get('text', '').strip() and not aligned.get('words') else None)
            fa_failed = bool(aligned.get('error') or (not aligned.get('words') and not chunk.get('text', '').strip()))
            if reason or fa_failed:
                reason = reason or f'{backend} failed without ASR text'
                if chunk.get('text', '').strip():
                    item = dict(chunk, conversation_id=speech['speech_id'], background_audio_reason=reason,
                                fa_backend=backend, fa_error=aligned.get('error'),
                                mfa_error=aligned.get('error'), fa_status='background_audio',
                                mfa_status='background_audio')
                    background.append(item)
                else:
                    speech_failure_reasons.append(reason)
                    failed_chunks.append(dict(chunk, fa_backend=backend,
                                              fa_error=aligned.get('error'), mfa_error=aligned.get('error'),
                                              failure_reason=reason))
            else:
                if aligned:
                    chunk['words'] = aligned.get('words', [])
                    if not is_mfa:
                        # Qwen receives a physical crop whose local origin is
                        # zero; speech archives keep recording coordinates.
                        offset = float(chunk.get('start', chunk.get('chunk_start', 0.0)))
                        chunk['words'] = [dict(word, start=float(word.get('start', 0.0)) + offset,
                                               end=float(word.get('end', 0.0)) + offset)
                                          for word in chunk['words']]
                    chunk['alignment_status'] = 'failed' if aligned.get('error') else 'aligned'
                    if aligned.get('error'):
                        chunk['alignment_error'] = aligned['error']
                    if aligned.get('issues'):
                        chunk['alignment_issues'] = aligned['issues']
                kept.append(chunk)
        speech['chunks'] = kept
        speech['background_audio_chunks'] = sum(x['conversation_id'] == speech['speech_id'] for x in background)
        speech['speaker_count'] = len({x.get('speaker') for x in kept})
        if speech_failure_reasons:
            failures.append(dict(speech, chunks=original_chunks, retained_chunks=kept,
                                 mfa_failed_chunks=failed_chunks,
                                 failure_reason='fa_failed_without_asr_text',
                                 failure_reasons=sorted(set(speech_failure_reasons)),
                                 status='failed by mfa'))
        else:
            kept_speeches.append(speech)
            kept_total += len(kept)
    a.output.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(kept_speeches, ensure_ascii=False, indent=2)+'\n'
    (a.output/'speech_archive.json').write_text(serialized)
    # The speech archive is the authoritative FA input; remove background
    # chunks there as well as in the dedicated reconciliation directory.
    a.speech_archive.write_text(serialized)
    (a.output/'background_audio.json').write_text(json.dumps(background, ensure_ascii=False, indent=2)+'\n')
    (a.output/'failure_speeches.json').write_text(json.dumps(failures, ensure_ascii=False, indent=2)+'\n')
    qwen_words = qwen_zero_words = qwen_chunks = qwen_failed = 0
    total_words = zero_words = zero_chunks = zero_speeches = 0
    for speech in kept_speeches:
        speech_zero = False
        for chunk in speech.get('chunks', []):
            words = chunk.get('words', [])
            total_words += len(words)
            zeros = sum(abs(float(w.get('end', 0)) - float(w.get('start', 0))) <= 1e-7 for w in words)
            zero_words += zeros; zero_chunks += bool(zeros); speech_zero |= bool(zeros)
        zero_speeches += speech_zero
    for row in qwen.values():
        words = row.get('words', [])
        qwen_words += len(words); qwen_zero_words += sum(abs(float(w.get('end', 0)) - float(w.get('start', 0))) <= 1e-7 for w in words)
        qwen_chunks += 1; qwen_failed += bool(row.get('error') or (row.get('text', '').strip() and not words))
    summary = dict(speeches=len(kept_speeches), input_speeches=len(speeches), chunks=kept_total,
                   background_audio_chunks=len(background), failure_speeches=len(failures),
                   qwen_fa_chunks=qwen_chunks, qwen_fa_words=qwen_words,
                   qwen_fa_zero_duration_words=qwen_zero_words,
                   qwen_fa_zero_duration_word_rate=qwen_zero_words / qwen_words if qwen_words else 0.,
                   qwen_fa_failed_chunks=qwen_failed, source_mfa_output=str(a.mfa_output),
                   source_qwen_output=str(a.qwen_output) if a.qwen_output else None,
                   zero_duration_words=zero_words, total_words=total_words,
                   zero_duration_chunk_rate=zero_chunks / max(1, kept_total),
                   zero_duration_speech_rate=zero_speeches / max(1, len(kept_speeches)),
                   status='complete')
    (a.output/'mfa_reconciliation_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
