"""Map known Chinese backchannels to English in English-labelled ASR chunks."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

_CJK = re.compile(r"[\u2e80-\u9fff\u3040-\u30ff\uac00-\ud7af\u0e00-\u0e7f]")


def load_mapping(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"backchannel mapping must be a JSON object: {path}")
    return value


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    # Whitespace in a phrase is flexible; punctuation next to a phrase is left
    # in place, so "嗯哼，yeah" and "嗯哼, yeah" are both handled safely.
    bits = [re.escape(x) for x in re.split(r"\s+", _normal(phrase)) if x]
    return re.compile(r"\s*".join(bits), re.IGNORECASE)


def _is_cjk(value: str) -> bool:
    return bool(value and _CJK.fullmatch(value))


def map_english_backchannels(text: str, mapping: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Return corrected text and auditable replacement events.

    The caller is responsible for checking that the chunk was labelled English.
    Single-character rules are deliberately conservative: a mapped character
    adjacent to another CJK character is treated as ordinary foreign content,
    while standalone/mixed-English backchannels are mapped.
    """
    value = unicodedata.normalize("NFKC", text or "")
    events: list[dict[str, Any]] = []

    def replace_pattern(pattern: re.Pattern[str], replacement: str, rule: str, source: str) -> None:
        nonlocal value
        before = value
        value, count = pattern.subn(replacement, value)
        if count:
            events.append(dict(rule=rule, source=source, replacement=replacement, count=count,
                               text_before=before, text_after=value))

    exact = mapping.get("exact_phrases", {})
    if isinstance(exact, dict):
        for phrase, spec in sorted(exact.items(), key=lambda item: len(_normal(item[0])), reverse=True):
            if not isinstance(spec, dict) or not isinstance(spec.get("replacement"), str):
                continue
            replace_pattern(_phrase_pattern(phrase), spec["replacement"], "exact_phrase", phrase)

    repetition = mapping.get("repetition_rules", {})
    if isinstance(repetition, dict):
        for char, spec in repetition.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("replacement_unit"), str):
                continue
            minimum = max(2, int(spec.get("minimum_repetitions", 2)))
            unit = spec["replacement_unit"]
            separator = str(spec.get("separator", " "))
            pattern = re.compile(re.escape(char) + "{" + str(minimum) + ",}")
            def repeated(match: re.Match[str], unit: str = unit, separator: str = separator) -> str:
                return separator.join([unit] * len(match.group(0)))
            before = value
            value, count = pattern.subn(repeated, value)
            if count:
                events.append(dict(rule="repetition", source=char, replacement=unit,
                                   count=count, text_before=before, text_after=value))

    singles = mapping.get("single_characters", {})
    if isinstance(singles, dict):
        for char, spec in singles.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("replacement"), str):
                continue
            replacement = spec["replacement"]
            chars_replaced = 0
            out: list[str] = []
            for index, current in enumerate(value):
                if current != char:
                    out.append(current)
                    continue
                # Do not translate a mapped character embedded in a normal CJK
                # word. Punctuation/Latin/whitespace delimiters permit mapping.
                previous = value[index - 1] if index else ""
                following = value[index + 1] if index + 1 < len(value) else ""
                if _is_cjk(previous) or _is_cjk(following):
                    out.append(current)
                else:
                    out.append(replacement)
                    chars_replaced += 1
            if chars_replaced:
                before = value
                value = "".join(out)
                events.append(dict(rule="single_character", source=char,
                                   replacement=replacement, count=chars_replaced,
                                   text_before=before, text_after=value))
    return value, events


def mapping_summary(events: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "replacement_events": len(events),
        "replacement_occurrences": sum(int(event.get("count", 0)) for event in events),
    }
