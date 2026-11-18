# 从 input 到 Force Alignment 的当前代码树逻辑

> 范围：只依据当前目录中的代码、配置和模型适配器整理。  
> 主流程：`ingest → dnsmos / vad / enhance → asr1 / asr2 → disagreement → asr3 → rover → align`。  
> 其中 `dnsmos` 是质量评分支线，默认不决定 VAD；`enhance` 是否执行由配置/命令行决定。

## 1. 总体调用树

```text
命令行 input
├─ run_v3.sh
│  └─ scripts/launch_v3.py
│     └─ run_pipeline.sh
│        └─ run.py
│           └─ pipeline/audio_pipeline/cli.py: main()
│
└─ 直接入口（等价地进入 run_pipeline.sh）
   ├─ stages/ingest.py
   ├─ stages/vad.py
   ├─ stages/enhance.py
   ├─ stages/asr1.py / asr2.py / asr3.py
   ├─ stages/disagreement.py
   ├─ stages/rover.py
   └─ stages/align.py
      └─ run_pipeline.sh ... align
         └─ cli.py
```

`run_pipeline.sh` 设置 `PYTHONPATH`（`pipeline`、`vendor`、部分模型源码），`run.py` 再导入 `audio_pipeline.cli.main`。CLI 负责解析参数、加载 YAML、建立输出根目录、生成 stage plan、调度 worker、恢复 checkpoint 和最终导出。

## 2. 代码目录树（与 input→FA 相关）

```text
.
├── run_v3.sh                         # V3 启动包装
├── run_pipeline.sh                    # 设置 PYTHONPATH，调用 run.py
├── run.py                             # CLI Python 控制器入口
├── scripts/
│   └── launch_v3.py                   # V3 启动/参数封装
├── stages/
│   ├── ingest.py
│   ├── vad.py
│   ├── enhance.py
│   ├── asr1.py / asr2.py / asr3.py
│   ├── disagreement.py
│   ├── rover.py
│   └── align.py                       # 转发到 cli 的 align stage
├── config/
│   ├── pipeline.v3.yaml               # stage、模型、FA/MFA 路由和参数
│   └── resources.v3*.yaml             # worker/GPU/并发/缓存策略
├── pipeline/audio_pipeline/
│   ├── cli.py                         # 主调度、依赖、worker、manifest、导出
│   ├── planning.py                    # descriptor()、各 stage task items
│   ├── datasets.py                    # input 文件/manifest 发现与 fingerprint
│   ├── stage_plan.py                  # 持久化任务计划
│   ├── worker.py                      # 单 worker 加载 Adapter、批推理、保存
│   ├── stage_pipelines.py             # 可选预取/流水线封装
│   ├── finalization.py                # recording 聚合和 recordings/*.json 导出
│   ├── state.py / task_manifest.py    # 状态、manifest、checkpoint
│   ├── resume*.py / working_cache.py  # 恢复、缓存、重用
│   ├── audio.py / prefetch.py         # chunk 音频读取及预取
│   └── failure_policy.py              # 错误分类、失败持久化
├── stages/* 与 models/*/final_script/
│   └── 各 stage 的 Adapter
└── models/qwen3-fa-mfa/
    ├── final_script/adapter.py        # V3 FA+MFA Adapter（当前 align 实际入口）
    └── source/
        ├── qwen3-fa/core.py           # QwenAligner、批量 FA、时间戳校验
        ├── qwen3-fa/process.py       # 输入行展开及 FA 组件逻辑
        ├── qwen3-fa+mfa/process.py   # Qwen + MFA 命令行封装
        └── qwen3-fa+mfa/repair.py    # 句子级 MFA 修复
```

## 3. 主流程逐层逻辑

### 3.1 input：数据集发现

```text
config/pipeline.v3.yaml
└─ dataset_root + datasets[dataset]
   ├─ path：扫描目录中的 wav/flac/ogg
   └─ manifest：按 JSONL 的 path 读取
      ↓
pipeline/audio_pipeline/planning.py
└─ items('ingest', ...)
   └─ pipeline/audio_pipeline/datasets.py: discover()
      └─ _discover_live()
         ├─ include/exclude/extension 过滤
         ├─ 生成 id、relative_path、绝对 path
         └─ fingerprint(path) = size + mtime_ns
      ↓
ingest Adapter
└─ models/audio_ingestion/final_script/adapter.py
   （由 planning.ADAPTERS['ingest'] 选择）
      ↓
output_root/ingest/<model>/<id>/<signature>/result.json
```

input 阶段只做源文件发现和元数据快照，原始文件不会被复制进任务结果。后续阶段通过 `source_fingerprint` 检测源文件是否变化。

### 3.2 VAD：录音 → 可处理 speech chunks

```text
planning.items('vad')
└─ recordings(state)
   └─ ingest 结果
      ↓
models/VAD/FireRedVAD/final_script/adapter.py
└─ FireRedVAD + vendor/fireredvad/*
   └─ 输出 chunks[]
      ├─ recording_id / channel
      ├─ start/end
      ├─ start_sample/end_sample
      ├─ audio/path 信息
      └─ duration
      ↓
vad/.../result.json
```

VAD 的 chunk 是 ASR、后续 ROVER 和 FA 的基本任务单位。VAD 的参数来自 `config/pipeline.v3.yaml: settings.vad`，调度和可选预处理由 `worker.py`、`stage_pipelines.py: VADPipeline` 处理。

### 3.3 DNSMOS 与 Enhancement（可选/支线）

```text
ingest recordings
├─ planning.items('dnsmos')
│  └─ models/dnsmos/final_script/adapter.py
│     └─ 每个 recording 的质量分数
│
└─ planning.items('enhance')
   ├─ models/deepfilternet3/final_script/adapter*.py
   ├─ enhancement_chunks_v1.py（VAD-guided 时）
   └─ 输出增强录音 path、sample_rate、total_samples
      ↓
planning.chunks(state, enhanced=True)
└─ 将 chunk.audio_path 改为增强录音
   并写入 audio_start_sample/audio_end_sample、
   enhancement_version、audio_fingerprint
```

如果启用增强，ASR/FA 使用增强录音的 chunk sample 范围；否则使用 ingest/VAD 产生的原始音频路径。

### 3.4 ASR1 / ASR2：同一 chunk 的两条识别假设

```text
planning.items('asr1'/'asr2')
└─ chunks(state, enhanced)
   ↓
asr1: models/qwen3-asr/final_script/adapter.py
asr2: models/funasr/final_script/adapter.py
（或 config.assignments / --model 指定的模型）
   ├─ worker.py 加载 Adapter
   ├─ 可选 FunASR/Qwen/FireRed 流水线包装
   ├─ 音频预取、batch、OOM 拆分、checkpoint
   └─ 输出 text、language、chunk、generation 等
      ↓
asr1/.../result.json
asr2/.../result.json
```

worker 保存前会补充 `_provenance`，校验 chunk/audio fingerprint，并将结果登记到 stage manifest。

### 3.5 disagreement：比较两条 ASR 结果

```text
planning.items('disagreement')
├─ head_results(asr1)
├─ head_results(asr2)
├─ hypothesis_payload()：保留文本/语言/来源引用
└─ 对每个 chunk 生成 hypotheses=[asr1, asr2]
   ↓
models/disagreement/final_script/adapter.py
   └─ 按 token distance / threshold 判断 high 或 low
      ↓
disagreement manifest
├─ manifests/high_disagreement.json
└─ manifests/low_disagreement.json
```

### 3.6 ASR3：只重识别 high disagreement

```text
planning.items('asr3')
└─ 读取 disagreement 的 .high.json
   └─ 只保留 high chunks
      ↓
models/firered-asr2/final_script/adapter.py
      ↓
asr3/.../result.json
```

### 3.7 rover：融合 ASR 假设，形成 FA 文本

```text
planning.items('rover')
├─ 当前 chunks
├─ asr1 hypothesis
├─ asr2 hypothesis
├─ disagreement route
└─ high route 时追加 asr3 hypothesis
   ↓
models/roverfusion/final_script/adapter.py
└─ ROVER/NIST 融合
   └─ 输出 text、hypotheses、chunk、transcript_status
      ↓
rover/.../result.json
rover/recordings/<recording_id>_c<channel>.json
```

`planning.items('align')` 只消费 ROVER head：

```python
{
  'id': recording_chunk_id,
  'chunk': value['chunk'],
  'text': value['text'],
  'fusion_version': digest(value),
  'language': 唯一可推断语言或 None
}
```

ROVER 的 `transcript_status == no_transcript` 会被 align 规划跳过。

## 4. 当前 align / Force Alignment 详细树

### 4.1 stage 选择和配置解析

```text
cli.py
└─ dependencies['align'] = {'rover'}
   └─ run_stage('align', ...)
      ├─ planning.descriptor('align', cfg, args)
      │  ├─ ADAPTERS['align']
      │  │  = qwen3-asr + models/qwen3-fa-mfa/final_script/adapter.py
      │  ├─ checkpoint
      │  │  = Qwen3-ForcedAligner-0.6B
      │  ├─ language / dataset MFA 路由
      │  ├─ dictionary / acoustic_model / mfa_python
      │  └─ timestamp_tolerance / padding / mfa_jobs / timeout
      ├─ planning.items('align')
      │  └─ 读取 rover head，生成 chunk-level FA task
      ├─ stage_plan.save/load()
      ├─ worker.py
      │  ├─ load_module(...).Adapter(job)
      │  ├─ adapter.process_batch(items)
      │  ├─ validate + 写 result.json
      │  └─ 写 align manifest
      └─ finalization.export_recordings()
```

配置来源是 `config/pipeline.v3.yaml: stage_models.align`。显式 dataset route 优先于 language route；例如 Chinese 使用 `chinese_adapter.py` 及 Mandarin MFA 资源，Japanese/Korean/Thai/Vietnamese 使用相应 multilingual route。

### 4.2 重要澄清：当前不是“单语言 MFA、混合语言 Qwen”的二选一路由

按当前文件夹内的实现，实际顺序是：

```text
每个 align chunk
  → Qwen Forced Aligner（所有语言/所有 speech 的主路径）
  → 时间戳校验
  → 仅当存在 zero-duration、invalid timestamp 或 issues/error
      → MFA 句子级 repair fallback
```

证据：

- `models/qwen3-fa-mfa/final_script/adapter.py:process_batch()` 对 active rows 统一调用 `self.aligner.batch(...)`。
- `models/qwen3-fa-mfa/source/qwen3-fa/core.py:QwenAligner.batch()` 的主后端是 `model.align(...)`（`backend=public`）。
- `models/qwen3-fa-mfa/final_script/adapter.py:_repair()` 只有在 `issues` 或 `error` 存在时才调用 `SentenceRepair`。
- `chinese_adapter.py` 和 `multilingual_adapter.py` 的注释都写明：它们选择的是 **MFA repair 资源**，不是把正常单语言 speech 改道到 MFA。

语言信息仍然会影响两件事：

1. `planning.py:descriptor()` 根据 dataset/language 选择 Qwen/MFA 的语言配置、词典和 acoustic model。
2. 如果触发 repair，MFA 使用该语言对应的资源和文本归一化规则。

因此，当前代码的准确表述是：**Qwen FA 主对齐 + 按语言选择的 MFA 异常修复**。如果目标是“单语言 speech 全部直接 MFA、混合语言 speech 走 Qwen FA”，当前目录中还没有这样的 speech-level router；需要在 `planning.items('align')` 或 `models/qwen3-fa-mfa/final_script/adapter.py` 增加路由，并相应调整输入、输出及校验逻辑。

## 4.2 当前真正执行的 FA+MFA Adapter

```text
models/qwen3-fa-mfa/final_script/adapter.py
└─ Adapter.__init__(job)
   ├─ QwenAligner(checkpoint, device, backend, language, tolerance)
   ├─ 配置 MFA Python、dictionary、acoustic_model
   └─ 校验依赖文件存在
      ↓
Adapter.process_batch(items)
├─ _row(item)
│  └─ V3 task → {audio, text, duration, chunk_start/end,
│                 audio_start/end_sample, recording_id, channel, language}
├─ _chunk_audio(row, tmpdir)
│  ├─ 若 audio 已是 chunk 时长：直接使用
│  └─ 否则按 sample bounds 裁剪成临时 PCM16 WAV
├─ QwenAligner.batch(rows)
│  ├─ 跳过空文本 active rows
│  ├─ QwenAligner._public()：model.align(...)
│  │  （backend=public，当前配置默认）
│  ├─ 或 _native()：processor + thinker logits（仅 backend=native）
│  ├─ ForcedAlignResult → {text,start,end}
│  ├─ validate_words()
│  │  ├─ finite
│  │  ├─ 0 <= start <= end <= duration+tolerance
│  │  └─ 单调性
│  └─ 加回 chunk_start，得到原录音相对时间
├─ _prediction(...)
│  └─ 兼容 dict / structured item / tuple 类型，统一成 words[]
├─ _repair(predictions)
│  ├─ process.audit()
│  ├─ 没有 issues/error：直接返回
│  └─ 有问题：SentenceRepair(...).repair()
└─ _v3_result(item, prediction)
   └─ 输出 {text, chunk, words:[{text,start,end}], language}
      ↓
worker.py
├─ 加 _provenance
├─ common.validate()
├─ 原子写 align/.../result.json
└─ 状态写入 StateWriter / manifest
```

### 4.3 MFA 句子修复逻辑

```text
models/qwen3-fa-mfa/source/qwen3-fa+mfa/repair.py
└─ SentenceRepair.repair(predictions)
   ├─ 找到 Qwen zero-duration words / issues
   ├─ prepare_sentence_tasks()
   │  ├─ 按 sentence_spans 分组相邻受影响句子
   │  ├─ 以 padding 裁剪对应音频
   │  ├─ 写 mfa/corpus/<task>.wav
   │  └─ 写 mfa/corpus/<task>.lab
   ├─ 启动 MFA（配置的 mfa_python/dictionary/acoustic_model）
   ├─ 读取 aligned JSON 或 MFA SQLite corpus.db
   ├─ map_mfa_words()
   │  ├─ 校验 transcript coverage
   │  ├─ 允许 MFA 拆分 contraction
   │  └─ 拒绝 OOV、零时长、重叠、token 数变化
   ├─ splice_sentence()
   │  ├─ 受相邻未修复词边界约束
   │  └─ 再做时间单调/范围校验
   └─ 回写 prediction['words']
```

## 5. align 的输出形态

### chunk 级

```text
<output_root>/align/<model>/<chunk_id>/<signature>/result.json
{
  "text": "...",
  "chunk": {...},
  "words": [
    {"text": "...", "start": 12.345, "end": 12.678}
  ],
  "language": "...",
  "_provenance": {...}
}
```

### recording/channel 级

```text
<output_root>/align/recordings/<recording_id>_c<channel>.json
{
  "recording_channel": "..._c...",
  "text": "按 chunk 排序拼接的文本",
  "chunks": [...],
  "words": [...],
  "coverage": {
    "status": "complete | partial",
    "expected_chunks": ...,
    "completed_chunks": ...,
    "missing_chunk_ids": [...]
  }
}
```

生成位置：`pipeline/audio_pipeline/finalization.py: collect_recording() → export_recordings()`。align 阶段完成后，`cli.py` 更新 stage head、manifest、失败文件和 ledger。

## 6. 调度、恢复和失败边界

```text
cli.run_stage()
├─ stage_lock()
├─ State(root)
├─ resume_checkpoint / stage_plan
├─ items() 构造不可变 task
├─ worker allocation（GPU、batch、CPU、memory）
├─ worker.run_job()
│  ├─ 单 worker 常驻 Adapter
│  ├─ batch inference
│  ├─ CUDA OOM 时递归拆 batch
│  ├─ 单 task 失败写 failed 状态
│  └─ 完成结果原子持久化
└─ finalization
   ├─ manifest
   ├─ recordings export
   ├─ state.set_head()
   └─ ledger
```

`config/resources.v3.yaml` 中 `stage_pipelines.align.enabled=false`，原因是 FA+MFA Adapter 自己拥有完整的 Qwen + MFA 调用；因此当前 align 不走 `stage_pipelines.AlignmentPipeline` 的旧原生路径，而是直接执行 `models/qwen3-fa-mfa/final_script/adapter.py`。该开关若被改为 true，执行路径会进入 `pipeline/audio_pipeline/stage_pipelines.py: AlignmentPipeline`，这与当前 FA+MFA 修复路径不是同一条实现。

## 7. 一句话数据流

```text
音频文件/manifest
→ ingest 源快照
→ VAD speech chunks
→（可选）增强音频及 sample bounds
→ Qwen/FunASR 双 ASR
→ disagreement 路由 + FireRed ASR3
→ ROVER 融合文本
→ qwen3-fa+mfa Adapter
→ Qwen3 Forced Aligner 词时间戳
→ MFA 句子级修复（必要时）
→ align/recordings/*.json 词级 Force Alignment 结果
```

