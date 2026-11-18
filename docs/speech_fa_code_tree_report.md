# Speech-level ASR / FA code tree report

本文档对应当前目录：

`/hbt_share02/202610/ob/ob_ppl_env_data/audio_pipeline_optimized/Pipeline_github_v3_Merge/pipeline_runnable_subset_20261111`

## 1. 设计规则

ROVER 仍然按 chunk 产出，但 `sz-fa/src/process_speeches.py` 会根据
`Mapping_with_rover/*_with_rover_manifest.json` 把 `speaker_1` 和 `speaker_2`
合并成一个 speech 记录。每个 chunk 保存自己的 `languages`，speech 保存两个
speaker 的 chunk ID、语言集合和唯一的 `route`。

路由只在 speech 级决定：

```text
speech 的所有 chunk 语言集合只有 1 种  -> mfa
speech 的所有 chunk 语言集合超过 1 种 -> qwen-fa
```

Qwen 不是默认启动。只有 `route == qwen-fa` 的 speech 才会写入
`route_inputs/qwen_fa_input.jsonl`；只有该 JSONL 非空时才启动 Qwen-FA。

语言标签来源依次包括 chunk 的 `language`、`input_language`、
`language_verified`、`lang` 和 `hypotheses[*].language/lang`，再结合文本的
Unicode 脚本检测。当前覆盖中、英、日、韩、越、泰、阿拉伯、俄语、印地语。
日文假名出现时，汉字不会被误标成中文，除非上游明确给出中文证据。

语言标签完成后，FA 路由输入生成前，会对包含 `en` 的 chunk 应用仓库根目录的
`mapping_flexible_v2.json`。该文件复制自
`Final_post_processing_v3/mapping_list/mapping_flexible_v2.json`，用于修正英文
backchannel 被 ASR 识别成中文字符的情况。`english_backchannel_mapping.py` 按
最长精确短语、重复字符、单字符规则执行映射，例如 `嗯哼` → `uh-huh`、`嗯嗯` →
`mm-mm`。单字符规则遇到相邻 CJK 字符时跳过，保留普通中文词；映射事件和修改
前文本写入 chunk 的 `english_backchannel_mapping` 审计字段，summary 写入映射
文件和修改计数。可用 `--english-backchannel-map` 覆盖词表路径。
summary 同时记录词表版本和 SHA256。

## 2. 入口和调用树

```text
sz-fa/run_fa_pipeline.py
├─ parent/run.py
│  └─ ingest → dnsmos → vad → enhance → asr1 → asr2
│     → disagreement → asr3 → rover
│        └─ production/<dataset>/rover/recordings/*.json
├─ sz-fa/src/process_speeches.py
│  ├─ 读取 ROVER recordings + Mapping_with_rover
│  ├─ 按 speech 合并两个 speaker 的 chunk
│  ├─ 标注 chunk.languages / speech.languages
│  ├─ 对英文标签 chunk 应用 mapping_flexible_v2.json backchannel 映射
│  ├─ 写 MFA 和 Qwen 路由输入
│  ├─ 写乱码 corpus 和两级重跑 manifest
│  └─ 写 speech_archive.json / summary.json
├─ （有乱码时）sz-fa/src/messy_asr_rerun.py
│  ├─ 先重跑 scope=chunk 的条目
│  └─ 再重跑 scope=speech 的全部条目
├─ process_speeches.py --rerun-results
│  ├─ 用重跑文本重新标语言和路由
│  ├─ 持续乱码 speech → failed by asr messy code
│  └─ 失败 speech 不进入 MFA/Qwen FA
├─ sz-fa/src/run_recordings_mfa.py
│  ├─ 按 chunk 裁 WAV
│  ├─ 分批调用 MFA
│  └─ 写 pure_mfa/batch_results/*.jsonl.gz
├─ sz-fa/src/qwen3-fa/process.py
│  ├─ 只处理混合语言 speech
│  └─ 写 qwen_fa/alignment_predictions.jsonl
├─ sz-fa/src/reconcile_mfa.py
│  ├─ MFA/Qwen words 回写 speech chunks
│  ├─ Qwen 局部时间加回 recording chunk 起点
│  ├─ 有 ASR 文本但 FA 无 alignment → background_audio.json
│  ├─ 无 ASR 文本且 FA 失败 → failure_speeches.json
│  └─ 从正式 speech_archive 删除 background chunk / failed speech
└─ sz-fa/src/anomaly_filter.py
   ├─ clean speech → fa_data/<speech_id>.json
   └─ 异常 speech → fa_anomalies/<speech_id>.json
```

## 3. 关键文件

| 文件 | 作用 |
|---|---|
| `sz-fa/run_fa_pipeline.py` | 全流程编排、命令记录、环境/设备报告 |
| `sz-fa/src/process_speeches.py` | speech 聚合、语言标注、路由、乱码输入准备、统计 |
| `sz-fa/src/english_backchannel_mapping.py` | 英文标签 chunk 的中文 backchannel 映射和审计事件 |
| `sz-fa/src/messy_asr_rerun.py` | Qwen3-ASR 乱码重跑实现 |
| `sz-fa/src/run_recordings_mfa.py` | 物理 chunk WAV 裁剪和 MFA 批处理 |
| `sz-fa/src/qwen3-fa/process.py` | Qwen Forced Aligner 和 alignment audit |
| `sz-fa/src/reconcile_mfa.py` | 对齐结果合并、MFA background 处理、Qwen 时间坐标修正 |
| `sz-fa/src/anomaly_filter.py` | 正式数据与异常数据分流 |
| `sz-fa/SPEECH_FA_PIPELINE.md` | 运行说明、输入输出契约和统计字段 |
| `config/resources.v3.2gpu.yaml` | 两卡资源配置；GPU 锁位于本地 `/tmp` |
| `pipeline/vendor_py/yaml` | 集群环境缺失 PyYAML 时使用的本地模块 |

## 4. 乱码重跑状态机

命中条件是 replacement character `�`、NUL 字符或常见 UTF-8 mojibake
签名（例如 `Ã`、`Â`、`Ð` 后接 continuation byte）。

```text
初始 ROVER text
  ├─ 无乱码 → 正常语言标注和 MFA/Qwen 路由
  └─ 有乱码
       ↓
  只重跑命中的 chunk
       ├─ chunk 文本恢复 → 用新文本重新标语言，继续路由
       └─ 仍乱码 / 同 speech 仍有乱码
             ↓
       重跑该 speech 的全部 chunk
             ├─ 全部恢复 → 重新标语言和路由
             └─ 任意 chunk 仍乱码
                   ↓
             speech.status = "failed by asr messy code"
             不进入 MFA/Qwen FA
```

重跑输入和结果分别是：

```text
speech_fa/rerun_inputs.jsonl
speech_fa/messy_asr_predictions.jsonl
speech_fa/messy_code_rerun.json
```

## 5. MFA、Qwen 和统计输出

FA 结果有 ASR 文本但出现命令错误、空词或缺失 alignment 时，chunk 会被
写入 `speech_fa_reconciled/background_audio.json`，并从
`speech_fa_reconciled/speech_archive.json` 删除；没有 ASR 文本且 FA 失败的
speech 会写入 `speech_fa_reconciled/failure_speeches.json`（保留该 speech 的完整
chunk 列表，并附 `mfa_failed_chunks` / `retained_chunks` 审计字段），同样从保留归档删除。
Qwen 结果的局部时间戳会加上原始 chunk 的 `start`，因此最终 speech 归档使用
recording 坐标。

统计包含：

```text
qwen_fa_zero_duration_word_rate
qwen_fa_zero_duration_words
qwen_fa_total_words
zero_duration_chunk_rate
zero_duration_speech_rate
```

Qwen 的零时长统计以实际 Qwen 输出为准；MFA/Qwen words 会先回写，再由
`anomaly_filter.py` 检查零时长、空词、越界和对齐错误。多语 speech 本身是预期
路由，不会仅因 `route == qwen-fa` 被标成异常。

## 6. 最终输出树

```text
<output-root>/
├─ upstream_output/production/<dataset>/...          # V3 九阶段结果
├─ speech_fa/
│  ├─ speech_archive.json                             # speech 级正式候选
│  ├─ rerun_inputs.jsonl                              # 乱码重跑音频清单
│  ├─ messy_asr_predictions.jsonl                    # ASR 重跑结果
│  ├─ messy_code_rerun.json                           # chunk/speech 两级计划
│  ├─ messy_code_corpus.json                          # 世界主要语言乱码样本
│  └─ route_inputs/
│     ├─ mfa_recordings/*.json
│     ├─ qwen_fa_input.jsonl
│     ├─ qwen_audio_chunks/*.wav
│     └─ messy_rerun/{chunk,speech}/.../*.wav
├─ pure_mfa/
│  ├─ batch_results/*.jsonl.gz
│  └─ summary.json
├─ qwen_fa/
│  ├─ alignment_predictions.jsonl
│  ├─ predictions.jsonl
│  └─ error_stats.json
├─ speech_fa_reconciled/
│  ├─ speech_archive.json
│  ├─ background_audio.json
│  ├─ failure_speeches.json
│  └─ mfa_reconciliation_summary.json
├─ fa_data/<speech_id>.json                       # 无异常 speech
├─ fa_anomalies/<speech_id>.json                   # 异常 speech
├─ fa_report.json
└─ call_stack.json
```

## 7. 验证和运行环境

已完成 Python 语法检查，并用合成 WAV 验证了：中英混合 speech 路由到 Qwen、
MFA background 删除、Qwen 局部时间回写、乱码重跑结果应用和多语 speech 正常
进入 clean 输出。

实际 oto141 运行命令使用 `CUDA_VISIBLE_DEVICES=0,1`，上游资源文件为
`config/resources.v3.2gpu.yaml`，Python 使用项目 `pipeline/vendor_py` 中的
PyYAML 兼容路径；MFA 使用仓库内 `models/mfa/bin/python`，Qwen 使用集群的
Qwen ASR / Forced Aligner checkpoint。运行报告中的 `fa_report.json` 和
`call_stack.json` 会记录 Python 版本、GPU、checkpoint、命令和各阶段耗时。

本次 oto141 的旧运行在上游 ASR2 阶段因远端进程 `nofile=1024` 触发
`Too many open files` 而中断，未生成完整 ROVER，因此没有把它作为 FA 结果
写入报告。按用户要求，后续不再重跑 oto141；代码验证使用合成 WAV 覆盖 speech
聚合、单/多语路由、乱码重跑结果应用、FA 对齐回写、background 和
failure_speeches 分流。
