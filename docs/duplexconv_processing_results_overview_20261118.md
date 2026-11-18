# DuplexConv 数据集处理结果概述

- **检查日期**：2026-11-18
- **检查对象**：当前机器上的 DuplexConv 数据、处理结果和可运行 pipeline 目录
- **代码目录**：`/hbt_share02/202610/ob/ob_ppl_env_data/audio_pipeline_optimized/Pipeline_github_v3_Merge/pipeline_runnable_subset_20261111`

## 结论

当前 `Processing_Results/duplexconv` 下有三套 ASR 批处理结果：Qwen3-ASR（ASR1）、FunASR（ASR2）和 FireRedASR（ASR3）。ASR1 和 ASR2 都对全部 VAD 语音 chunk 发起了识别，成功结果覆盖率分别为 99.963% 和 99.951%；剩余 chunk 在失败清单中，主要原因是 `decoder_token_limit`。

当前目录**没有完整的 forced alignment 结果**。已有 ASR 结果只提供识别文本和 chunk 级起止时间；没有发现 `align/forced` 目录或 `.ctm`、`.TextGrid`、`.lab`、`.stm` 等常见词级对齐文件。FireRedASR 结果的元数据也明确记录为只返回 chunk boundaries，不返回 word alignments。

如果“完整”要求每个 chunk 都有成功的文字结果，那么目前没有 100% 完整的 ASR 标注；如果允许保留失败审计记录，ASR1/ASR2 已经覆盖了整个 DuplexConv 的 VAD chunk 集合。

## 数据地址

原始/待处理数据：

```text
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/data_processing/duplexconv
```

该目录当前包含 **209,060 个 WAV 文件**，主要位于以下子目录：

```text
duplexconv/
├── Edu/audios/
├── edu/audios/
└── none_Edu/audios/
```

处理结果根目录：

```text
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv
```

主要阶段目录如下：

| 阶段 | 结果路径 | 用途 |
| --- | --- | --- |
| ingest | `ingest/ingest/batch_results/` | 音频元数据、采样率、时长和源文件指纹 |
| VAD | `vad/vad/batch_results/` | 语音区间和 ASR 输入 chunk |
| enhance | `enhance/enhance/` | 增强音频及其缓存/引用 |
| ASR1 | `asr1/qwen3_asr/batch_results/` | Qwen3-ASR 识别结果 |
| ASR2 | `asr2/funasr/batch_results/` | FunASR 识别结果 |
| disagreement | `disagreement/disagreement/batch_results/` | ASR 差异比较和路由信息 |
| ASR3 | `asr3/fireredasr/batch_results/` | FireRedASR 路由补充结果 |
| ROVER | `rover/` | 多 ASR 结果融合相关输出 |

## 数据规模和覆盖情况

VAD 阶段输出 **209,060 条 recording 结果**，共生成 **2,078,502 个语音 chunk**。ASR 统计按这些 VAD chunk 作为总数核对。

| 阶段 | 成功 transcript | 失败记录 | 已尝试 chunk | 相对全部 VAD chunk 的已尝试覆盖率 | 成功率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| ASR1 / Qwen3-ASR | 2,077,736 | 766 | 2,078,502 | 100.000% | 99.963% |
| ASR2 / FunASR | 2,077,487 | 1,015 | 2,078,502 | 100.000% | 99.951% |
| ASR3 / FireRedASR | 923,500 | 7,969 | 931,469 | 44.814% | 44.431% |

ASR1 和 ASR2 的成功数加失败数都恰好等于 VAD 的 2,078,502 个 chunk，说明两套结果对完整 chunk 集合都完成了处理尝试。ASR3 只处理了部分任务，不能作为完整数据集的独立 ASR 结果。

## ASR1：Qwen3-ASR

成功结果：

```text
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr1/qwen3_asr/batch_results
```

失败审计：

```text
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr1/qwen3_asr/failures.csv
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr1/qwen3_asr/failures.jsonl
```

失败清单有 766 行数据，全部归类为 `decoder_token_limit`。成功 JSON 的每个 `results` 元素通常包含：

```text
text
language
chunk.id
chunk.path
chunk.start / chunk.end
chunk.start_sample / chunk.end_sample
generation
_provenance
```

其中 `chunk.start/end` 是 VAD chunk 的边界，不是词级时间对齐。

## ASR2：FunASR

成功结果：

```text
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr2/funasr/batch_results
```

失败审计：

```text
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr2/funasr/failures.csv
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr2/funasr/failures.jsonl
```

失败清单有 1,015 行数据，全部归类为 `decoder_token_limit`。ASR2 结果同样是 chunk 级文本和音频区间信息，没有词级 alignment 字段。

## ASR3：FireRedASR

结果路径：

```text
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr3/fireredasr/batch_results
```

失败审计：

```text
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr3/fireredasr/failures.csv
/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr3/fireredasr/failures.jsonl
```

ASR3 结果中的 metadata 明确写明：

```text
timestamps: chunk boundaries only; LLM variant does not return word alignments
```

因此 ASR3 也不能当作 forced alignment 结果。

## Forced alignment 检查结果

本次在 `Processing_Results/duplexconv` 下检查了目录名、文件名和常见对齐文件扩展名，没有找到独立的 forced alignment 输出。当前结果中可见的是：

1. VAD 生成的 chunk 区间；
2. ASR 结果中的 chunk `start/end` 和 sample 边界；
3. ASR 文本及模型生成元数据。

这些信息可以支持 chunk 级转写和后续重跑，但不能替代词级 forced alignment。若下游需要词级 `word/start/end`，还需要运行 pipeline 的 `align` 阶段，或对现有最终文本和音频单独运行 Qwen forced aligner/MFA，并把失败清单单独审计。

## 建议使用方式

对于需要尽可能完整的 ASR 文本，优先使用 ASR1 或 ASR2，并保留对应的 `failures.csv` 作为缺失审计。ASR1 和 ASR2 可以按 `chunk.id` 与 VAD 结果关联；失败 chunk 不应被当作空文本静默填充。

查看某个 ASR batch 文件中的示例结果：

```bash
jq '.results[0] | {text, chunk: {id, path, start, end}}' \
  /hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr1/qwen3_asr/batch_results/00/*.json \
  | head -80
```

查看失败类别：

```bash
python - <<'PY'
import csv
from collections import Counter

path = "/hbt_share02/202610/ob/ob_ppl_env_data/dataset/Processing_Results/duplexconv/asr1/qwen3_asr/failures.csv"
with open(path, newline="") as f:
    print(Counter(row["category"] for row in csv.DictReader(f)))
PY
```

