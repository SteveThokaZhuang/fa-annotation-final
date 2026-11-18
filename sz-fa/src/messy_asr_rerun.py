#!/usr/bin/env python3
"""Re-run ASR for messy chunks, then for every chunk in affected speeches.

The input is the ``rerun_inputs.jsonl`` emitted by process_speeches.py.  It
contains two ordered scopes: ``chunk`` first and ``speech`` second.  The
result file is consumed by process_speeches.py on its second pass, where the
last (speech-scope) prediction wins and persistent messy text is failed.
"""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--device', default='cuda:0')
    p.add_argument('--batch-size', type=int, default=4)
    args = p.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding='utf-8').splitlines() if line.strip()]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        args.output.write_text('', encoding='utf-8')
        print(json.dumps({'status': 'complete', 'rows': 0}))
        return
    import torch
    from qwen_asr import Qwen3ASRModel

    model = Qwen3ASRModel.from_pretrained(
        str(args.checkpoint), dtype=torch.bfloat16, device_map=args.device,
        attn_implementation='sdpa', max_inference_batch_size=args.batch_size,
        max_new_tokens=512)
    predictions = []
    for offset in range(0, len(rows), args.batch_size):
        batch = rows[offset:offset + args.batch_size]
        audio = [row['audio'] for row in batch]
        values = model.transcribe(audio=audio, language=None, return_time_stamps=False)
        if len(values) != len(batch):
            raise RuntimeError(f'ASR returned {len(values)} values for {len(batch)} inputs')
        for row, value in zip(batch, values):
            predictions.append(dict(
                scope=row['scope'], speech_id=row['speech_id'], chunk_id=row['chunk_id'],
                id=row['id'], text=value.text or '', language=getattr(value, 'language', None),
                audio=row['audio'],
            ))
        print(f'messy ASR {min(offset + len(batch), len(rows))}/{len(rows)}', flush=True)
    args.output.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in predictions), encoding='utf-8')
    print(json.dumps({'status': 'complete', 'rows': len(predictions),
                      'chunk_scope': sum(row['scope'] == 'chunk' for row in predictions),
                      'speech_scope': sum(row['scope'] == 'speech' for row in predictions)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
