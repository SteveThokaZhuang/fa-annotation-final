# 当前可运行音频 Pipeline 代码树与 Step3 Annotator 逻辑报告

- **检查对象**
  - 主 pipeline：`/hbt_share02/202610/ob/ob_ppl_env_data/audio_pipeline_optimized/Pipeline_github_v3_Merge/pipeline_runnable_subset_20261111`
  - Step3 测试/代码快照：`/hbt_share02/202610/ob/ob_ppl_env_data/audio_pipeline_optimized/Pipeline_github_v3_Merge/annotation_step3_test_20261117`
- **报告日期**：2026-11-17
- **依据**：当前目录源码、配置、`RUNNABLE_STRUCTURE.md`、Step3 `README.md`/`RELEASE_NOTES.md`、测试报告和实际输出结构。

## 1. 先给结论：从 WAV 到最终标注的运行关系

主 V3 控制器的完整阶段顺序为：

```text
WAV/FLAC/OGG
  -> ingest
  -> dnsmos
  -> vad
  -> enhance（按数据集配置，可关闭）
  -> asr1（Qwen3-ASR）
  -> asr2（FunASR）
  -> disagreement（ASR1/ASR2 差异与 ASR3 路由）
  -> asr3（FireRedASR2，只处理路由样本）
  -> rover（多 ASR 融合，导出 recording JSON）
  -> align（常规 V3 all 流程中的词级强制对齐）
  -> Step3 annotator（双人全双工事件/空缺标注）
```

实际入口有两条：

1. **常规 V3**：`run_v3.sh`/`run.py` 调用 `pipeline.audio_pipeline.cli`，按配置运行各阶段；README 明确把 `align` 列为 `rover` 后的阶段。
2. **FA 包装入口**：[`sz-fa/run_fa_pipeline.py`](../sz-fa/run_fa_pipeline.py) 的 `STAGES` 只包含 `ingest` 到 `rover` 的九个阶段，然后把 ROVER recording JSON 交给 `process_speeches.py` 和纯 MFA。它**没有在脚本内调用 Step3 annotator，也没有把 `align` 放进这个九阶段列表**。

因此，Step3 当前是一个独立的后处理入口：它要求先存在 V3 的词级 FA recording 导出（`align/recordings/<recording_id>_c<channel>.json`），再由 Step3 `pipeline.py` 读取两个参与者的 FA 和对应原始音频。

## 2. 可运行代码树（按运行职责整理）

```text
pipeline_runnable_subset_20261111/
├── run.py                         # 将 pipeline/vendor/models 源码加入 PYTHONPATH，调用 audio_pipeline.cli
├── run_pipeline.sh                # 传统/单阶段控制入口
├── run_v3.sh                      # V3 控制入口
├── stages/                        # 单阶段薄包装 -> run_pipeline.sh
│   ├── ingest.py dnsmos.py vad.py enhance.py
│   ├── asr1.py asr2.py disagreement.py asr3.py
│   ├── rover.py align.py
├── config/
│   ├── pipeline.v3*.yaml          # 阶段顺序、模型、checkpoint、语言/阈值/增强/对齐配置
│   ├── datasets.v3*.yaml          # 数据集路径、语言、扩展名、是否增强
│   └── resources.v3*.yaml         # GPU/CPU、并发、缓存、预取、resume 资源策略
├── pipeline/audio_pipeline/
│   ├── cli.py                     # 解析命令、加载配置、规划/调度阶段
│   ├── planning.py / stage_plan.py# 上游依赖、任务签名、阶段计划
│   ├── worker.py                  # worker、adapter 生命周期、batch、持久化、重试
│   ├── datasets.py / catalog.py   # 数据集扫描和 manifest
│   ├── audio.py / ram_audio.py    # 音频读取、规范化、RAM/缓存传输
│   ├── batching.py / scheduling.py# batch 和资源调度
│   ├── staging.py / prefetch.py   # 本地暂存和预取
│   ├── enhancement_dispatch.py    # enhance 分块/完整增强调度
│   ├── enhancement_chunks_v1.py   # 增强切分、边界、拼接策略
│   ├── dnsmos_pipeline.py         # DNSMOS 窗口与校准流水线
│   ├── funasr_pipeline.py         # FunASR 编码/解码预取流水线
│   ├── stage_pipelines.py         # VAD/DNSMOS/ASR/align 等阶段预取包装
│   ├── disagreement/相关逻辑      # 由 disagreement adapter 完成文本差异判定
│   ├── finalization.py / writer.py# manifest、汇总、发布
│   ├── failure_policy.py          # 失败、空文本和基础设施错误判定
│   └── resume*.py / cache*.py     # checkpoint、完成回执、缓存和断点续跑
├── models/*/final_script/          # 每个阶段的 Adapter；worker 动态加载 Adapter.process/process_batch
│   ├── audio_ingestion/adapter.py
│   ├── dnsmos/final_script/adapter.py
│   ├── VAD/FireRedVAD/final_script/adapter.py
│   ├── deepfilternet3/final_script/adapter*.py
│   ├── qwen3-asr/final_script/adapter.py
│   ├── funasr/final_script/adapter.py
│   ├── disagreement/final_script/adapter.py
│   ├── firered-asr2/final_script/adapter.py
│   ├── roverfusion/final_script/adapter.py + fuse.py
│   └── qwen3-fa-mfa/final_script/adapter.py（常规 align 适配器）
└── sz-fa/
    ├── run_fa_pipeline.py         # 上游九阶段 + speech-level FA + pure MFA
    └── src/
        ├── process_speeches.py    # ROVER chunk -> speech archive/语言路由/脏文本旁路
        ├── run_recordings_mfa.py  # recording JSON 展开、裁 WAV、批量调用 MFA
        └── mfa/process.py          # MFA 子进程、TextGrid/JSON 解析、边界归一化、失败审计

annotation_step3_test_20261117/new_code_snapshot/step3/
├── pipeline.py                    # Step3 总编排、输入检查、窗口/空缺生成、输出汇总
├── input_v3.py                    # 从 align recordings 读取双参与者词级 FA
├── input_seamless.py              # 另一条 Seamless 发布数据入口
├── event_extractor.py             # overlap 候选生成、语义后处理、KEEP/MERGE/DROP 落地
├── run_multi.py                   # Qwen 文本多事件 annotator、JSON schema、重试/修复
├── audio_judge.py                 # DuplexJev 双轨混音和音频投票
├── gap_judge.py                   # Qwen 空缺判定、复判、grounding/risk
├── risk_annotations.py            # 风险旁路标记，不改写业务标签
├── run_multi.py                   # overlap 的 Qwen 调用及结构校验
├── interaction_*.txt              # 多事件、entry refinement、floor check 规则
└── test_*.py                      # Step3 回归测试
```

模型权重、数据集、MFA 环境、Qwen 服务和 DuplexJev checkpoint 不在可运行代码子集中；配置中的绝对 checkpoint/环境路径必须在运行机存在。

## 3. WAV 进入主 pipeline 后，各阶段做什么

### 3.1 ingest：建立源音频事实和任务输入

入口是 `stages/ingest.py -> run_pipeline.sh ingest`，最终由 worker 加载 `models/audio_ingestion/final_script/adapter.py`。数据集目录或 JSONL manifest 中的 `.wav/.flac/.ogg` 被扫描，读取音频头、采样率、声道、时长、源文件大小/mtime 等元数据，形成后续阶段的 durable manifest。这里主要做**发现、去重、格式/元数据登记和源指纹**，不做 ASR。

### 3.2 dnsmos：按窗口计算音质分数

`models/dnsmos/final_script/adapter.py` 读取源音频，按配置的 `window_seconds=9.01`、`hop_seconds=1` 和 `window_batch_size=64` 构造窗口，运行 DNSMOS ONNX 模型并聚合记录级质量指标。它默认不阻塞 VAD；分数进入阶段结果和后续质量分析。

### 3.3 vad：检测语音区间并切 chunk

`models/VAD/FireRedVAD/final_script/adapter.py` 在音频上运行 FireRedVAD，结合 `speech_threshold`、`min_speech_ms`、`padding`、`merge_gap`、`chunk_seconds` 等配置生成语音区间/块。后续 ASR 以这些语音块为输入；worker 可批量执行并对 GPU/CPU 内存做 admission。

### 3.4 enhance：可选降噪/增强

数据集配置 `enhance: true` 时，调度到 DeepFilterNet3：

- `enhancement_dispatch.py`/`enhancement_chunks_v1.py` 决定整段还是分段。
- `adapter_auto.py` 按时长、RAM/GPU 估计自动选择 whole/chunked。
- `adapter_chunked.py` 对大录音切分，完成频谱特征准备、模型推理、交叉淡化和原顺序拼接。

输出是增强音频/增强引用，供 ASR 使用；原始音频和增强音频的来源关系保留在结果元数据中。

### 3.5 asr1：Qwen3-ASR

`models/qwen3-asr/final_script/adapter.py` 对 VAD chunk（或增强后的 chunk）做批量语音识别，输出至少包含 `text` 以及任务/音频标识。配置中的 `assignments.asr1=qwen3_asr`，可使用 native 或 vLLM engine。

### 3.6 asr2：FunASR

`models/funasr/final_script/adapter.py` 运行第二个独立 ASR。其 encoder batch、最大 padded frames 和 vLLM 解码由 resources/config 控制；`funasr_pipeline.py` 可以把下一批音频特征准备和当前批解码重叠。

### 3.7 disagreement：决定是否进入 ASR3

`models/disagreement/final_script/adapter.py` 读取 ASR1/ASR2 文本：英文默认 WER，中文默认 CER，`settings.disagreement.reference` 指定参考方向，默认阈值为 `0.1`。分数大于阈值，或任一结果缺失/为空，会标记高风险并路由到 ASR3。空字符串目前被保留为字符串结果，属于高风险路由条件，但不自动升级成通用失败。

### 3.8 asr3：FireRedASR2 路由补充

`models/firered-asr2/final_script/adapter.py` 只处理 disagreement 路由的样本，使用 FireRedASR2（当前 V3 配置为 vLLM、beam size 1）。其文本结果追加到后续 ROVER 的 hypothesis 集合。

### 3.9 rover：多模型文本融合并导出 recording JSON

`models/roverfusion/final_script/adapter.py` 调用 `models/roverfusion/fuse.py` 和 ROVER 二进制，将 ASR1/2/3 hypothesis 按配置的 `alpha`、`null_confidence`、`unit` 融合。结果形成 chunk 级最终文本；recording 导出目录通常为：

```text
<output_root>/production/<dataset>/rover/recordings/*.json
```

这些 recording JSON 是后续 speech-level FA 和 Step3 输入的上游证据。空文本不会被统一当成失败；全空场景可能仍以 `text=""` 的 fused 结果继续下游。

### 3.10 align：把文本变成词级时间轴

常规 V3 `all` 流程中的 align 使用 `stage_models.align` 配置的 `qwen3-fa-mfa` adapter，可按语言/数据集选择 Qwen forced aligner 或 MFA 路由。它读取录音 chunk、最终文本和音频，输出词级 `start/end/text`，并发布 `align/recordings/<recording_id>_c<channel>.json`。Step3 的 `input_v3.py` 直接消费这些 JSON。

### 3.11 sz-fa 分支：纯 MFA CPU（不是 Step3）

[`sz-fa/run_fa_pipeline.py`](../sz-fa/run_fa_pipeline.py) 的后半段是另一条 FA 基线：

1. `src/process_speeches.py` 读取 ROVER recording JSON，按 mapping 合并两位 speaker，保留已有词时间；缺词时才按文本构造 fallback units；检测语言和乱码（messy code），把多语言 speech 路由到 `qwen-fa`，单语言路由到 `mfa`，输出 `speech_archive.json`、`background_audio.json`、`messy_code_rerun.json`。
2. `src/run_recordings_mfa.py` 展开 recording JSON，按 chunk 的 `start/end/path/channel` 裁出物理 WAV，写 manifest，分批调用 MFA。
3. `src/mfa/process.py` 解析 MFA 输出，把局部时间加回 `chunk_start`，做边界归一化和结构审计，写 `batch_results/*.jsonl.gz`、`failures.jsonl`、`unresolved.json`、`summary.json`。

这条纯 MFA 结果不能替代 Step3 的全双工事件 annotator；Step3 需要双参与者词级时间轴和两条可同步访问的原始音轨。

## 4. Step3 的输入契约和预处理

Step3 入口：

```bash
python step3/pipeline.py \
  --align-recordings <V3 align/recordings> \
  --pairs <双人配对清单.jsonl> \
  --output <新的输出目录> \
  --jev-checkpoint <DuplexJev checkpoint> \
  --jev-device cuda:0 \
  --qwen-endpoint http://127.0.0.1:18770/v1/chat/completions \
  --qwen-model qwen38-27b
```

也可以使用 `--prepare-only` 做不加载大模型的输入/窗口检查。

### 4.1 `input_v3.load_channel`

对每个 participant：

- 校验 `recording_id`、FA channel 和文件名安全格式。
- 读取 `<recording_id>_c<channel>.json`，要求 `recording_channel` 一致、coverage complete、chunk 不为空。
- 对每个 chunk 检查 recording/channel、chunk ID 唯一、时间合法、词的局部时间在 chunk 内。
- 将词时间从 `chunk.start + word.start/end` 恢复为录音绝对时间；词 ID 变成 `recording_channel:chunk_id:word_index`。
- 如果整句词序与上游 `row["text"]` 完整匹配，则仅恢复句末标点，不改词时间。
- 强制一个 participant 的 FA 只能对应一个源音频路径，且同一声道词不能重叠。

`load_conversation` 要求恰好两位不同 speaker、不同 FA channel、两条非空词流和两条不同物理音轨。配对清单可用 `audio_path` 覆盖 chunk 的 source path，也可用 `audio_channel` 指定多声道 WAV 的物理声道。

### 4.2 共同时间轴与测试窗口

`WordTimeline` 对每个 speaker 按绝对时间排序，建立快速区间查询，并按 `>0.35s` 间隔或标点估计 lexical unit 起点。Step3 可以用：

- 默认整段按 8 秒 core window 扫描，两侧各加最多 4 秒上下文；
- 配对清单中的 `interval` 限制分析音频范围；
- `core` 指定真正计入事件起点/空缺中心的核心范围。

如果 `interval/core` 不落在两条音轨的共同 duration 内，录音会失败；Step3 不会静默把错位音轨当作有效输入。

## 5. Step3 overlap annotator：重叠/打断事件的完整逻辑

### 5.1 候选生成：先宽提案，后语义裁决

`event_extractor.extract_candidates` 对两位 speaker 的词流分别形成 lexical units：连续词之间间隔不超过 0.35 秒且未遇句末标点时归为一个 unit。每个 unit 与另一 speaker 配对，计算：

- `overlap_intervals` 和 `overlap_seconds`；
- 目标 speaker 的起点/终点、词 ID、文本；
- 对方在 onset 前 0.7 秒是否刚活跃；
- 对方是否在 onset 时仍处于 speech run（`other_active_run_at_onset`）；
- `proposal_kind`：`ENTRY_OR_CONTINUATION` 或 `CONTINUATION_ONLY`。

这里**重叠本身不等于打断**。`pipeline.windows` 只保留“对方在 onset 时仍 active 且实际有 overlap”的候选进入 overlap annotator。

### 5.2 双轨音频判断：DuplexJev

`audio_judge.render_mix` 对窗口两条物理音轨：

- 按共同 absolute interval 从源 WAV 读取目标声道；
- 非 16 kHz 时用 `resample_poly` 重采样；
- 检查非 finite sample、窗口完整性和重采样长度；
- 两轨按列堆叠后求均值写成临时单轨 mix WAV，并做峰值增益保护。

`MixedAudioJudge.vote` 调用 DuplexJev `Decider.from_pretrained`，对每个候选执行 4 次 option permutation（`n_perm=4`，seed=0），三分类：

- `BACKCHANNEL`：支持性反馈，不抢 floor；
- `INTERRUPT`：对方尚未结束时试图抢/改变 floor；
- `NEITHER`：普通接话、延续或不构成事件。

Qwen 请求体会同时携带候选前后约 3 秒的双方转录，用于后续语义判断；JEV 是音频证据，不直接成为最终业务标签。

### 5.3 Qwen 多事件文本 annotator：KEEP/MERGE/DROP

`run_multi.classify` 重新生成同一组候选，向 Qwen 服务发送 JSON schema 请求。输出必须覆盖所有候选，字段为：

```json
{"candidate_id":"...","action":"KEEP|MERGE|DROP",
 "label":"BACKCHANNEL|INTERRUPT","merge_into":"...","reason":"..."}
```

处理规则：

1. `KEEP`：保留一个事件，但仅当对方在 onset 时确实 active；否则滤为 `future_overlap_only_no_recent_recipient_activity`。
2. `DROP`：候选不成为事件，但如果同候选的 JEV 是 `INTERRUPT/BACKCHANNEL`，风险层会记录为 `possible_event_omission`。
3. `MERGE`：只能合并到更早、同方向（同 initiator/recipient）、同业务功能的父事件；非法 parent、跨方向或功能不一致会被修复为 DROP。
4. 文本输出被截断或 schema 错误时，先重试；必要时切换为短 alias key 输出，仍要求候选全覆盖。
5. `refined=True` 加载 `interaction_entry_refinement.txt`；`floor_checks=True` 加载 `interaction_floor_checks.txt`，执行 terminal answer 抑制、echo intervention 恢复、open-clause continuation 抑制等规则修补。
6. 最终事件保留绝对 `start/end`、方向、`label`、候选 ID、文本、重叠时长、可见窗口、是否越过 clip 和 alignment flags。

Step3 的发布说明明确：双向打断是两个 directed events；嵌套反馈可作为独立事件；普通回答/延续不能仅因 overlap 被标成 interrupt。

### 5.4 overlap 风险标注

`risk_annotations.tag_pipeline_result` 不改写 `event.label`，只增加：

- `audio_judge_missing`；
- `text_audio_disagreement`；
- `small_audio_option_margin`（音频 top1-top2 概率差小于 0.2）；
- alignment flags；
- Qwen DROP 但 JEV 支持事件时的 `rejected_candidate/possible_event_omission`。

输出的 `quality.risk_flag` 是可审计风险信号，不是校准后的错误概率。风险事件写入 `risk_subsets/*.json`，同时保留在主 recording JSON。

## 6. Step3 gap annotator：同一 speaker 的长空缺逻辑

### 6.1 候选生成

`pipeline.gap_candidates` 对每位 speaker 的相邻词对 `(a,b)` 计算 `a.end -> b.start`：

- 严格大于 0.5 秒才生成候选；
- 可按 `core` 限制 gap 中心，按 `interval` 限制两端；
- 左右各取最多 12 秒上下文；超过 24 秒时设置 `long_gap_middle_omitted=true`；
- `target_right_unit` 从右侧词开始，遇到 >0.5 秒或句末标点停止；
- 同时收集 focus speaker 前后文字、对方临近语音、所有 context spans。

正式业务标签只有：

- `PAUSE`：同一沟通动作的延续、完成、修正、重述或支持说明；
- `TURN_TRANSITION`：前一动作已经结束/让出/放弃，右侧开始新的动作。

同一 speaker 开启独立表达也可以是 `TURN_TRANSITION`，不要求一定换 speaker。

### 6.2 首判 Qwen

`gap_judge.request` 向 Qwen Chat Completions 发 JSON schema 请求，temperature=0、max_tokens=1024、最多 2 次请求。模型必须返回：

- `relation`：`CONTINUATION|INDEPENDENT|UNCLEAR`；
- `left_evidence`、`right_evidence`：必须是输入上下文中的短字面片段；
- `reason`；
- `label`：`PAUSE|TURN_TRANSITION`。

`risk(payload, decision)` 额外检查：请求失败、relation 与 binary label 冲突、证据不在原文中、gap 附近存在 UNKNOWN speaker、任一侧上下文不超过 5 个词。证据 grounding 失败会升高风险，但不会擅自改标签。

### 6.3 风险优先复判

每条 recording 的复判预算为 `ceil(gap_count × review_fraction)`，默认 `review_fraction=0.2`。只从“首判有 label 且 risk 非空”的 gap 中按风险分数降序选择；没有风险项时不强制复判。

每个被选 gap 并行执行两次：

1. `short`：裁短上下文后再次调用 `PROMPT`；
2. `verify`：原始上下文调用 `VERIFY`，要求独立核对边界。

只有两次都成功、label 一致、relation 对应同一 binary label、证据均 grounded 时才生成 `proposal`。当前策略**保留首判 label，不自动用 proposal 覆盖**；复判只是旁路质量检查。

### 6.4 gap 风险输出

风险原因包括 `unclear_relation`、`relation_label_conflict`、`ungrounded_excerpt`、`unattributed_near_gap`、`short_context_side`、`rejudge_failure`、`rejudge_label_disagreement`、`no_valid_business_label` 等。每条 gap 输出 `quality`；风险项进入 `risk_subsets`。

## 7. Step3 输出和运行状态

非 prepare 模式每条 recording 写：

```text
<output>/recordings/00000001.json
<output>/risk_subsets/00000001.json
<output>/summary.json                 # 运行中原子更新，结束时 run_state=finished
<output>/provenance.json              # manifest/code/prompt hash + 参数
```

`summary.json` 关键指标：

- `recordings/completed/completion`；
- `event_counts`、`gap_counts`、`gap_candidates`；
- `model_requests.jev/overlap_qwen/gap_first/gap_review`；
- `reviewed_gaps`、`completed_reviews`、`review_rate`、`review_completion`；
- `risk_flagged_items`、`possible_event_omissions`；
- `failures_by_stage`、wall time、吞吐和 RTF。

`run_state=running` 表示尚未正常收尾；`finished` 只表示遍历结束，仍需检查 `failures_by_stage` 和 completion。输出目录必须是新目录，当前没有跨进程断点续跑。

## 8. 当前测试证据和未完成项

`annotation_step3_test_20261117/REPORT.md` 记录：

- Step3 回归测试 **33/33 通过**；
- 真实大批量 FA 的 `--prepare-only`：25/25 ready、774,334 个对齐词、25 个 overlap windows、5,929 个 gap candidates、0 个 invalid FA word；
- 完整 JEV/Qwen 语义 annotator 当时未执行，原因是目标节点没有 DuplexJev checkpoint，`18770` Qwen endpoint 未启动；所以这些产物证明输入契约、窗口和候选准备可运行，不代表模型语义标签准确率。

## 9. 当前实现的集成边界和风险点

1. **Step3 没有接入 `sz-fa/run_fa_pipeline.py`**：要运行 Step3，必须另行准备 `align/recordings`、双人 pairs 和 JEV/Qwen 服务。
2. **Step3 不是从任意单条 WAV 自动推断两位说话人**：它依赖 pairs 显式指定两个 recording/channel；单轨混音若没有可靠 speaker attribution，不能直接得到可信的双向事件。
3. **FA 是硬门槛**：partial coverage、缺词对齐、跨源音频、词重叠、时间越界会在输入阶段失败；需要先修复上游 chunk/FA。
4. **音频判定和文本判定是互补证据**：JEV 的音频投票不会直接替换 Qwen 的 KEEP/MERGE/DROP；两者不一致只进入风险侧写。
5. **gap 复判不覆盖首判**：即使 `short/verify` 都提出另一标签，当前发布策略仍保留首判并记录 disagreement。
6. **风险标记不是概率**：`risk_flag`、`possible_event_omission` 只用于审计/筛选，不能解释成已确认错误或校准置信度。
7. **空 ASR 文本可继续下游**：主 pipeline 的空字符串结果可能进入 ROVER；若要进入 Step3，必须确保最终 recording 有可用、完整、与音轨一致的词级 FA。

## 10. 建议的实际运行顺序

```text
1. 用 run_v3.sh all 跑主 pipeline，至少生成 rover；
2. 确认 align 阶段已完成，检查 align/recordings 的 coverage=complete；
3. 为每个双人会话生成 pairs.jsonl，明确 recording_id、FA channel、audio_path/audio_channel；
4. 先运行 Step3 --prepare-only，检查 shared duration、FA 完整性、overlap/gap 数量；
5. 准备 DuplexJev checkpoint 和 Qwen endpoint，使用新的输出目录运行正式 annotator；
6. 检查 summary.json 的 completion、failures_by_stage、review_completion 和 risk_subsets；
7. 对 high-risk / possible omission 做人工或专项复核。
```


## 11. 2026-11-18 本次代码更新后的实际落地状态

本次已修改 `sz-fa`，使 speech-level 路由真正进入执行链：

- `process_speeches.py` 读取 `Mapping_with_rover/*_with_rover_manifest.json`，按 `conversation_id` 生成一个 speech archive；每条 speech 同时保存 `speaker_1`、`speaker_2` 的 chunk ID 列表和合并后的 chunks。
- 每个 chunk 保存 `languages`、`language_evidence`、`speech_language_set` 和 `speech_route`。支持中、英、日、韩、越、泰，并补充阿拉伯、俄语、印地语脚本识别。
- 单语言 speech 生成 `route_inputs/mfa_recordings/*.json`，只有这些 speech 进入 `run_recordings_mfa.py`。
- 混合语言 speech 生成 `route_inputs/qwen_fa_input.jsonl`，并先从原始 WAV 按 chunk 的 `start/end/channel` 物理裁出精确 WAV，避免 Qwen FA 错把整条源录音当成 chunk；提供 `--qwen-checkpoint` 时才执行 Qwen FA。
- `run_fa_pipeline.py` 不再把全部 ROVER recordings 直接送 MFA，而是按上述 route 执行 MFA/Qwen 两条分支。
- 新增 `reconcile_mfa.py`：对 MFA 输出中有 ASR 文本但 `error` 或没有 words 的 chunk，写入 `background_audio.json`，并从权威 `speech_fa/speech_archive.json` 删除；典型原因包括 `Missing MFA alignment`。
- `summary.json` 继续统计 Qwen 路由的 0 时长字比例、全部 0 时长 chunk 比例和 0 时长 speech 比例；speech 的统计单位包含两位 speaker 的全部 chunks。
- 乱码检查现在生成 `messy_code_rerun.json`，明确记录“先 chunk、仍乱码再 speech”的重跑范围和最终失败策略；乱码样本本同步扩展到中英日韩越泰、阿拉伯、俄语、印地语。

验证结果：`process_speeches.py` 的临时双 speaker mixed-language fixture 正确生成一个 speech、两条 speaker chunk，并路由到 `qwen-fa`；纯英文 fixture 正确路由到 `mfa`；MFA reconciliation fixture 正确保留有效 chunk、删除失败 chunk，并生成 `background_audio.json`。相关 Python 文件已通过 `py_compile`。

当前仍需运行机提供实际 Qwen forced-aligner checkpoint，才能执行混合语言 speech 的 GPU FA；没有 `--qwen-checkpoint` 时，代码会生成完整 Qwen 输入和精确 chunk WAV，但不会假装已经完成 Qwen 推理。

## 12. 2026-11-18 speech 异常筛选与数据隔离

新增 [`sz-fa/src/anomaly_filter.py`](../sz-fa/src/anomaly_filter.py)，并已接入 `run_fa_pipeline.py`。它在 MFA reconciliation 和可选 Qwen FA 完成后运行，逐 speech 汇总 chunk 异常：

- `multi_language`：speech 路由为 qwen-fa、speech 语言集合超过一种，或某个 chunk 的语言集合超过一种；
- `missing_mfa_alignment`：MFA 输出报错、没有 words，或被 `reconcile_mfa.py` 放入 background audio；
- `only_one_speaker_dialogue`：speech archive 中少于两个有效 speaker；
- `qwen_fa_alignment_error`：Qwen FA 对非空 ASR 文本没有输出对齐词或有 error；
- `asr_messy_code`：chunk 被乱码规则命中；
- `zero_duration_word`：Qwen 输出出现 0 时长 word。

每个异常 speech 都写成**完整 speech JSON**，包含两个 speaker 的剩余 chunks、speech 元数据、`anomaly_reasons` 和 `anomaly_chunks`，目录与正常数据完全分离：

```text
<output>/fa_data/
  <speech_id>.json                 # 只有无异常 speech
  clean_summary.json
<output>/fa_anomalies/
  <speech_id>.json                 # 整个异常 speech，不进入 fa_data
  anomaly_summary.json
```

因此 `fa_data` 是可直接进入下游数据集的 clean 集合；`fa_anomalies` 是审计/返修集合。异常筛选 fixture 已验证：multi-language、missing MFA alignment、only-one-speaker 三类均能把整条 speech 放入异常目录，正常数据目录不写入对应 speech。
