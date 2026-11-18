# Qwen3 Forced Alignment + MFA

The `source/` directory vendors the upstream implementation from
`FA_awaiting`, with language-adapter hooks for MFA lab text. The default
`final_script/adapter.py` uses English resources; `chinese_adapter.py` selects
Mandarin resources, and `multilingual_adapter.py` selects Japanese, Korean,
Thai, or Vietnamese resources. All adapters convert Rover alignment tasks to
the upstream row format, run Qwen forced alignment and optional MFA sentence
repair, and return the existing V3 `{text, chunk, words, language}` result.

Model checkpoints and MFA runtime assets remain external and are configured by
the pipeline YAML.

For `magicdata_chinese` and `basis-conversations-1500-zh`, V3 selects
`final_script/chinese_adapter.py`, the official `mandarin_china_mfa` dictionary,
and the official `mandarin_mfa` acoustic model. Run
`scripts/setup_mfa_mandarin.py` from a machine with the MFA runtime to install
those resources. Japanese, Korean, Thai, and Vietnamese datasets are selected
from `stage_models.align.mfa_by_language` automatically and use the matching
official MFA dictionary/acoustic model. Install those four pairs with
`scripts/setup_mfa_multilingual.py`. The Japanese adapter reuses Qwen's
`nagisa` word boundaries, while Thai can use MFA's language-aware tokenizer;
the MFA environment must include its normal Thai tokenizer dependency. To
inspect an existing run and re-align script-detected chunks, use
`scripts/scan_multilingual_mfa.py`. When the caller Python has `nagisa` (as
the Qwen ASR environment does), Japanese lab files use that tokenizer while
saved results retain the original transcript.
