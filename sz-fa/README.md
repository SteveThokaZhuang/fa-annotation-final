# Speech-routed FA pipeline

`run_fa_pipeline.py` 先运行上游 V3 的 ingest、DNSMOS、VAD、enhance、ASR1、ASR2、disagreement、ASR3、ROVER，再使用 `Mapping_with_rover/*_with_rover_manifest.json` 将两个 speaker 的 ROVER chunks 合并到一个 speech archive。每个 chunk 保留语言证据，speech 统一路由：单语言 speech 进入纯 MFA，混合语言 speech 进入 Qwen FA。

纯 MFA 的核心代码来自 `src/run_recordings_mfa.py` 和 `src/mfa/process.py`，运行在 CPU。只有混合语言 speech 才调用 `src/qwen3-fa/process.py`；checkpoint 可用默认集群路径，也可显式覆盖。

```bash
python3 run_fa_pipeline.py --output-root runs/oto141_speech_routed_fa \
  --mapping /hbt_share02/202610/ob/ob_ppl_env_data/dataset/mapping_final/Mapping_with_rover/oto_141_with_rover_manifest.json

# 需要执行混合语言 speech 的 Qwen FA 时追加：
python3 run_fa_pipeline.py ... \
  --qwen-checkpoint /path/to/Qwen3-ForcedAligner \
  --qwen-device cuda:0
```

输出包括：

- `speech_fa/speech_archive.json`：一个 speech 一个 JSON 记录，包含两个 speaker 的 chunk 列表；
- `speech_fa/route_inputs/mfa_recordings/`：纯语言 speech 的 MFA 输入；
- `speech_fa/route_inputs/qwen_fa_input.jsonl`：混合语言 speech 的 Qwen FA 输入及精确 chunk WAV；
- `pure_mfa/`、`qwen_fa/`：两条 FA 结果；
- `speech_fa_reconciled/background_audio.json`：有 ASR 文本但 MFA/Qwen FA 对齐失败的 chunk；这些 chunk 同时从保留 speech archive 删除；
- `speech_fa_reconciled/failure_speeches.json`：没有 ASR 文本且 FA 失败的完整 speech；不进入保留 FA JSON；
- `fa_data/`：通过异常筛选的 clean speech JSON；
- `fa_anomalies/`：异常 speech 的完整 JSON，不进入 `fa_data/`，并附 `anomaly_summary.json`；当前筛选包括 FA 对齐错误、`fa_failure_speech`、`only_one_speaker_dialogue`、乱码和 0 时长字；混合语言路由本身不算异常；
- `messy_code_rerun.json`：乱码 chunk 先重跑、仍乱码时整 speech 重跑的可执行计划；
- `fa_report.json`：speech 路由、MFA background reconciliation 和 Qwen 0 时长字/块/speech 统计。
