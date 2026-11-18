# Speech FA 标注 Pipeline

本仓库提供一条从录音、ASR 到 speech 级强制对齐（Forced Alignment，FA）的可恢复流水线。生产入口是：

```text
sz-fa/run_fa_pipeline.py
```

它把上游 V3 的录音级结果恢复为一个 speech 一个 JSON 记录，再根据整个 speech 的语言集合选择 MFA 或 Qwen Forced Aligner，最后回收失败块、生成可交付数据，并把异常 speech 单独保存供复核。

## 处理范围

一个完整运行包含两部分：

```text
原始 WAV / manifest
  -> ingest
  -> DNSMOS
  -> VAD
  -> enhancement（按配置可关闭）
  -> ASR1
  -> ASR2
  -> disagreement
  -> ASR3
  -> ROVER
  -> speech 级聚合与语言判断
  -> MFA 或 Qwen FA
  -> FA 失败块回收
  -> 异常筛选
  -> fa_data/ 与 fa_anomalies/
```

上游阶段仍按录音 chunk 工作。FA 阶段通过 `Mapping_with_rover/*_with_rover_manifest.json` 将两个 speaker 的 ROVER 结果合并为一个 speech，因此最终的路由单位是 speech，而不是单个 chunk。

每个 speech 会保留：

- `speech_id`、speaker 列表和每个 chunk 的原始 ID；
- chunk 的录音路径、通道、开始/结束时间和文本；
- ROVER/ASR 提供的语言证据；
- Unicode 文本检测出的语言集合；
- FA 后的 word 时间戳、后端、错误和审计字段。

## 快速运行

从仓库根目录执行。最小运行需要准备好配置引用的 Python 环境、模型、MFA 资源和数据 mapping。

```bash
python3 sz-fa/run_fa_pipeline.py \
  --dataset oto_141 \
  --output-root /absolute/path/oto141_speech_routed_fa \
  --mapping /path/to/Mapping_with_rover/oto_141_with_rover_manifest.json
```

指定 GPU 和 MFA 并行度：

```bash
python3 sz-fa/run_fa_pipeline.py \
  --dataset oto_141 \
  --output-root /absolute/path/oto141_speech_routed_fa \
  --mapping /path/to/oto_141_with_rover_manifest.json \
  --gpus 0,1 \
  --mfa-jobs 32 \
  --mfa-timeout 14400 \
  --resources config/resources.v3.2gpu.yaml
```

混合语言 speech 才需要 Qwen FA。默认 checkpoint 是：

```text
models/qwen3-asr/checkpoints/Qwen3-ForcedAligner-0.6B
```

可显式覆盖 Qwen 环境、checkpoint、设备和 batch size：

```bash
python3 sz-fa/run_fa_pipeline.py \
  --output-root /absolute/path/oto141_speech_routed_fa \
  --mapping /path/to/oto_141_with_rover_manifest.json \
  --qwen-checkpoint /path/to/Qwen3-ForcedAligner-0.6B \
  --qwen-python /path/to/qwen3-asr/bin/python \
  --qwen-device cuda:0 \
  --qwen-batch-size 4
```

如果上游 V3 已经完成，只执行 speech 聚合和 FA：

```bash
python3 sz-fa/run_fa_pipeline.py \
  --skip-upstream \
  --output-root /absolute/path/oto141_speech_routed_fa \
  --mapping /path/to/oto_141_with_rover_manifest.json
```

`--skip-upstream` 要求以下目录已经存在：

```text
<output-root>/upstream_output/production/<dataset>/rover/recordings/
```

## 参数和默认值

`run_fa_pipeline.py` 的主要参数如下：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--dataset` | `oto_141` | 上游数据集名称，也用于定位 ROVER 输出 |
| `--output-root` | `sz-fa/runs/oto141_speech_routed_fa` | 本次运行的根目录 |
| `--mapping` | 集群上的 `oto_141_with_rover_manifest.json` | speech 与两个 speaker ROVER 文件的映射 |
| `--gpus` | `0,1,2,3` | 上游资源协调和 Qwen 可见 GPU 列表 |
| `--resources` | `config/resources.v3.oto104_magiczh.yaml` | 上游 V3 调度、模型和缓存配置 |
| `--mfa-python` | `models/mfa/bin/python` | MFA 运行时 Python |
| `--mfa-jobs` | `32` | 每个 MFA batch 的并行 job 数 |
| `--mfa-timeout` | `14400` 秒 | 单个 MFA batch 超时 |
| `--qwen-checkpoint` | `models/qwen3-asr/checkpoints/Qwen3-ForcedAligner-0.6B` | Qwen FA checkpoint |
| `--qwen-python` | `$QWEN_PYTHON`，否则集群 qwen 环境 | Qwen FA Python |
| `--qwen-device` | `cuda:0` | Qwen FA 设备 |
| `--qwen-batch-size` | `4` | Qwen FA 推理 batch size |
| `--asr-rerun-checkpoint` | Qwen3 ASR 1.7B checkpoint | 乱码文本重跑所用 ASR checkpoint |
| `--skip-messy-rerun` | 关闭 | 跳过乱码文本重跑 |

也可以用环境变量覆盖解释器：

```bash
PIPELINE_PYTHON=/path/to/controller-python \
MFA_PYTHON=/path/to/mfa/bin/python \
QWEN_PYTHON=/path/to/qwen/bin/python \
python3 sz-fa/run_fa_pipeline.py ...
```

当前脚本的上游命令固定使用：

```text
config/pipeline.v3.oto104_magiczh.yaml
config/datasets.v3.oto104_magiczh.local.yaml
```

如需运行其他数据集，先检查并修改这两个配置，或使用 `--skip-upstream` 直接消费已经生成的 ROVER recording JSON。

## 输入数据

### ROVER mapping

mapping 是 JSON 数组。正常的双 speaker speech 应提供一个 speech ID 和两个 speaker 的 ROVER 引用；缺少一侧引用时仍可被读取，但会在异常筛选阶段标记为单 speaker 对话。例如：

```json
[
  {
    "conversation_id": "conversation-001",
    "rover_speaker_1": "speaker_1_recording.json",
    "rover_speaker_2": "speaker_2_recording.json"
  }
]
```

实现也接受 `session_id`；没有 ID 时使用数组下标。两个引用对应的文件应能在 ROVER recording 目录中找到。每个 recording JSON 的 chunk 需要包含：

- `id`（没有时由文件名和序号生成）；
- `start`、`end` 或 `chunk_start`、`duration`；
- `audio_path` 或 `path`；
- `text`；
- 可选 `channel`、`language`、`hypotheses`、`words` 和原始来源字段。

音频 chunk 会按原始采样率和通道读取。缺失音频、非法通道、短读、非有限采样值会使对应输入无法物化，并记录在运行日志或重跑字段中。

### 语言识别

每个 chunk 的语言来自两类证据：

1. ROVER/ASR 行中的 `language`、`input_language`、`language_verified`、`lang` 和 hypothesis 字段；
2. 文本 Unicode 检测。

当前支持的语言代码包括 `en`、`zh`、`ja`、`ko`、`th`、`vi`、`ar`、`ru`、`hi`。拉丁字母在没有越南语证据时会计为英文；日文假名会优先于共享汉字推断日文；没有证据的文本标记为 `unknown`。

### 英文 backchannel 词表修正

语言标记完成后、生成 MFA/Qwen FA 输入前，`process_speeches.py` 会对含有
`en` 标签的 chunk 应用仓库根目录的 `mapping_flexible_v2.json`。该文件从
`Final_post_processing_v3/mapping_list/mapping_flexible_v2.json` 复制而来，作为
当前 pipeline 的固定版本输入，用于处理 MFA 可能把英文 backchannel 识别成中文
字符的情况，例如 `嗯哼` → `uh-huh`、`哼` → `hmm`、`哈` → `ha`。

替换顺序是最长精确短语、重复 backchannel、单字符 backchannel。单字符规则只在
字符独立出现或处于英文/标点混合文本中时生效；嵌在普通中文词里的字符会保留，
以避免把任意中文内容翻译成英文。替换前后的文本和每条替换事件会写入 chunk，
`speech_fa/summary.json` 会记录修改 chunk 数、事件数和替换次数。
同时记录词表版本和 SHA256，便于确认 FA 输入使用的词表版本。

默认词表路径是 `mapping_flexible_v2.json`，可用
`--english-backchannel-map /path/to/mapping.json` 覆盖。该选项同时存在于
`run_fa_pipeline.py` 和 `process_speeches.py`；只有经过语言标记且含英文的 chunk
才会调用词表。

语言集合在 speech 级合并：

```python
route = "qwen-fa" if len(speech_languages) > 1 else "mfa"
```

因此，同一个混合语言 speech 的所有 chunk 都进入 Qwen FA；单语言 speech 的所有 chunk 都进入 MFA。路由只在 speech 级决定，不会因为某一个 chunk 的语言不同而拆成两条后端。

## 各阶段说明

### 1. 上游 V3 和 ROVER

`run_fa_pipeline.py` 在未指定 `--skip-upstream` 时调用仓库根目录的 V3 controller，依次运行：

```text
ingest -> dnsmos -> vad -> enhance -> asr1 -> asr2 -> disagreement -> asr3 -> rover
```

上游输出位于：

```text
<output-root>/upstream_output/production/<dataset>/
```

ROVER recording JSON 位于：

```text
<output-root>/upstream_output/production/<dataset>/rover/recordings/
```

上游日志是 `<output-root>/upstream_pipeline.log`。V3 的 checkpoint、环境、缓存和 GPU 数量由 `--resources` 以及其继承的 pipeline 配置控制。首次运行建议先使用上游 V3 的 dry run 检查路径和资源。

### 2. speech 聚合和路由输入

`sz-fa/src/process_speeches.py` 完成以下工作：

- 从 mapping 读取 `rover_speaker_1` 和 `rover_speaker_2`；
- 将两个 speaker 的 recording chunks 合并为一个 speech；
- 为 chunk 保存 speaker、语言证据、语言集合和原始 chunk 信息；
- 在英文标签确认后修正英文 backchannel 映射；
- 识别乱码文本；
- 为 MFA 或 Qwen 物化所需的 WAV；
- 生成 MFA JSON 输入和 Qwen JSONL 输入；
- 生成 speech 级 summary 和乱码重跑计划。

### 3. 纯语言 speech：MFA

MFA 由 `sz-fa/src/run_recordings_mfa.py` 调用，底层实现是 `sz-fa/src/mfa/process.py`，运行在 CPU。wrapper 会：

- 从完整录音中提取 chunk 音频；
- 必要时重采样到 16 kHz；
- 生成每个 chunk 的 `.wav` 和 `.lab`；
- 按最多 5000 个 chunk 分 batch 调用 Montreal Forced Aligner；
- 保存 gzip JSONL 预测、MFA 日志和可恢复状态；
- 把词时间戳加回原始 recording 时间坐标；
- 对轻微边界浮点误差进行 epsilon clamp，对真实越界进行有界压缩；
- 拒绝零时长、负时长、非单调或无法修复的词时间戳。

默认 MFA 资源是英文：

```text
models/mfa-english/dictionary/english_us_arpa.dict
models/mfa-english/english_us_arpa.zip
```

中文、日文、韩文、泰文和越南文需要对应的 dictionary、acoustic model、tokenizer 和语言 adapter。`run_fa_pipeline.py` 的纯 MFA 默认参数不会自动按 speech 语言替换资源；非英文数据应在运行前确认资源配置，或单独调用 `run_recordings_mfa.py` 指定 `--dictionary`、`--acoustic-model` 和必要的 `--mfa-language`。

### 4. 混合语言 speech：Qwen FA

Qwen 路由由 `sz-fa/src/qwen3-fa/process.py` 执行。speech 聚合阶段为每个 chunk 生成精确裁剪的 WAV，并在 `qwen_fa_input.jsonl` 中保留：

- chunk ID 和 speech ID；
- speaker；
- 裁剪 WAV 路径；
- ASR 文本；
- `chunk_start`、`chunk_end`；
- 原始 recording 坐标；
- 单语言提示或 `auto`。

Qwen 输出保留 chunk 本地坐标；`reconcile_mfa.py` 会按原始 chunk 起点转换回 recording 坐标。Qwen 会审计：非有限时间、零时长、负时长、越界、非单调和重叠词，并将错误写入 `error_stats.json`、`unresolved.json` 和预测 JSONL。

### 5. 乱码文本重跑

`process_speeches.py` 将以下情况识别为乱码：

- Unicode replacement character `�`；
- NUL；
- 常见 UTF-8 误解码片段，例如 `Ã`、`Â`、`Ð` 后接 continuation byte。

第一次 speech 聚合时，乱码 chunk 会生成两类重跑输入：

1. 只重跑乱码 chunk；
2. 重跑包含乱码 chunk 的整个 speech。

`run_fa_pipeline.py` 默认先执行 chunk 级重跑，再执行 speech 级重跑；speech 级结果优先。第二次调用 `process_speeches.py --rerun-results ...` 后，如果仍有乱码，speech 会标记为 `failed by asr messy code`，后续进入异常数据。

跳过这一步：

```bash
python3 sz-fa/run_fa_pipeline.py ... --skip-messy-rerun
```

只有第一次发现乱码、且不使用 `--skip-messy-rerun` 时才会启动 Qwen ASR 重跑。重跑日志和预测位于：

```text
<output-root>/messy_asr_rerun.log
<output-root>/messy_asr_predictions.jsonl
```

### 6. FA 结果回收

`sz-fa/src/reconcile_mfa.py` 同时处理 MFA 和 Qwen 结果：

- 有 ASR 文本但没有 FA 词、或 FA 报错：该 chunk 移入 `background_audio.json`；
- 没有 ASR 文本且 FA 失败：完整 speech 移入 `failure_speeches.json`；
- 成功 chunk 的词时间戳写回 speech archive；
- 被回收的 chunk 从权威保留 archive 中删除。

注意：reconcile 会原地重写 `speech_fa/speech_archive.json`，使其只包含保留 speech 和保留 chunk；原始中间结果仍可在运行目录的日志和其他 JSON 中追溯。

### 7. 异常筛选

`sz-fa/src/anomaly_filter.py` 将 reconcile 后的 speech 分到两个目录：

```text
fa_data/       # 没有异常标记的 speech JSON
fa_anomalies/  # 带异常原因的 speech JSON
```

当前异常原因包括：

- `fa_failure_speech`：没有 ASR 文本且 FA 失败的完整 speech；
- `missing_fa_alignment`：有文本但没有可用 FA 对齐；
- `alignment_error`：MFA/Qwen 对齐错误；
- `qwen_fa_alignment_error`：Qwen 输出错误或有文本却没有 words；
- `zero_duration_word`：词时间戳为零时长；
- `asr_messy_code`：乱码仍存在或被标记为乱码；
- `only_one_speaker_dialogue`：最终 speech 只有一个 speaker；
- `multi_language`：chunk 检测到多个语言；
- `multi_language` 是预期的 Qwen 路由条件，但当前筛选器仍会把它作为 chunk 级审计标记写入异常原因，应按项目验收规则决定是否剔除。

异常数据不会静默丢弃。每个异常 speech 都保留完整 JSON、`anomaly_reasons` 和 `anomaly_chunks`，并在 `anomaly_summary.json` 中汇总。

## 输出目录

假设本次运行目录是 `<output-root>`，主要文件如下：

```text
<output-root>/
├── upstream_pipeline.log
├── speech_fa.log
├── speech_fa_after_rerun.log
├── messy_asr_rerun.log
├── pure_mfa.log
├── qwen_fa.log
├── mfa_reconcile.log
├── anomaly_filter.log
├── upstream_output/
│   └── production/<dataset>/...                 # V3 与 ROVER 输出
├── speech_fa/
│   ├── speech_archive.json                       # speech 级 archive
│   ├── background_audio.json                     # 初始中间文件
│   ├── messy_code_rerun.json                    # 重跑计划
│   ├── messy_code_corpus.json                   # 乱码测试样例
│   ├── rerun_inputs.jsonl                       # 可执行 ASR 重跑输入
│   ├── messy_asr_predictions.jsonl              # ASR 重跑结果
│   ├── summary.json
│   └── route_inputs/
│       ├── mfa_recordings/*.json
│       ├── qwen_fa_input.jsonl
│       ├── qwen_audio_chunks/*.wav
│       └── messy_rerun/{chunk,speech}/...
├── pure_mfa/
│   ├── manifest.jsonl
│   ├── state.json
│   ├── logs/*.log
│   ├── batch_results/*.jsonl.gz
│   ├── summary.json
│   ├── error_stats.json
│   └── unresolved.json
├── qwen_fa/
│   ├── qwen_predictions.jsonl
│   ├── alignment_predictions.jsonl
│   ├── error_stats.json
│   ├── unresolved.json
│   ├── metadata.json
│   └── runtime.json
├── speech_fa_reconciled/
│   ├── speech_archive.json                     # FA 后权威保留 archive
│   ├── background_audio.json                   # 有文本但对不齐的 chunk
│   ├── failure_speeches.json                   # 无文本且 FA 失败的 speech
│   └── mfa_reconciliation_summary.json
├── fa_data/
│   ├── clean_summary.json
│   └── <speech-id>.json
├── fa_anomalies/
│   ├── anomaly_summary.json
│   └── <speech-id>.json
├── fa_report.json
└── call_stack.json
```

### 关键 JSON 的语义

- `speech_fa/speech_archive.json`：路由前或重跑后的 speech 级输入 archive；reconcile 完成后会被更新。
- `speech_fa_reconciled/speech_archive.json`：经过 FA 失败回收后的权威 archive。
- `fa_data/*.json`：可直接进入后续标注/分析流程的 clean speech。
- `fa_anomalies/*.json`：需要人工检查或单独规则处理的完整 speech。
- `fa_report.json`：命令、阶段、计时、路由计数、MFA/Qwen 统计、异常汇总、解释器和代码 hash。
- `call_stack.json`：本次运行的命令链和阶段顺序，便于复现。

## 单独运行和调试

### 只构建 speech archive

```bash
python3 sz-fa/src/process_speeches.py \
  --recordings /path/to/rover/recordings \
  --mapping /path/to/with_rover_manifest.json \
  --output /path/to/speech_fa
```

使用 ASR 重跑结果重建 archive：

```bash
python3 sz-fa/src/process_speeches.py \
  --recordings /path/to/rover/recordings \
  --mapping /path/to/with_rover_manifest.json \
  --output /path/to/speech_fa \
  --rerun-results /path/to/messy_asr_predictions.jsonl
```

### 单独运行 MFA

```bash
python3 sz-fa/src/run_recordings_mfa.py \
  --recordings /path/to/speech_fa/route_inputs/mfa_recordings \
  --output /path/to/pure_mfa \
  --mfa-python /path/to/models/mfa/bin/python \
  --dictionary /path/to/english_us_arpa.dict \
  --acoustic-model /path/to/english_us_arpa.zip \
  --mfa-jobs 32
```

### 单独运行 Qwen FA

```bash
python3 sz-fa/src/qwen3-fa/process.py \
  --input /path/to/speech_fa/route_inputs/qwen_fa_input.jsonl \
  --output /path/to/qwen_fa \
  --checkpoint /path/to/Qwen3-ForcedAligner-0.6B \
  --device cuda:0 \
  --batch-size 4 \
  --export-unit both
```

### 重新回收和筛选

```bash
python3 sz-fa/src/reconcile_mfa.py \
  --speech-archive /path/to/speech_fa/speech_archive.json \
  --mfa-output /path/to/pure_mfa \
  --qwen-output /path/to/qwen_fa/alignment_predictions.jsonl \
  --output /path/to/speech_fa_reconciled

python3 sz-fa/src/anomaly_filter.py \
  --speech-archive /path/to/speech_fa_reconciled/speech_archive.json \
  --background-audio /path/to/speech_fa_reconciled/background_audio.json \
  --failure-speeches /path/to/speech_fa_reconciled/failure_speeches.json \
  --qwen-predictions /path/to/qwen_fa/alignment_predictions.jsonl \
  --output-data /path/to/fa_data \
  --output-anomalies /path/to/fa_anomalies
```

## 可恢复性和重跑规则

- 上游 V3 使用自己的 manifest、checkpoint 和 resume 机制；相同输出目录可以继续运行。
- MFA wrapper 通过 `state.json` 记录已经完成的 5000 chunk batch；重新启动会从下一个 batch 继续。
- Qwen FA 输出目录要求为空或不存在；若要重跑，应使用新的输出目录，或先备份并清理旧目录。
- `process_speeches.py` 每次运行会删除并重建 `speech_fa/route_inputs/`，避免旧路由输入混入新结果。
- `reconcile_mfa.py` 会更新 speech archive；重跑前应确认使用的是正确的中间 archive。
- 只有一个 controller 应同时写入同一个 `<output-root>`；MFA 和 Qwen 的结果目录也不要被另一条任务并行覆盖。
- `fa_report.json`、各阶段日志和 `call_stack.json` 应与最终 `fa_data/` 一起保存。

## 运行前检查

```bash
# 检查入口和帮助
python3 sz-fa/run_fa_pipeline.py --help
python3 sz-fa/src/process_speeches.py --help
python3 sz-fa/src/run_recordings_mfa.py --help
python3 sz-fa/src/qwen3-fa/process.py --help

# 检查 mapping、ROVER 目录和关键模型
python3 - <<'PY'
from pathlib import Path
checks = [
    Path('sz-fa/run_fa_pipeline.py'),
    Path('sz-fa/src/process_speeches.py'),
    Path('sz-fa/src/run_recordings_mfa.py'),
    Path('sz-fa/src/reconcile_mfa.py'),
    Path('sz-fa/src/anomaly_filter.py'),
    Path('models/mfa-english/dictionary/english_us_arpa.dict'),
    Path('models/mfa-english/english_us_arpa.zip'),
    Path('models/qwen3-asr/checkpoints/Qwen3-ForcedAligner-0.6B'),
]
for path in checks:
    print(('OK   ' if path.exists() else 'MISS '), path)
PY
```

先做小样本验证时，可以把 mapping 限制到少量 speech，或者先完成上游并使用 `--skip-upstream` 反复调试 FA 后处理。验收至少应检查：

- `fa_report.json` 的 `status` 是否为 `complete`；
- `speech_fa/summary.json` 的 MFA/Qwen 路由计数；
- `pure_mfa/summary.json` 的 `failed_chunks`；
- `qwen_fa/error_stats.json` 的 `bad_chunks`、零时长词和错误率；
- `speech_fa_reconciled/mfa_reconciliation_summary.json` 的 background 和 failure 数量；
- `fa_anomalies/anomaly_summary.json` 的异常原因；
- `fa_data/` 中每个 speech 是否保留两个 speaker、有效 word 时间戳和原始来源字段。

## 代码结构

```text
sz-fa/
├── run_fa_pipeline.py          # 生产总控入口
├── src/
│   ├── process_speeches.py     # speech 聚合、语言判断、路由输入
│   ├── english_backchannel_mapping.py # 英文 chunk 的中文 backchannel 映射
│   ├── messy_asr_rerun.py      # 乱码 chunk/speech 的 ASR 重跑
│   ├── run_recordings_mfa.py   # MFA wrapper、音频裁剪和恢复状态
│   ├── mfa/process.py          # MFA 结果解析和边界修复
│   ├── qwen3-fa/               # Qwen FA 模型调用、审计和输出
│   ├── reconcile_mfa.py        # FA 结果回写和失败回收
│   ├── anomaly_filter.py       # clean/anomaly speech 分流
│   └── complex_handling/       # Qwen FA 的复杂语言处理组件
└── models/                     # speech-FA 相关模型引用或本地资产
```

上游通用音频流水线位于 `pipeline/`、`models/*/final_script/` 和 `config/`。运行环境、模型权重、音频数据、输出目录和大型缓存不应提交到 Git。第三方组件和许可信息见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

仓库根目录的 `mapping_flexible_v2.json` 是英文 backchannel 映射词表；它和
`process_speeches.py` 一起版本化，确保同一批 FA 结果可以复现。

## 失败处理原则

FA 失败不会把有文本的 chunk 直接算作 speech 成功：它会进入 `background_audio.json` 并从保留 archive 移除。没有文本且 FA 失败时，整条 speech 进入 `failure_speeches.json`。乱码重跑仍失败、单 speaker、零时长词、FA 对齐错误和缺失 speaker 都会在 `fa_anomalies/` 保留完整上下文，便于人工复核或再次处理。
