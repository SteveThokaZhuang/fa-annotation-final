"""Mandarin Qwen FA + official Montreal Forced Aligner repair adapter.

The normal V3 alignment adapter uses Qwen for the primary word timestamps and
invokes MFA only for invalid sentences.  This adapter keeps that behavior but
selects the official Mandarin MFA resources and writes Hanzi lab text in the
form expected by ``mandarin_mfa``/``mandarin_china_mfa``.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unicodedata


_BASE_SPEC = spec_from_file_location(
    '_qwen_fa_mfa_base_adapter', Path(__file__).with_name('adapter.py'))
_BASE = module_from_spec(_BASE_SPEC)
assert _BASE_SPEC.loader is not None
_BASE_SPEC.loader.exec_module(_BASE)


def normalize_mandarin_lab(text):
    """Normalize Qwen text for the official Hanzi Mandarin MFA dictionary.

    The official model performs Hanzi-to-pinyin conversion itself.  Spaces and
    punctuation are therefore removed from the lab text; lexical characters,
    including Latin letters and digits in mixed Mandarin transcripts, remain.
    """
    text = unicodedata.normalize('NFKC', str(text))
    return ''.join(
        char for char in text
        if not char.isspace() and not unicodedata.category(char).startswith('P'))


class Adapter(_BASE.Adapter):
    """V3 adapter locked to Chinese Qwen alignment and Mandarin MFA repair."""

    def __init__(self, job):
        language = str(job.get('settings', {}).get('language', 'Chinese'))
        if language.casefold() not in {'chinese', 'zh', 'mandarin'}:
            raise ValueError(
                'Chinese MFA adapter requires settings.language=Chinese, got '
                + language)
        super().__init__(job)
        self.mfa_language = 'Chinese'
        self.mfa_text_normalizer = normalize_mandarin_lab

