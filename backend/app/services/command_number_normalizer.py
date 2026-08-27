from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "○": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}
_ENGLISH_SMALL = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_ENGLISH_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_CHINESE_NUMBER = r"[負负零〇○一二兩两三四五六七八九十百千點点]+"
_ARABIC_NUMBER = r"[-+]?\d+(?:\.\d+)?"
_ENGLISH_NUMBER_WORD = (
    r"(?:minus\s+)?(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)"
    r"(?:[\s-]+(?:and\s+)?(?:zero|one|two|three|four|five|six|seven|eight|nine|"
    r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred))*"
)
_NUMBER_TOKEN = rf"(?:{_ARABIC_NUMBER}|{_CHINESE_NUMBER}|{_ENGLISH_NUMBER_WORD})"


@dataclass(frozen=True, slots=True)
class NormalizedNumber:
    start: int
    end: int
    raw_text: str
    value: float


def normalize_number_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def parse_number(value: object) -> float | None:
    text = normalize_number_text(value)
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text).strip().casefold()
    try:
        numeric = float(compact)
    except ValueError:
        numeric = None
    if numeric is not None:
        return numeric if math.isfinite(numeric) else None

    english = _parse_english_number(compact)
    if english is not None:
        return float(english)
    chinese = _parse_chinese_number(compact)
    return float(chinese) if chinese is not None else None


def find_number_spans(text: str) -> list[NormalizedNumber]:
    normalized = normalize_number_text(text)
    result: list[NormalizedNumber] = []
    for match in re.finditer(_NUMBER_TOKEN, normalized, flags=re.IGNORECASE):
        raw = match.group(0)
        value = parse_number(raw)
        if value is None:
            continue
        result.append(
            NormalizedNumber(
                start=match.start(),
                end=match.end(),
                raw_text=raw,
                value=value,
            )
        )
    return result


def find_percentage(text: str) -> NormalizedNumber | None:
    normalized = normalize_number_text(text)
    patterns = (
        re.compile(
            rf"百分(?:之)?\s*(?P<number>{_NUMBER_TOKEN})",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?P<number>{_NUMBER_TOKEN})\s*(?:%|趴|percent(?:age)?)(?![a-z])",
            re.IGNORECASE,
        ),
    )
    matches: list[NormalizedNumber] = []
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            number_text = match.group("number")
            number = parse_number(number_text)
            if number is None:
                continue
            matches.append(
                NormalizedNumber(
                    start=match.start(),
                    end=match.end(),
                    raw_text=match.group(0),
                    value=number / 100.0,
                )
            )
    if not matches:
        return None
    matches.sort(key=lambda item: (item.start, -(item.end - item.start)))
    return matches[0]


def find_version_references(text: str) -> list[NormalizedNumber]:
    normalized = normalize_number_text(text)
    candidates: list[NormalizedNumber] = []
    patterns = (
        re.compile(
            rf"(?:版本|第|version)\s*(?P<number>{_NUMBER_TOKEN})\s*(?:版)?",
            re.IGNORECASE,
        ),
        re.compile(
            rf"versions?\s+(?P<first>{_NUMBER_TOKEN})\s+"
            rf"(?:and|&)\s+(?P<second>{_NUMBER_TOKEN})",
            re.IGNORECASE,
        ),
    )
    for match in patterns[0].finditer(normalized):
        raw = match.group("number")
        value = parse_number(raw)
        if value is None:
            continue
        candidates.append(
            NormalizedNumber(match.start(), match.end(), match.group(0), value)
        )
    for match in patterns[1].finditer(normalized):
        for group in ("first", "second"):
            raw = match.group(group)
            start, end = match.span(group)
            value = parse_number(raw)
            if value is not None:
                candidates.append(NormalizedNumber(start, end, raw, value))

    result: list[NormalizedNumber] = []
    seen: set[tuple[int, int]] = set()
    for candidate in sorted(candidates, key=lambda item: (item.start, item.end)):
        key = (candidate.start, candidate.end)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _parse_chinese_number(text: str) -> float | None:
    negative = text.startswith(("負", "负"))
    if negative:
        text = text[1:]
    if not text:
        return None
    parts = re.split(r"[點点]", text)
    if len(parts) > 2:
        return None
    integer = _parse_chinese_integer(parts[0])
    if integer is None:
        return None
    value = float(integer)
    if len(parts) == 2:
        if not parts[1] or any(char not in _CHINESE_DIGITS for char in parts[1]):
            return None
        fraction = "".join(str(_CHINESE_DIGITS[char]) for char in parts[1])
        value += int(fraction) / (10 ** len(fraction))
    return -value if negative else value


def _parse_chinese_integer(text: str) -> int | None:
    if text == "":
        return 0
    if all(char in _CHINESE_DIGITS for char in text):
        digits = "".join(str(_CHINESE_DIGITS[char]) for char in text)
        return int(digits)
    total = 0
    current = 0
    saw = False
    for char in text:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
            saw = True
            continue
        unit = _CHINESE_UNITS.get(char)
        if unit is None:
            return None
        saw = True
        total += (current or 1) * unit
        current = 0
    return total + current if saw else None


def _parse_english_number(text: str) -> int | None:
    negative = text.startswith("minus ")
    if negative:
        text = text[6:].strip()
    tokens = [token for token in re.split(r"[\s-]+", text) if token != "and"]
    if not tokens:
        return None
    total = 0
    current = 0
    for token in tokens:
        if token in _ENGLISH_SMALL:
            current += _ENGLISH_SMALL[token]
        elif token in _ENGLISH_TENS:
            current += _ENGLISH_TENS[token]
        elif token == "hundred":
            current = max(1, current) * 100
        else:
            return None
    total += current
    return -total if negative else total


__all__ = [
    "NormalizedNumber",
    "find_number_spans",
    "find_percentage",
    "find_version_references",
    "normalize_number_text",
    "parse_number",
]
