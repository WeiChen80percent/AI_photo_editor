"""Language-agnostic text normalization with source-offset preservation.

The normalizer deliberately handles only text mechanics.  It does not know
about editing parameters, regions, intents, or prompt templates.  Semantic
registries and parsers can therefore reuse it without coupling normalization
to the current feature set.

All offsets in this module are zero-based Unicode code-point offsets.  They
match normal Python string slicing.  ``normalized_to_raw[i]`` is the raw span
that produced normalized code point ``i``.  A span, instead of a single raw
offset, is necessary because Unicode normalization and case folding may
contract or expand text.
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Iterable, Literal, Sequence


MAX_RAW_CODE_POINTS = 2_000


class SemanticNormalizationError(ValueError):
    """A structured, caller-safe normalization failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class RawTextSpan:
    """Half-open source span measured in raw Unicode code points."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("RawTextSpan must satisfy 0 <= start <= end")


@dataclass(frozen=True, slots=True)
class NormalizedToken:
    """A token and its half-open offsets in normalized text."""

    text: str
    start: int
    end: int
    kind: Literal["word", "number", "cjk", "punctuation", "symbol"]

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("NormalizedToken must satisfy 0 <= start < end")


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """Normalized text plus exact provenance for its code points."""

    raw_text: str
    text: str
    tokens: tuple[NormalizedToken, ...]
    normalized_to_raw: tuple[RawTextSpan, ...]

    def __post_init__(self) -> None:
        if len(self.text) != len(self.normalized_to_raw):
            raise ValueError(
                "normalized_to_raw must contain one span per normalized code point"
            )

    @property
    def normalized_text(self) -> str:
        """Explicit alias for callers that prefer a descriptive field name."""

        return self.text

    def restore_span(self, start: int, end: int) -> RawTextSpan:
        """Restore a normalized half-open span to its conservative raw span.

        For a non-empty span, every contributing raw code point is included.
        This remains reliable when one raw character expands to multiple
        normalized characters or a combining sequence contracts to one.

        Empty spans represent insertion points.  At an interior expansion
        boundary there is no unique raw offset, so the closest source boundary
        on the requested side is returned.
        """

        self._validate_normalized_span(start, end)
        if start == end:
            raw_offset = self.raw_offset_for_boundary(start, bias="right")
            return RawTextSpan(raw_offset, raw_offset)

        spans = self.normalized_to_raw[start:end]
        return RawTextSpan(
            min(span.start for span in spans),
            max(span.end for span in spans),
        )

    def restore_text(self, start: int, end: int) -> str:
        """Return the raw evidence text that produced a normalized span."""

        raw_span = self.restore_span(start, end)
        return self.raw_text[raw_span.start : raw_span.end]

    def restore_token_span(self, token: NormalizedToken) -> RawTextSpan:
        """Restore a token while rejecting tokens from another result."""

        if (
            token.start < 0
            or token.end > len(self.text)
            or self.text[token.start : token.end] != token.text
        ):
            raise ValueError("token does not belong to this normalized text")
        return self.restore_span(token.start, token.end)

    def raw_offset_for_boundary(
        self,
        offset: int,
        *,
        bias: Literal["left", "right"] = "right",
    ) -> int:
        """Map a normalized insertion boundary back to a raw offset.

        Boundary mappings can be ambiguous inside an expansion such as one
        source code point case-folding to two code points.  ``bias`` makes that
        ambiguity explicit.  Evidence extraction should normally use
        :meth:`restore_span`, which uses the full source spans instead.
        """

        if not isinstance(offset, int) or isinstance(offset, bool):
            raise TypeError("offset must be an integer")
        if offset < 0 or offset > len(self.text):
            raise ValueError("offset is outside normalized text")
        if bias not in {"left", "right"}:
            raise ValueError("bias must be 'left' or 'right'")
        if not self.normalized_to_raw:
            return 0 if bias == "right" else len(self.raw_text)
        if offset == 0:
            return self.normalized_to_raw[0].start
        if offset == len(self.text):
            return self.normalized_to_raw[-1].end
        if bias == "left":
            return self.normalized_to_raw[offset - 1].end
        return self.normalized_to_raw[offset].start

    def _validate_normalized_span(self, start: int, end: int) -> None:
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
        ):
            raise TypeError("normalized span offsets must be integers")
        if start < 0 or end < start or end > len(self.text):
            raise ValueError(
                "normalized span must satisfy 0 <= start <= end <= text length"
            )


@dataclass(frozen=True, slots=True)
class _MappedChar:
    value: str
    raw_span: RawTextSpan


_SINGLE_APOSTROPHES = frozenset(
    {
        "\u0060",  # grave accent used as an apostrophe
        "\u00b4",  # acute accent used as an apostrophe
        "\u02bc",  # modifier letter apostrophe
        "\u2018",
        "\u2019",
        "\u201a",
        "\u201b",
        "\uff07",
    }
)
_DOUBLE_QUOTES = frozenset(
    {
        "\u201c",
        "\u201d",
        "\u201e",
        "\u201f",
        "\u00ab",
        "\u00bb",
        "\uff02",
    }
)
_DASH_LIKE_SYMBOLS = frozenset({"\u2212"})

# A deliberately small, reviewable orthographic-equivalence layer.  It
# canonicalizes common Simplified/Traditional and variant glyphs without
# performing open-ended machine translation or knowing any edit parameter.
# Every replacement is one code point, so source provenance remains exact.
_CJK_CHARACTER_EQUIVALENTS = {
    "艳": "豔",
    "艷": "豔",
    "鲜": "鮮",
    "对": "對",
    "阴": "陰",
    "饱": "飽",
    "颜": "顏",
    "温": "溫",
    "锐": "銳",
    "雾": "霧",
    "蓝": "藍",
    "黄": "黃",
    "边": "邊",
    "缘": "緣",
    "浓": "濃",
    "软": "軟",
    "细": "細",
    "节": "節",
    "肤": "膚",
    "红": "紅",
    "润": "潤",
    "脏": "髒",
    "调": "調",
    "减": "減",
    "参": "參",
    "数": "數",
    "强": "強",
    "过": "過",
    "后": "後",
    "还": "還",
    "经": "經",
    "点": "點",
    "够": "夠",
    "画": "畫",
    "显": "顯",
    "这": "這",
    "样": "樣",
    "没": "沒",
    "么": "麼",
    "别": "別",
    "开": "開",
    "关": "關",
    "压": "壓",
    "轻": "輕",
    "补": "補",
    "来": "來",
    "个": "個",
    "张": "張",
    "图": "圖",
    "与": "與",
    "并": "並",
    "为": "為",
    "设": "設",
    "归": "歸",
    "较": "較",
    "应": "應",
    "请": "請",
    "帮": "幫",
    "吗": "嗎",
    "无": "無",
    "须": "須",
    "变": "變",
}

# These are ordinary English language contractions, not domain vocabulary.
_IRREGULAR_CONTRACTIONS = {
    "ain't": "is not",
    "can't": "can not",
    "doesnt": "does not",
    "isnt": "is not",
    "let's": "let us",
    "shan't": "shall not",
    "won't": "will not",
}
_CONTRACTION_SUFFIXES = (
    ("'re", "are"),
    ("'ve", "have"),
    ("'ll", "will"),
    ("'d", "would"),
    ("'m", "am"),
)
_S_CONTRACTION_SUBJECTS = frozenset(
    {
        "he",
        "she",
        "it",
        "that",
        "there",
        "here",
        "what",
        "who",
        "where",
        "when",
        "why",
        "how",
        "this",
    }
)


def normalize_semantic_text(
    raw_text: str,
    *,
    max_raw_code_points: int = MAX_RAW_CODE_POINTS,
) -> NormalizedText:
    """Normalize text in linear passes while preserving source provenance.

    ``max_raw_code_points`` and the default limit are measured with
    ``len(raw_text)``: Unicode code points, not UTF-8/UTF-16 bytes.  The
    explicit bound protects all later semantic stages from oversized input.
    """

    if not isinstance(raw_text, str):
        raise SemanticNormalizationError(
            "invalid_text_type",
            "Text to normalize must be a string.",
            details={"received_type": type(raw_text).__name__},
        )
    if (
        not isinstance(max_raw_code_points, int)
        or isinstance(max_raw_code_points, bool)
        or max_raw_code_points < 0
    ):
        raise ValueError("max_raw_code_points must be a non-negative integer")

    raw_length = len(raw_text)
    if raw_length > max_raw_code_points:
        raise SemanticNormalizationError(
            "raw_text_too_long",
            (
                "Text exceeds the normalization limit of "
                f"{max_raw_code_points} Unicode code points."
            ),
            details={
                "raw_code_points": raw_length,
                "max_raw_code_points": max_raw_code_points,
            },
        )

    mapped = _normalize_raw_clusters(raw_text)
    mapped = _canonicalize_punctuation(mapped)
    mapped = _canonicalize_cjk_variants(mapped)
    mapped = _casefold(mapped)
    mapped = _normalize_mapped_clusters(mapped)
    mapped = _collapse_whitespace(mapped)
    mapped = _expand_english_contractions(mapped)

    text = "".join(item.value for item in mapped)
    tokens = _tokenize(text)
    return NormalizedText(
        raw_text=raw_text,
        text=text,
        tokens=tokens,
        normalized_to_raw=tuple(item.raw_span for item in mapped),
    )


def _normalize_raw_clusters(raw_text: str) -> list[_MappedChar]:
    mapped: list[_MappedChar] = []
    index = 0
    while index < len(raw_text):
        start = index
        index += 1
        while index < len(raw_text):
            next_char = raw_text[index]
            if _continues_normalization_cluster(next_char):
                index += 1
                continue
            current_normalized = unicodedata.normalize(
                "NFKC", raw_text[start:index]
            )
            next_normalized = unicodedata.normalize("NFKC", next_char)
            if _hangul_sequences_compose(current_normalized, next_normalized):
                index += 1
                continue
            break
        normalized = unicodedata.normalize("NFKC", raw_text[start:index])
        span = RawTextSpan(start, index)
        mapped.extend(_MappedChar(char, span) for char in normalized)
    return mapped


def _normalize_mapped_clusters(chars: Sequence[_MappedChar]) -> list[_MappedChar]:
    normalized: list[_MappedChar] = []
    index = 0
    while index < len(chars):
        start = index
        index += 1
        while index < len(chars) and _continues_normalization_cluster(
            chars[index].value
        ):
            index += 1
        cluster = chars[start:index]
        value = unicodedata.normalize(
            "NFKC", "".join(item.value for item in cluster)
        )
        span = _union_raw_spans(item.raw_span for item in cluster)
        normalized.extend(_MappedChar(char, span) for char in value)
    return normalized


def _continues_normalization_cluster(char: str) -> bool:
    return unicodedata.category(char).startswith("M")


def _hangul_sequences_compose(current: str, following: str) -> bool:
    """Cover the one canonical-composition case with combining class zero."""

    if not current or not following:
        return False
    current_code_point = ord(current[-1])
    following_code_point = ord(following[0])

    # Standard and extended conjoining Jamo ranges.
    current_is_leading = (
        0x1100 <= current_code_point <= 0x115F
        or 0xA960 <= current_code_point <= 0xA97C
    )
    following_is_vowel = (
        0x1160 <= following_code_point <= 0x11A7
        or 0xD7B0 <= following_code_point <= 0xD7C6
    )
    if current_is_leading and following_is_vowel:
        return True

    syllable_index = current_code_point - 0xAC00
    current_is_lv_syllable = (
        0 <= syllable_index < 11_172 and syllable_index % 28 == 0
    )
    following_is_trailing = (
        0x11A8 <= following_code_point <= 0x11FF
        or 0xD7CB <= following_code_point <= 0xD7FB
    )
    return current_is_lv_syllable and following_is_trailing


def _canonicalize_punctuation(
    chars: Sequence[_MappedChar],
) -> list[_MappedChar]:
    canonical: list[_MappedChar] = []
    for item in chars:
        char = item.value
        if char in _SINGLE_APOSTROPHES:
            char = "'"
        elif char in _DOUBLE_QUOTES:
            char = '"'
        elif unicodedata.category(char) == "Pd" or char in _DASH_LIKE_SYMBOLS:
            char = "-"
        canonical.append(_MappedChar(char, item.raw_span))
    return canonical


def _casefold(chars: Sequence[_MappedChar]) -> list[_MappedChar]:
    folded: list[_MappedChar] = []
    for item in chars:
        folded.extend(
            _MappedChar(char, item.raw_span) for char in item.value.casefold()
        )
    return folded


def _canonicalize_cjk_variants(
    chars: Sequence[_MappedChar],
) -> list[_MappedChar]:
    return [
        _MappedChar(
            _CJK_CHARACTER_EQUIVALENTS.get(item.value, item.value),
            item.raw_span,
        )
        for item in chars
    ]


def _collapse_whitespace(chars: Sequence[_MappedChar]) -> list[_MappedChar]:
    collapsed: list[_MappedChar] = []
    pending_whitespace: list[RawTextSpan] = []
    for item in chars:
        if item.value.isspace():
            if collapsed:
                pending_whitespace.append(item.raw_span)
            continue
        if pending_whitespace:
            collapsed.append(
                _MappedChar(" ", _union_raw_spans(pending_whitespace))
            )
            pending_whitespace.clear()
        collapsed.append(item)
    return collapsed


def _expand_english_contractions(
    chars: Sequence[_MappedChar],
) -> list[_MappedChar]:
    expanded: list[_MappedChar] = []
    index = 0
    while index < len(chars):
        if not _is_ascii_contraction_char(chars[index].value):
            expanded.append(chars[index])
            index += 1
            continue

        end = index + 1
        while end < len(chars) and _is_ascii_contraction_char(chars[end].value):
            end += 1
        word = chars[index:end]
        expanded_word = _expand_contraction_word(word)
        expanded.extend(expanded_word if expanded_word is not None else word)
        index = end
    return expanded


def _is_ascii_contraction_char(char: str) -> bool:
    return char == "'" or ("a" <= char <= "z")


def _expand_contraction_word(
    chars: Sequence[_MappedChar],
) -> list[_MappedChar] | None:
    word = "".join(item.value for item in chars)
    irregular = _IRREGULAR_CONTRACTIONS.get(word)
    if irregular is not None:
        span = _union_raw_spans(item.raw_span for item in chars)
        return [_MappedChar(char, span) for char in irregular]

    if word.endswith("n't") and len(word) > 3:
        stem = list(chars[:-3])
        suffix_span = _union_raw_spans(item.raw_span for item in chars[-3:])
        return stem + [
            _MappedChar(char, suffix_span) for char in " not"
        ]

    for suffix, expansion in _CONTRACTION_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix):
            suffix_start = len(chars) - len(suffix)
            stem = list(chars[:suffix_start])
            suffix_span = _union_raw_spans(
                item.raw_span for item in chars[suffix_start:]
            )
            return stem + [
                _MappedChar(char, suffix_span) for char in f" {expansion}"
            ]

    if word.endswith("'s") and len(word) > 2:
        subject = word[:-2]
        if subject in _S_CONTRACTION_SUBJECTS:
            suffix_span = _union_raw_spans(
                item.raw_span for item in chars[-2:]
            )
            return list(chars[:-2]) + [
                _MappedChar(char, suffix_span) for char in " is"
            ]
    return None


def _tokenize(text: str) -> tuple[NormalizedToken, ...]:
    tokens: list[NormalizedToken] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if _is_cjk(char):
            tokens.append(NormalizedToken(char, index, index + 1, "cjk"))
            index += 1
            continue
        if char.isdecimal():
            end = _consume_number(text, index)
            tokens.append(
                NormalizedToken(text[index:end], index, end, "number")
            )
            index = end
            continue
        if _is_word_start(char):
            end = _consume_word(text, index)
            tokens.append(
                NormalizedToken(text[index:end], index, end, "word")
            )
            index = end
            continue

        category = unicodedata.category(char)
        kind: Literal["punctuation", "symbol"] = (
            "punctuation" if category.startswith("P") else "symbol"
        )
        tokens.append(NormalizedToken(char, index, index + 1, kind))
        index += 1
    return tuple(tokens)


def _consume_number(text: str, start: int) -> int:
    index = start
    seen_decimal_separator = False
    while index < len(text):
        char = text[index]
        if char.isdecimal():
            index += 1
            continue
        if (
            char == "."
            and not seen_decimal_separator
            and index + 1 < len(text)
            and text[index + 1].isdecimal()
        ):
            seen_decimal_separator = True
            index += 1
            continue
        break
    return index


def _consume_word(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        char = text[index]
        if _is_word_continuation(char):
            index += 1
            continue
        if (
            char in {"'", "-"}
            and index + 1 < len(text)
            and _is_word_continuation(text[index - 1])
            and _is_word_continuation(text[index + 1])
        ):
            index += 1
            continue
        break
    return index


def _is_word_start(char: str) -> bool:
    category = unicodedata.category(char)
    return not _is_cjk(char) and (
        category.startswith("L") or char == "_"
    )


def _is_word_continuation(char: str) -> bool:
    category = unicodedata.category(char)
    return not _is_cjk(char) and (
        category.startswith("L")
        or category.startswith("M")
        or category.startswith("N")
        or char == "_"
    )


def _is_cjk(char: str) -> bool:
    code_point = ord(char)
    return (
        0x3400 <= code_point <= 0x4DBF
        or 0x4E00 <= code_point <= 0x9FFF
        or 0xF900 <= code_point <= 0xFAFF
        or 0x20000 <= code_point <= 0x2FA1F
        or 0x3040 <= code_point <= 0x30FF
        or 0xAC00 <= code_point <= 0xD7AF
    )


def _union_raw_spans(spans: Iterable[RawTextSpan]) -> RawTextSpan:
    materialized = tuple(spans)
    if not materialized:
        raise ValueError("cannot union an empty span sequence")
    return RawTextSpan(
        min(span.start for span in materialized),
        max(span.end for span in materialized),
    )


__all__ = [
    "MAX_RAW_CODE_POINTS",
    "NormalizedText",
    "NormalizedToken",
    "RawTextSpan",
    "SemanticNormalizationError",
    "normalize_semantic_text",
]
