# Speech-level FA pipeline

This document describes the completed path from WAV input to the final
speech-level FA archives. The upstream V3 stages still process bounded chunks;
the FA stage joins those chunks back into a speech using
`Mapping_with_rover` before choosing an FA backend.

## Entry points

The production entry point is:

```text
sz-fa/run_fa_pipeline.py
```

The speech-level implementation is:

```text
sz-fa/src/process_speeches.py
```

The supporting stages are:

```text
sz-fa/src/run_recordings_mfa.py       # CPU MFA execution
sz-fa/src/reconcile_mfa.py            # remove ASR-text chunks with MFA failure
sz-fa/src/anomaly_filter.py           # clean speech archive and anomaly archive
sz-fa/src/qwen3-fa/process.py         # Qwen forced aligner
sz-fa/src/qwen3-fa/core.py            # Qwen model wrapper
```

## Data flow

```text
WAV + transcript metadata
  -> ingest
  -> DNSMOS
  -> VAD
  -> enhancement
  -> ASR1/ASR2/disagreement/ASR3
  -> ROVER recording JSON (chunk-level)
  -> process_speeches.py
       - mapping joins speaker_1 and speaker_2
       - language evidence is collected for every chunk
       - one language set is computed for the whole speech
       - single-language speech -> MFA route
       - multi-language speech -> qwen-fa route
       - route-specific input manifests are materialized
  -> MFA route: run_recordings_mfa.py
  -> qwen-fa route: qwen3-fa/process.py
  -> reconcile_mfa.py
       - chunks with ASR text and FA error/no words become background audio
       - chunks without ASR text and FA failure move the whole speech to failure_speeches.json
       - those chunks are removed from the authoritative speech archive
  -> anomaly_filter.py
       - clean speech JSON files
       - anomalous speech JSON files
       - zero-duration and messy-code audit fields
```

## Speech grouping and language routing

`process_speeches.py` reads the two `rover_speaker_*` references from the
mapping manifest. It keeps each chunk's `speaker`, `id`, audio bounds, text,
language evidence, and source provenance. Language evidence comes from the
ROVER hypotheses/transcript metadata and Unicode detection for Chinese,
Japanese, Korean, Thai, Vietnamese, Arabic, Russian, Hindi, and Latin text.

The route is selected once per speech:

```python
route = 'qwen-fa' if len(speech_languages) > 1 else 'mfa'
```

The decision is speech-level even though Qwen and MFA receive chunk-sized
audio files. This preserves exact chunk boundaries and lets the final archive
retain per-chunk language labels while ensuring all chunks in a mixed speech
use the Qwen route.

The generated route inputs are stored under:

```text
speech_fa/route_inputs/mfa_recordings/
speech_fa/route_inputs/qwen_fa_input.jsonl
speech_fa/route_inputs/qwen_audio_chunks/
```

## MFA/Qwen route and background audio

MFA is run only on `mfa_recordings`. `run_recordings_mfa.py` preserves the
original chunk ID and writes word intervals or an error per chunk.

`reconcile_mfa.py` compares those results with the speech archive. The same
rule is applied to both backends: a chunk with ASR text and a FA error or no
aligned words is moved to `background_audio.json`; a chunk with no ASR text
and no FA alignment moves its whole speech to `failure_speeches.json`. Both
the background chunk and failed speech are removed from the authoritative
retained speech archive.

## Qwen route

The default checkpoint is:

```text
models/qwen3-asr/checkpoints/Qwen3-ForcedAligner-0.6B
```

`run_fa_pipeline.py` invokes `sz-fa/src/qwen3-fa/process.py` whenever the
Qwen route input is non-empty. The output is written to `qwen_fa/`, including
word timestamps and `error_stats.json`. The anomaly filter uses these results
for Qwen alignment failures and zero-duration word statistics.

## Messy-code handling

`process_speeches.py` detects replacement characters, NULs, and common
UTF-8-as-Latin mojibake signatures. It writes:

```text
messy_code_rerun.json
messy_code_corpus.json
```

The rerun manifest contains chunk-level rerun candidates and speech-level
fallback candidates. `anomaly_filter.py` marks a speech with
`asr_messy_code` when the issue remains in the final route output. This keeps
the failed speech auditable instead of silently dropping it.

## Statistics and artifacts

`speech_fa/summary.json` contains:

- speech and chunk counts;
- MFA and Qwen route counts;
- Qwen zero-duration word rate;
- zero-duration chunk rate;
- zero-duration speech rate;
- background audio count;
- messy-code rerun counts.

`fa_report.json` records all commands, stage reports, route summaries,
reconciliation summaries, anomaly summaries, GPU selection, and environment
information.

## Two-GPU invocation

```bash
python3 sz-fa/run_fa_pipeline.py \
  --dataset oto_141 \
  --output-root /path/to/v6_run \
  --gpus 0,1 \
  --resources config/resources.v3.2gpu.yaml \
  --mapping /hbt_share02/202610/ob/ob_ppl_env_data/dataset/mapping_final/Mapping_with_rover/oto_141_with_rover_manifest.json
```

The two-GPU resource file uses a node-local GPU lock root to avoid shared
filesystem root-squash failures:

```text
config/resources.v3.2gpu.yaml
```
