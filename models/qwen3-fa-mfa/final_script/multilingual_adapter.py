"""Qwen forced alignment with official MFA resources for Asian languages.

The Qwen aligner already supports Japanese, Korean, Thai, and Vietnamese.  A
language-specific MFA dictionary/acoustic-model pair is needed only for the
sentence repair fallback.  This adapter keeps the normal Qwen/MFA flow and
normalizes the lab text to the orthography expected by the selected MFA model.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re
import unicodedata


_BASE_SPEC = spec_from_file_location(
    '_qwen_fa_mfa_base_adapter', Path(__file__).with_name('adapter.py'))
_BASE = module_from_spec(_BASE_SPEC)
assert _BASE_SPEC.loader is not None
_BASE_SPEC.loader.exec_module(_BASE)


LANGUAGE_ALIASES = {
    'ja': 'Japanese',
    'japanese': 'Japanese',
    'jp': 'Japanese',
    'ko': 'Korean',
    'korean': 'Korean',
    'th': 'Thai',
    'thai': 'Thai',
    'vi': 'Vietnamese',
    'vietnamese': 'Vietnamese',
}
SUPPORTED_LANGUAGES = frozenset(LANGUAGE_ALIASES.values())


def canonical_language(value):
    """Return the Qwen/MFA language name for a common alias."""
    text = str(value or '').strip()
    return LANGUAGE_ALIASES.get(text.casefold(), text)


def _normalize_lab(text, language):
    """Normalize punctuation while retaining orthographic word boundaries.

    MFA's language tokenizer handles Japanese and Thai segmentation when the
    language is supplied in the generated MFA config.  Spaces are therefore
    retained for all four languages; this also preserves Qwen's Vietnamese,
    Korean, and already-segmented Thai word units.
    """
    text = unicodedata.normalize('NFKC', str(text))
    chars = []
    for char in text:
        if char.isspace():
            chars.append(' ')
            continue
        category = unicodedata.category(char)
        if category.startswith('P') or category.startswith('S'):
            continue
        chars.append(char)
    normalized = ''.join(chars)
    if language == 'Chinese':
        normalized = normalized.replace(' ', '')
    return re.sub(r' +', ' ', normalized).strip()


def normalize_japanese_lab(text):
    return _normalize_lab(text, 'Japanese')


def normalize_korean_lab(text):
    return _normalize_lab(text, 'Korean')


def normalize_thai_lab(text):
    return _normalize_lab(text, 'Thai')


def normalize_vietnamese_lab(text):
    return _normalize_lab(text, 'Vietnamese')


_NORMALIZERS = {
    'Japanese': normalize_japanese_lab,
    'Korean': normalize_korean_lab,
    'Thai': normalize_thai_lab,
    'Vietnamese': normalize_vietnamese_lab,
}


class Adapter(_BASE.Adapter):
    """V3 adapter for Japanese, Korean, Thai, or Vietnamese MFA repair."""

    def __init__(self, job):
        language = canonical_language(job.get('settings', {}).get('language'))
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                'Multilingual MFA adapter requires Japanese, Korean, Thai, or '
                f'Vietnamese; got {language or "<empty>"}')
        super().__init__(job)
        self.mfa_language = language
        # Qwen already tokenizes Japanese with nagisa, so the lab text has
        # explicit word boundaries. Avoid requiring Sudachi in the separate
        # MFA runtime and preserve those exact units.
        self.mfa_no_tokenization = language == 'Japanese'
        self.mfa_text_normalizer = _NORMALIZERS[language]

