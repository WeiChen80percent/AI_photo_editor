"""Strict English-to-operation contract for the adaptive prompt pipeline.

This module is intentionally language specific only at the input boundary.  It
does not choose OpenCV values and it does not maintain adaptive state.  Its
only job is to turn supported English into the same language-neutral operation
shape used by the existing controller, or to fail closed with a precise code.

The grammar favors reviewable, high-confidence phrases over guessing.  Unknown
clauses, exclusions, disjunctions, partial numeric expressions, and modified
preset requests are rejected atomically.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Iterable

from app.services.adaptive_policy import ADAPTIVE_AXIS_ORDER, AXIS_POLICIES
from app.services.prompt_text import is_english_prompt, normalize_prompt_text


ENGLISH_PRESET_ALIASES: dict[str, str] = {
    "retro film style": "vintage_film",
    "vintage film look": "vintage_film",
    "old camera look": "vintage_film",
    "cinematic style": "cinematic",
    "cinematic look": "cinematic",
    "movie look": "cinematic",
    "fresh japanese style": "fresh_japanese",
    "japanese style": "fresh_japanese",
    "clean japanese look": "fresh_japanese",
}
ENGLISH_CONTEXT_FEEDBACK_ALIASES = frozenset(
    {
        "too much",
        "that is too much",
        "this is too much",
        "it is too much",
        "that was too much",
        "back off",
        "please back off",
        "back off a little",
        "back off a bit",
        "please back off a little",
        "please back off a bit",
        "back it off",
        "dial it back",
    }
)
MAX_ENGLISH_PROMPT_LENGTH = 2000

_TERMINAL_PUNCTUATION = re.compile(r"[\s.!?,;]+$")
_ASCII_WORD = re.compile(r"[a-z]+(?:-[a-z]+)?", re.IGNORECASE)
_NUMBER = r"(?P<number>[+-]?(?:\d+\.\d+|\d+|\.\d+))"
_WORD_NUMBER = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"hundred|thousand)\b",
    re.IGNORECASE,
)
_SUBTLE_STRENGTH = re.compile(
    r"\b(?:a\s+little\s+bit|a\s+little|a\s+bit|slightly|somewhat|"
    r"gently|subtly)\b",
    re.IGNORECASE,
)
_STRONG_STRENGTH = re.compile(
    r"\b(?:very\s+much|a\s+lot|much|significantly|dramatically|"
    r"strongly|far|way|very|really)\b",
    re.IGNORECASE,
)
_OBSERVATION_MODIFIER = re.compile(
    r"\b(?:a\s+little|a\s+bit|slightly|somewhat|very|really|far|way|strongly)\b|"
    r"\bmuch\b(?=\s+too\b)",
    re.IGNORECASE,
)
_STATE_SUBJECT = (
    r"(?:(?:it|this|that)\s+|"
    r"(?:(?:the|my|this|that|a|an)\s+)?"
    r"(?:image|photo|picture|shot)\s+|"
    r"(?:the\s+)?colou?rs?\s+)"
)
_STATE_LINK = (
    r"(?:is\s+looking|are\s+looking|is|are|looks?|feels?|seems?|appears?)"
)
_STATE_FILLERS = (
    r"(?:(?:a\s+(?:little(?:\s+bit)?|bit)|still|already|currently|"
    r"really|quite|just|now|actually|slightly|somewhat|very|much|far|"
    r"way|rather|absolutely)\s+)*"
)
_STATE_CLAUSE_BOUNDARY = r"(?:^|[,;]\s*|\b(?:and|but|while)\s+)"
_COMPARATIVE_DESCRIPTOR = (
    r"(?:brighter|darker|warmer|cooler|sharper|softer|clearer|"
    r"hazier|foggier|"
    r"(?:more|less)\s+(?:bright|dark|warm|cool|sharp|soft|clear|"
    r"hazy|foggy|saturated|vivid|vibrant|colou?rful|contrasty|"
    r"exposed|dehazed))"
)
_REGION_SUBJECT_TOO_DARK = (
    r"\b(?:the\s+)?(?:sky|person|portrait|background)\s+"
    rf"{_STATE_FILLERS}(?:is|looks?|feels?)\s+"
    rf"{_STATE_FILLERS}too\s+dark\b"
)
_REGION_SUBJECT_TOO_BRIGHT = (
    r"\b(?:the\s+)?(?:sky|person|portrait|background)\s+"
    rf"{_STATE_FILLERS}(?:is|looks?|feels?)\s+"
    rf"{_STATE_FILLERS}too\s+bright\b"
)
_REGION_SUBJECT_BRIGHTNESS_OBSERVATION = re.compile(
    rf"(?:{_REGION_SUBJECT_TOO_DARK}|{_REGION_SUBJECT_TOO_BRIGHT})",
    flags=re.IGNORECASE,
)
_SKY_BRIGHTNESS_OBSERVATION = (
    r"\b(?:the\s+)?sky\s+"
    rf"{_STATE_FILLERS}(?:is|looks?|feels?)\s+"
    rf"{_STATE_FILLERS}too\s+(?:bright|dark)\b"
)
_PERSON_BRIGHTNESS_OBSERVATION = (
    r"\b(?:the\s+)?(?:person|portrait)\s+"
    rf"{_STATE_FILLERS}(?:is|looks?|feels?)\s+"
    rf"{_STATE_FILLERS}too\s+(?:bright|dark)\b"
)
_BACKGROUND_BRIGHTNESS_OBSERVATION = (
    r"\b(?:the\s+)?background\s+"
    rf"{_STATE_FILLERS}(?:is|looks?|feels?)\s+"
    rf"{_STATE_FILLERS}too\s+(?:bright|dark)\b"
)


@dataclass(frozen=True)
class EnglishOperation:
    axis: str
    direction: int
    relation: str
    strength: str
    source_clause: str
    source_marker: str
    source_intent: str
    explicitness: str
    confidence: str = "high"
    numeric_value: float | None = None
    relative_delta: float | None = None
    include_companions: bool = False
    group_feedback: bool = False


@dataclass(frozen=True)
class EnglishPromptAnalysis:
    handled: bool
    kind: str
    operations: tuple[EnglishOperation, ...] = ()
    region: str | None = None
    preset_name: str | None = None
    contextual_all: bool = False


@dataclass
class EnglishPromptContractError(ValueError):
    code: str
    message: str
    issues: tuple[dict[str, Any], ...] = ()
    status_code: int = 422

    def __str__(self) -> str:
        return self.message


_AXIS_LABEL_PATTERNS: dict[str, tuple[str, ...]] = {
    "exposure": (r"exposure(?:\s+value)?",),
    "brightness": (r"brightness",),
    "contrast": (r"contrast",),
    "highlights": (r"highlights?",),
    "shadows": (r"shadows?",),
    "whites": (r"whites?", r"white\s+point"),
    "blacks": (r"blacks?", r"black\s+point"),
    "saturation": (r"saturation",),
    "vibrance": (r"vibrance",),
    "temperature": (
        r"(?:color|colour)\s+temperature",
        r"temperature",
        r"warmth",
    ),
    "white_balance_tint": (
        r"(?:white\s+balance\s+)?tint",
        r"green[\s-]+magenta\s+(?:balance|tint)",
    ),
    "sharpen": (r"sharpening", r"sharpness", r"sharpen"),
    "clarity": (r"clarity",),
    "dehaze": (r"dehaze", r"haze\s+removal"),
    "vignette": (r"vignetting", r"vignette"),
}

_OBSERVATION_PATTERNS: dict[str, tuple[tuple[int, str], ...]] = {
    "exposure": (
        (1, r"\b(?:(?:the\s+)?(?:image|photo|picture|shot))?\s*(?:is|looks?|feels?)?\s*under[\s-]?exposed\b"),
        (1, r"\bnot\s+too\s+under[\s-]?exposed\b"),
        (1, r"\bexposure\s+(?:is|looks?)\s+(?:a\s+bit\s+|slightly\s+)?too\s+low\b"),
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+exposure\b"),
        (1, r"\b(?:there\s+is\s+)?too\s+little\s+exposure\b"),
        (-1, r"\bexposure\s+(?:is|looks?)\s+(?:a\s+bit\s+|slightly\s+)?too\s+high\b"),
        (-1, r"\btoo\s+much\s+exposure\b"),
        (-1, r"\bnot\s+too\s+much\s+exposure\b"),
        (-1, r"\bexposure\s+(?:is|looks?)\s+overdone\b"),
    ),
    "brightness": (
        (1, _REGION_SUBJECT_TOO_DARK),
        (1, r"\b(?:(?:the\s+)?(?:image|photo|picture|shot)|it|this)?\s*(?:is|looks?|feels?)?\s*(?:still\s+)?too\s+dark\b"),
        (1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?)?\s*not\s+bright\s+enough\b"),
        (1, r"\b(?:it|the\s+(?:image|photo|picture))\s+does\s+not\s+look\s+bright\s+enough\b"),
        (1, r"\bbrightness\s+(?:is|looks?)\s+too\s+low\b"),
        (1, r"\bnot\s+too\s+dark\b"),
        (-1, _REGION_SUBJECT_TOO_BRIGHT),
        (-1, r"\b(?:(?:the\s+)?(?:image|photo|picture|shot)|it|this)?\s*(?:is|looks?|feels?)?\s*(?:still\s+)?too\s+bright\b"),
        (-1, r"\bbrightness\s+(?:is|looks?)\s+too\s+high\b"),
        (-1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?)?\s*not\s+too\s+bright\b"),
    ),
    "contrast": (
        (1, r"\b(?:(?:the\s+)?(?:image|photo|picture)|it)?\s*(?:is|looks?|feels?)?\s*too\s+flat\b"),
        (1, r"\bcontrast\s+(?:is|looks?)\s+too\s+low\b"),
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+contrast\b"),
        (1, r"\b(?:there\s+is\s+)?too\s+little\s+contrast\b"),
        (1, r"\bnot\s+too\s+flat\b"),
        (-1, r"\b(?:(?:the\s+)?(?:image|photo|picture)|it)?\s*(?:is|looks?|feels?)?\s*too\s+contrasty\b"),
        (-1, r"\bcontrast\s+(?:is|looks?)\s+too\s+(?:high|strong|heavy)\b"),
        (-1, r"\btoo\s+much\s+contrast\b"),
        (-1, r"\bnot\s+too\s+contrasty\b"),
    ),
    "highlights": (
        (1, r"\bhighlights?\s+(?:are|look|feel)\s+too\s+low\b"),
        (1, r"\bhighlights?\s+(?:are|look|feel)\s+not\s+(?:high|bright)\s+enough\b"),
        (1, r"\bhighlights?\s+(?:need|needs)\s+(?:to\s+be\s+)?lift(?:ed|ing)?\b"),
        (-1, r"\b(?:there\s+is\s+)?not\s+enough\s+highlight\s+detail\b"),
        (-1, r"\bhighlights?\s+(?:are|look|feel)\s+too\s+(?:high|bright|strong)\b"),
        (-1, r"\bhighlights?\s+(?:are|look)\s+(?:blown|clipped|overdone)\b"),
        (-1, r"\b(?:the\s+)?blown\s+highlights?\b"),
    ),
    "shadows": (
        (1, r"\bshadows?\s+(?:are|look|feel)\s+too\s+dark\b"),
        (1, r"\bshadows?\s+(?:are|look)\s+(?:too\s+)?crushed\b"),
        (1, r"\b(?:the\s+)?crushed\s+shadows?\b"),
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+shadow\s+detail\b"),
        (-1, r"\bshadows?\s+(?:are|look|feel)\s+too\s+bright\b"),
        (-1, r"\bshadows?\s+(?:are|look)\s+(?:lifted|opened)\s+too\s+much\b"),
        (-1, r"\bshadows?\s+value\s+(?:is|looks?)\s+too\s+high\b"),
    ),
    "whites": (
        (1, r"\bwhites?\s+(?:are|look|feel)\s+too\s+(?:low|dim|dull)\b"),
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+white\s+level\b"),
        (-1, r"\bwhites?\s+(?:are|look|feel)\s+too\s+(?:high|bright|strong)\b"),
        (-1, r"\bwhites?\s+(?:are|look)\s+(?:blown|clipped|overdone)\b"),
    ),
    "blacks": (
        (1, r"\bblacks?\s+(?:are|look|feel)\s+too\s+(?:low|dark|deep)\b"),
        (1, r"\bblacks?\s+(?:are|look)\s+(?:too\s+)?crushed\b"),
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+black\s+detail\b"),
        (-1, r"\bblacks?\s+(?:are|look|feel)\s+too\s+(?:high|bright|lifted|faded)\b"),
        (-1, r"\bblacks?\s+(?:are|look)\s+lifted\s+too\s+much\b"),
    ),
    "saturation": (
        (1, r"\b(?:(?:the\s+)?(?:colors?|image|photo)|it)?\s*(?:are|is|look|looks|feel|feels)?\s*(?:still\s+)?(?:too\s+)?washed[\s-]+out\b"),
        (1, r"\b(?:the\s+)?colou?rs?\s+(?:are|look|feel)\s+too\s+(?:faded|muted|pale)\b"),
        (1, r"\b(?:it|the\s+(?:image|photo))?\s*(?:is|looks?)?\s*not\s+saturated\s+enough\b"),
        (1, r"\b(?:(?:the\s+)?colou?rs?|it|the\s+(?:image|photo))?\s*(?:are|is|look|looks|feel|feels)?\s*not\s+(?:vivid|vibrant|colou?rful)\s+enough\b"),
        (1, r"\bsaturation\s+(?:is|looks?)\s+too\s+low\b"),
        (-1, r"\bnot\s+too\s+saturated\b"),
        (-1, r"\bnot\s+too\s+(?:vivid|vibrant|colou?rful)\b"),
        (-1, r"\b(?:it|the\s+(?:image|photo))?\s*(?:is|looks?)?\s*(?:still\s+)?too\s+saturated\b"),
        (-1, r"\b(?:(?:the\s+)?colou?rs?|it|the\s+(?:image|photo))?\s*(?:are|is|look|looks|feel|feels)?\s*(?:still\s+)?too\s+(?:vivid|vibrant|colou?rful)\b"),
        (-1, r"\bsaturation\s+(?:is|looks?)\s+too\s+high\b"),
        (-1, r"\b(?:the\s+)?colors?\s+(?:are|look|feel)\s+too\s+(?:intense|strong|heavy)\b"),
    ),
    "vibrance": (
        (1, r"\bvibrance\s+(?:is|looks?)\s+too\s+low\b"),
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+vibrance\b"),
        (-1, r"\bvibrance\s+(?:is|looks?)\s+too\s+(?:high|strong|heavy)\b"),
        (-1, r"\btoo\s+much\s+vibrance\b"),
        (-1, r"\bvibrance\s+(?:is|looks?)\s+overdone\b"),
    ),
    "temperature": (
        (1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?|feels?)?\s*(?:still\s+)?too\s+(?:cool|blue)\b"),
        (1, r"\b(?:color\s+)?temperature\s+(?:is|looks?)\s+too\s+low\b"),
        (1, r"\b(?:it|the\s+(?:image|photo))?\s*(?:is|looks?)?\s*not\s+warm\s+enough\b"),
        (1, r"\bnot\s+too\s+(?:cool|blue)\b"),
        (-1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?|feels?)?\s*(?:still\s+)?too\s+(?:warm|yellow)\b"),
        (-1, r"\b(?:color\s+)?temperature\s+(?:is|looks?)\s+too\s+high\b"),
        (-1, r"\b(?:it|the\s+(?:image|photo))?\s*(?:is|looks?)?\s*not\s+cool\s+enough\b"),
        (-1, r"\bnot\s+too\s+(?:warm|yellow)\b"),
    ),
    "white_balance_tint": (
        (1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?|feels?)?\s*too\s+green\b"),
        (1, r"\b(?:white\s+balance\s+)?tint\s+(?:is|looks?)\s+too\s+low\b"),
        (-1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?|feels?)?\s*too\s+magenta\b"),
        (-1, r"\b(?:white\s+balance\s+)?tint\s+(?:is|looks?)\s+too\s+high\b"),
    ),
    "sharpen": (
        (1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?)?\s*(?:still\s+)?too\s+soft\b"),
        (1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:does\s+not\s+look|is\s+not|looks?\s+not|not)\s*sharp\s+enough\b"),
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+sharpening\b"),
        (1, r"\b(?:sharpen(?:ing)?|sharpness)\s+(?:is|looks?)\s+too\s+low\b"),
        (1, r"\bnot\s+too\s+soft\b"),
        (-1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?)?\s*over[\s-]?sharpened\b"),
        (-1, r"\bsharpening\s+(?:is|looks?)\s+(?:overdone|too\s+(?:high|strong|heavy))\b"),
        (-1, r"\b(?:sharpen(?:ing)?|sharpness)\s+(?:is|looks?)\s+too\s+high\b"),
        (-1, r"\b(?:it|the\s+(?:image|photo))?\s*(?:is|looks?)?\s*too\s+sharp\b"),
        (-1, r"\bnot\s+too\s+sharp\b"),
    ),
    "clarity": (
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+clarity\b"),
        (1, r"\b(?:there\s+is\s+)?too\s+little\s+clarity\b"),
        (1, r"\bnot\s+too\s+little\s+clarity\b"),
        (1, r"\bclarity\s+(?:is|looks?)\s+too\s+low\b"),
        (1, r"\b(?:it|the\s+(?:image|photo))\s+(?:still\s+)?needs\s+more\s+clarity\b"),
        (-1, r"\bclarity\s+(?:is|looks?)\s+(?:overdone|too\s+(?:high|strong|heavy))\b"),
        (-1, r"\btoo\s+much\s+clarity\b"),
        (-1, r"\bnot\s+too\s+much\s+clarity\b"),
        (-1, r"\bclarity\s+effect\s+(?:is|looks?)\s+too\s+strong\b"),
    ),
    "dehaze": (
        (1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?)?\s*(?:still\s+)?(?:too\s+)?hazy\b"),
        (1, r"\b(?:it|the\s+(?:image|photo|picture))?\s*(?:is|looks?)?\s*(?:still\s+)?foggy\b"),
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+dehaze\b"),
        (1, r"\bdehaze\s+(?:is|looks?)\s+too\s+low\b"),
        (1, r"\bnot\s+too\s+(?:hazy|foggy)\b"),
        (-1, r"\b(?:the\s+)?dehaze\s+(?:is|looks?)\s+(?:overdone|too\s+(?:high|strong|heavy))\b"),
        (-1, r"\btoo\s+much\s+dehaze\b"),
        (-1, r"\bnot\s+too\s+much\s+dehaze\b"),
        (-1, r"\bdehaze\s+effect\s+(?:is|looks?)\s+too\s+strong\b"),
    ),
    "vignette": (
        (1, r"\b(?:there\s+is\s+)?not\s+enough\s+vignette\b"),
        (1, r"\b(?:there\s+is\s+)?too\s+little\s+vignette\b"),
        (1, r"\bnot\s+too\s+little\s+vignette\b"),
        (1, r"\b(?:vignette|vignetting)\s+(?:is|looks?)\s+too\s+low\b"),
        (1, r"\bvignette\s+(?:is|looks?)\s+too\s+(?:light|weak|subtle)\b"),
        (1, r"\bvignette\s+(?:needs|need)\s+to\s+be\s+(?:stronger|heavier|darker)\b"),
        (-1, r"\b(?:vignette|vignetting)\s+(?:is|looks?)\s+too\s+high\b"),
        (-1, r"\bvignette\s+(?:is|looks?|feels?)\s+too\s+(?:heavy|strong|dark|deep)\b"),
        (-1, r"\btoo\s+much\s+vignette\b"),
        (-1, r"\bnot\s+too\s+much\s+vignette\b"),
        (-1, r"\bvignette\s+(?:is|looks?)\s+overdone\b"),
    ),
}
_OBSERVATION_SIGNAL = re.compile(
    r"\b(?:too|enough|needs?|under[\s-]?exposed|washed[\s-]+out|"
    r"over[\s-]?sharpened|overdone|blown|clipped|crushed|hazy|foggy)\b",
    re.IGNORECASE,
)

_POSITIVE_VERBS = re.compile(
    r"\b(?:increase|increasing|raise|raising|boost|boosting|enhance|enhancing|"
    r"turn\s+up|bring\s+up|add\s+more)\b",
    re.IGNORECASE,
)
_NEGATIVE_VERBS = re.compile(
    r"\b(?:decrease|decreasing|lower|lowering|reduce|reducing|cut|cutting|"
    r"turn\s+down|dial\s+back|tone\s+down)\b",
    re.IGNORECASE,
)
_ACTION_VERBS = re.compile(
    rf"(?:{_POSITIVE_VERBS.pattern}|{_NEGATIVE_VERBS.pattern})",
    re.IGNORECASE,
)

_ALL_IMAGE_OBJECT = (
    r"(?:everything|(?:the\s+)?(?:whole|entire)\s+(?:image|photo|picture))"
)
_POLITE_COMMAND_PREFIX = (
    r"(?:please\s+|kindly\s+|"
    r"(?:(?:could|can|would|will)\s+you)(?:\s+please)?\s+|"
    r"please\s+(?:(?:could|can|would|will)\s+you)\s+)?"
)

_SPECIAL_ACTIONS: tuple[tuple[str, int, str, bool], ...] = (
    ("brightness", 1, rf"\b(?:make|let|get)\s+{_ALL_IMAGE_OBJECT}\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?brighter\b", True),
    ("brightness", -1, rf"\b(?:make|let|get)\s+{_ALL_IMAGE_OBJECT}\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?darker\b", True),
    ("temperature", 1, rf"\b(?:make|let|get)\s+{_ALL_IMAGE_OBJECT}\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?warmer\b", True),
    ("temperature", -1, rf"\b(?:make|let|get)\s+{_ALL_IMAGE_OBJECT}\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?cooler\b", True),
    ("saturation", 1, rf"\b(?:make|let|get)\s+{_ALL_IMAGE_OBJECT}\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?more\s+saturated\b", True),
    ("saturation", -1, rf"\b(?:make|let|get)\s+{_ALL_IMAGE_OBJECT}\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?less\s+saturated\b", True),
    ("sharpen", 1, rf"\b(?:make|let|get)\s+{_ALL_IMAGE_OBJECT}\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?sharper\b", True),
    ("sharpen", -1, rf"\b(?:make|let|get)\s+{_ALL_IMAGE_OBJECT}\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?less\s+sharp\b", True),
    (
        "brightness",
        1,
        r"\b(?:brighten|lighten)(?:\s+(?:it|the\s+(?:image|photo|picture)|"
        r"my\s+(?:image|photo|picture)|this\s+(?:image|photo|picture)))?\b",
        True,
    ),
    (
        "brightness",
        -1,
        r"\bdarken(?:\s+(?:it|the\s+(?:image|photo|picture)|"
        r"my\s+(?:image|photo|picture)|this\s+(?:image|photo|picture)))?\b",
        True,
    ),
    (
        "temperature",
        1,
        rf"^\s*{_POLITE_COMMAND_PREFIX}warm"
        r"(?:\s+(?:it|(?:the|this|my)\s+"
        r"(?:image|photo|picture)))?(?:\s+up)?\b",
        True,
    ),
    (
        "temperature",
        -1,
        rf"^\s*{_POLITE_COMMAND_PREFIX}cool"
        r"(?:\s+(?:it|(?:the|this|my)\s+"
        r"(?:image|photo|picture)))?(?:\s+down)?\b",
        True,
    ),
    (
        "sharpen",
        1,
        rf"^\s*{_POLITE_COMMAND_PREFIX}sharpen"
        r"(?:\s+(?:it|(?:the|this|my)\s+"
        r"(?:image|photo|picture)))?\b",
        True,
    ),
    (
        "sharpen",
        -1,
        rf"^\s*{_POLITE_COMMAND_PREFIX}soften"
        r"(?:\s+(?:it|(?:the|this|my)\s+"
        r"(?:image|photo|picture)))?\b",
        False,
    ),
    (
        "brightness",
        1,
        r"\b(?:make|let|get)\s+(?:the\s+)?(?:center|middle)"
        r"(?:\s+(?:subject|area))?\s+(?:(?:look|feel)\s+)?"
        r"(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|"
        r"way\s+|very\s+)?brighter\b",
        True,
    ),
    (
        "brightness",
        -1,
        r"\b(?:make|let|get)\s+(?:the\s+)?(?:center|middle)"
        r"(?:\s+(?:subject|area))?\s+(?:(?:look|feel)\s+)?"
        r"(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|"
        r"way\s+|very\s+)?darker\b",
        True,
    ),
    ("brightness", 1, r"\b(?:make|let|get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?brighter\b", True),
    ("brightness", -1, r"\b(?:make|let|get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?darker\b", True),
    ("brightness", 1, r"\b(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|could\s+i\s+get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:to\s+(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?brighter\b", True),
    ("brightness", -1, r"\b(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|could\s+i\s+get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:to\s+(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?darker\b", True),
    ("brightness", 1, r"\b(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+)?brighter\b", True),
    ("brightness", -1, r"\b(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+)?darker\b", True),
    ("temperature", 1, r"\b(?:make|let|get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?warmer\b", True),
    ("temperature", -1, r"\b(?:make|let|get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?cooler\b", True),
    ("temperature", 1, r"\b(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|could\s+i\s+get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:to\s+(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?warmer\b", True),
    ("temperature", -1, r"\b(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|could\s+i\s+get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:to\s+(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?cooler\b", True),
    ("temperature", 1, r"\b(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+)?warmer\b", True),
    ("temperature", -1, r"\b(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+)?cooler\b", True),
    ("temperature", 1, r"\bwarm\s+(?:it|the\s+(?:image|photo|picture))\s+up\b", True),
    ("temperature", -1, r"\bcool\s+(?:it|the\s+(?:image|photo|picture))\s+down\b", True),
    ("saturation", 1, r"\b(?:make|let|get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?more\s+saturated\b", True),
    ("saturation", -1, r"\b(?:make|let|get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?less\s+saturated\b", True),
    ("saturation", 1, r"\b(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|could\s+i\s+get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:to\s+(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?more\s+saturated\b", True),
    ("saturation", -1, r"\b(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|could\s+i\s+get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:to\s+(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?less\s+saturated\b", True),
    ("saturation", 1, r"\bmore\s+saturated\b", True),
    ("saturation", -1, r"\bless\s+saturated\b", True),
    ("saturation", -1, r"\bdesaturate\b", True),
    (
        "white_balance_tint",
        1,
        r"\b(?:shift|move)\s+(?:the\s+)?(?:white\s+balance\s+)?tint\s+"
        r"(?:(?:toward|towards|to)\s+)?magenta\b",
        False,
    ),
    (
        "white_balance_tint",
        -1,
        r"\b(?:shift|move)\s+(?:the\s+)?(?:white\s+balance\s+)?tint\s+"
        r"(?:(?:toward|towards|to)\s+)?green\b",
        False,
    ),
    (
        "saturation",
        1,
        r"\b(?:make|let|get)\s+(?:the\s+)?colou?rs?\s+"
        r"(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|"
        r"much\s+|far\s+|way\s+|very\s+)?(?:more\s+)?"
        r"(?:vivid|vibrant|colou?rful)\b",
        True,
    ),
    (
        "saturation",
        -1,
        r"\b(?:make|let|get)\s+(?:the\s+)?colou?rs?\s+"
        r"(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?"
        r"(?:less\s+(?:vivid|vibrant|colou?rful)|more\s+muted)\b",
        True,
    ),
    (
        "saturation",
        1,
        r"\b(?:make|let|get)\s+(?:it|(?:the|this|my)\s+"
        r"(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?"
        r"(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|"
        r"way\s+|very\s+)?(?:more\s+)?"
        r"(?:vivid|vibrant|colou?rful)\b",
        True,
    ),
    (
        "saturation",
        -1,
        r"\b(?:make|let|get)\s+(?:it|(?:the|this|my)\s+"
        r"(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?"
        r"(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?"
        r"(?:less\s+(?:vivid|vibrant|colou?rful)|more\s+muted)\b",
        True,
    ),
    (
        "saturation",
        1,
        rf"^\s*{_POLITE_COMMAND_PREFIX}(?:a\s+little\s+|a\s+bit\s+|"
        r"slightly\s+|much\s+|far\s+|way\s+|very\s+)?more\s+"
        r"(?:vivid|vibrant|colou?rful)\b",
        True,
    ),
    (
        "saturation",
        -1,
        rf"^\s*{_POLITE_COMMAND_PREFIX}(?:a\s+little\s+|a\s+bit\s+|"
        r"slightly\s+)?less\s+(?:vivid|vibrant|colou?rful)\b",
        True,
    ),
    ("sharpen", 1, r"\bsharpen\s+(?:it|the\s+(?:image|photo|picture))\b", True),
    ("sharpen", 1, r"\b(?:make|let|get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+|very\s+)?sharper\b", True),
    ("sharpen", 1, r"\b(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|could\s+i\s+get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:to\s+(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?sharper\b", True),
    ("sharpen", 1, r"\b(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|way\s+)?sharper\b", True),
    ("sharpen", -1, r"\b(?:make|let|get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?less\s+sharp\b", True),
    ("sharpen", -1, r"\b(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|could\s+i\s+get)\s+(?:it|the\s+(?:image|photo|picture))\s+(?:to\s+(?:look|feel)\s+)?(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?less\s+sharp\b", True),
    ("dehaze", 1, r"\bdehaze\s+(?:it|the\s+(?:image|photo|picture))\b", True),
    ("dehaze", 1, r"\bremove\s+(?:the\s+)?haze\b", True),
    ("vignette", 1, r"\badd\s+(?:a\s+little\s+|a\s+bit\s+|a\s+|some\s+)?vignette\b", False),
    ("vignette", -1, r"\b(?:remove|reduce)\s+(?:the\s+)?vignette\b", False),
)

_AXIS_SPECIFIC_VERBS: dict[str, tuple[tuple[int, str], ...]] = {
    "exposure": (
        (1, r"\b(?:add|bring)\s+(?:the\s+)?exposure\s+up\b"),
        (-1, r"\b(?:pull|dial|bring)\s+(?:the\s+)?exposure\s+(?:back|down)\b"),
    ),
    "contrast": (
        (1, r"\badd\s+(?:a\s+little\s+|a\s+bit\s+|some\s+|more\s+)?contrast\b"),
        (-1, r"\b(?:soften|flatten)\s+(?:the\s+)?contrast\b"),
    ),
    "highlights": (
        (1, r"\b(?:lift|open|bring\s+up)\s+(?:the\s+)?highlights?\b"),
        (-1, r"\b(?:recover|pull\s+back|tone\s+down)\s+(?:the\s+)?highlights?\b"),
    ),
    "shadows": (
        (1, r"\b(?:lift|open|bring\s+up)\s+(?:the\s+)?shadows?\b"),
        (-1, r"\b(?:deepen|crush|close)\s+(?:the\s+)?shadows?\b"),
    ),
    "whites": (
        (1, r"\b(?:lift|raise|brighten|bring\s+up)\s+(?:the\s+)?whites?\b"),
        (-1, r"\b(?:lower|reduce|pull\s+back|tone\s+down)\s+(?:the\s+)?whites?\b"),
    ),
    "blacks": (
        (1, r"\b(?:lift|raise|open|bring\s+up)\s+(?:the\s+)?blacks?\b"),
        (-1, r"\b(?:lower|deepen|crush|bring\s+down)\s+(?:the\s+)?blacks?\b"),
    ),
    "vibrance": (
        (1, r"\b(?:add|boost)\s+(?:a\s+little\s+|a\s+bit\s+|some\s+|more\s+)?vibrance\b"),
        (-1, r"\b(?:reduce|lower|pull\s+back|tone\s+down)\s+(?:the\s+)?vibrance\b"),
    ),
    "white_balance_tint": (
        (1, r"\badd\s+(?:a\s+little\s+|a\s+bit\s+|some\s+)?magenta\s+(?:tint|cast)\b"),
        (-1, r"\badd\s+(?:a\s+little\s+|a\s+bit\s+|some\s+)?green\s+(?:tint|cast)\b"),
    ),
    "clarity": (
        (1, r"\badd\s+(?:a\s+little\s+|a\s+bit\s+|some\s+|more\s+)?clarity\b"),
        (-1, r"\b(?:dial|pull)[\s-]+(?:the\s+)?clarity\s+back\b"),
        (-1, r"\bdial[\s-]+back\s+(?:the\s+)?clarity\b"),
    ),
}

_FULL_IMAGE_NOUN = r"(?:image|photo|picture|shot)"
_EXPLICIT_FULL_IMAGE_CONTEXTS: tuple[
    tuple[re.Pattern[str], bool],
    ...,
] = (
    (
        re.compile(
        rf"\b(?:make|let|get|adjust|brighten|lighten|darken|warm|cool|"
        rf"sharpen|soften|dehaze)\s+(?:the|my|this|that)\s+"
        rf"{_FULL_IMAGE_NOUN}\b",
        flags=re.IGNORECASE,
        ),
        True,
    ),
    (
        re.compile(
        rf"\b(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|"
        rf"could\s+i\s+get)\s+(?:the|my|this|that)\s+"
        rf"{_FULL_IMAGE_NOUN}\b",
        flags=re.IGNORECASE,
        ),
        True,
    ),
    (
        re.compile(
        rf"\b(?:(?:the|my|this|that)\s+)?{_FULL_IMAGE_NOUN}\s+"
        rf"(?=(?:is|are|looks?|feels?|seems?|appears?)\b)",
        flags=re.IGNORECASE,
        ),
        False,
    ),
    (
        re.compile(
            rf"\b{_FULL_IMAGE_NOUN}\s+"
            rf"(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|"
            rf"far\s+|way\s+|very\s+)?(?:{_COMPARATIVE_DESCRIPTOR})\b",
            flags=re.IGNORECASE,
        ),
        True,
    ),
    (
        re.compile(
            rf"\b(?:{_COMPARATIVE_DESCRIPTOR})\s+{_FULL_IMAGE_NOUN}\b",
            flags=re.IGNORECASE,
        ),
        True,
    ),
    (
        re.compile(
            rf"\b{_FULL_IMAGE_NOUN}\b",
            flags=re.IGNORECASE,
        ),
        False,
    ),
)
_NAMED_REGION_BRIGHTNESS_ACTION = (
    r"\b(?:make|let|get)\s+(?:the\s+)?"
    r"(?P<named_region>"
    r"sky|person|portrait|background|center|middle|edges?)"
    r"(?:\s+(?:subject|area))?\s+(?:(?:look|feel)\s+)?"
    r"(?:a\s+little\s+|a\s+bit\s+|slightly\s+|much\s+|far\s+|"
    r"way\s+|very\s+)?(?:brighter|darker)\b"
)
_REGION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "all",
        (
            rf"\b(?:(?:in|on|for)\s+(?:the\s+)?)?whole\s+{_FULL_IMAGE_NOUN}\b",
            rf"\b(?:(?:in|on|for)\s+(?:the\s+)?)?entire\s+{_FULL_IMAGE_NOUN}\b",
            r"\b(?:(?:in|on|for)\s+)?everything\b",
            rf"\b(?:in|on|for|of)\s+(?:the|my|this|that|a|an)\s+"
            rf"{_FULL_IMAGE_NOUN}\b",
            rf"\bof\s+(?:the\s+)?(?:whole|entire)\s+{_FULL_IMAGE_NOUN}\b",
            r"\boverall\b",
        ),
    ),
    ("sky", (r"\b(?:in|on|for)\s+(?:the\s+)?sky\b", r"\b(?:brighten|darken|lighten|adjust)\s+(?:the\s+)?sky\b", r"\bsky\s+only\b", _SKY_BRIGHTNESS_OBSERVATION)),
    ("person", (r"\b(?:in|on|for)\s+(?:the\s+)?(?:person|portrait)\b", r"\b(?:brighten|darken|lighten|adjust)\s+(?:the\s+)?(?:person|portrait)\b", r"\b(?:brighten|darken|lighten|warm|cool|sharpen|soften|desaturate)\s+me\b", r"\b(?:person|portrait)\s+only\b", _PERSON_BRIGHTNESS_OBSERVATION)),
    ("background", (r"\b(?:in|on|for)\s+(?:the\s+)?background\b", r"\b(?:brighten|darken|lighten|adjust)\s+(?:the\s+)?background\b", r"\bbackground\s+only\b", _BACKGROUND_BRIGHTNESS_OBSERVATION)),
    ("highlights", (r"\b(?:in|on|for)\s+(?:the\s+)?highlights?\b", r"\bhighlight\s+areas?\b")),
    ("shadows", (r"\b(?:in|on|for)\s+(?:the\s+)?shadows?\b", r"\bshadow\s+areas?\b")),
    (
        "center",
        (
            r"\b(?:in|on|for)\s+(?:the\s+)?(?:center|middle)\b",
            r"\b(?:center|middle)\s+only\b",
            r"\b(?:the\s+)?(?:center|middle)\s+(?:subject|area)\b",
            r"\b(?:brighten|darken|lighten|adjust)\s+(?:the\s+)?"
            r"(?:center|middle)\b",
        ),
    ),
    (
        "edges",
        (
            r"\b(?:in|on|for|around)\s+(?:the\s+)?edges?\b",
            r"\bedges?\s+only\b",
            r"\b(?:brighten|darken|lighten|adjust)\s+(?:the\s+)?edges?\b",
        ),
    ),
)
_REGION_LIST_ITEM = (
    r"(?:the\s+)?(?:"
    r"(?:whole|entire)\s+(?:image|photo|picture)|"
    r"everything|sky|person|portrait|background|"
    r"highlights?|shadows?|highlight\s+areas?|shadow\s+areas?|"
    r"center|middle|edges?"
    r")"
)
_SHARED_PREPOSITION_REGION_LIST = re.compile(
    rf"\b(?:in|on|for|around)\s+(?:both\s+)?{_REGION_LIST_ITEM}\s*"
    rf"(?:,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)"
    rf"{_REGION_LIST_ITEM}\b",
    flags=re.IGNORECASE,
)
_SINGLE_REGION_SCOPE = re.compile(
    rf"(?:\b(?:in|on|for|around)\s+{_REGION_LIST_ITEM}\b|"
    r"\b(?:in|on|for|of)\s+(?:the|my|this|that|a|an)\s+"
    r"(?:image|photo|picture)\b|"
    r"\b(?:sky|person|portrait|background|highlights?|shadows?|"
    r"center|middle|edges?)\s+only\b|\boverall\b)",
    flags=re.IGNORECASE,
)
_OUTER_REGION_ITEM = (
    r"(?:the\s+)?(?:"
    r"(?:whole|entire)\s+(?:image|photo|picture)|"
    r"sky|person|portrait|background|center|middle|edges?"
    r")"
)
_NESTED_HIGHLIGHT_SHADOW_SCOPE = re.compile(
    rf"\b(?:in|on|for|around)\s+{_OUTER_REGION_ITEM}"
    rf"(?:'s\s+|\s+(?:the\s+)?)(?:highlights?|shadows?)\b",
    flags=re.IGNORECASE,
)
_ACTION_BEARING_REGION = re.compile(
    r"\b(?:brighten|darken|lighten|adjust)\s+(?:the\s+)?"
    r"(?:sky|person|portrait|background|center|middle|edges?)\b|"
    r"\b(?:brighten|darken|lighten|warm|cool|sharpen|soften|desaturate)"
    r"\s+me\b|"
    rf"{_NAMED_REGION_BRIGHTNESS_ACTION}",
    flags=re.IGNORECASE,
)
_ACTION_REGION_SAFE_RESIDUE_WORDS = {
    "a",
    "again",
    "an",
    "bit",
    "can",
    "could",
    "dramatically",
    "far",
    "for",
    "gently",
    "i",
    "image",
    "in",
    "it",
    "just",
    "kindly",
    "little",
    "lot",
    "me",
    "more",
    "much",
    "my",
    "now",
    "of",
    "on",
    "only",
    "overall",
    "photo",
    "picture",
    "please",
    "really",
    "significantly",
    "slightly",
    "somewhat",
    "strongly",
    "subtly",
    "that",
    "the",
    "this",
    "to",
    "very",
    "want",
    "way",
    "will",
    "would",
    "you",
}

_SAFE_RESIDUE_WORDS = {
    "a",
    "again",
    "all",
    "an",
    "and",
    "are",
    "around",
    "at",
    "be",
    "bit",
    "bring",
    "but",
    "by",
    "can",
    "change",
    "could",
    "dramatically",
    "effect",
    "everything",
    "far",
    "feel",
    "feels",
    "for",
    "from",
    "further",
    "gently",
    "get",
    "give",
    "i",
    "image",
    "in",
    "is",
    "it",
    "just",
    "kindly",
    "let",
    "little",
    "lot",
    "look",
    "looking",
    "looks",
    "make",
    "me",
    "much",
    "my",
    "need",
    "needs",
    "now",
    "of",
    "on",
    "only",
    "overall",
    "photo",
    "picture",
    "please",
    "already",
    "appear",
    "appears",
    "prefer",
    "really",
    "seem",
    "seems",
    "shot",
    "significantly",
    "slightly",
    "some",
    "somewhat",
    "still",
    "strongly",
    "subtly",
    "the",
    "that",
    "there",
    "this",
    "to",
    "too",
    "turn",
    "very",
    "want",
    "way",
    "we",
    "while",
    "would",
    "you",
}

_EXCLUSION_PATTERNS = (
    re.compile(r"\bwithout\s+(?:changing|adjusting|increasing|decreasing|raising|lowering|reducing)\b", re.IGNORECASE),
    re.compile(r"\bexcept\b", re.IGNORECASE),
    re.compile(r"\bleave\s+(?:the\s+)?[a-z -]+\s+alone\b", re.IGNORECASE),
    re.compile(r"(?:[,;]|\bbut\b)\s*not\s+(?:the\s+)?[a-z -]+", re.IGNORECASE),
)
_NEGATED_ACTION = re.compile(
    r"\b(?:(?:i\s+)?(?:do|does|did|can|could|would|should|will)\s+not|"
    r"never|no\s+more|avoid)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_DIRECTIONS: tuple[tuple[str, str], ...] = (
    ("dehaze", r"\b(?:add|increase|make|more)\s+(?:the\s+)?(?:haze|fog|haziness)\b"),
    ("sharpen", r"\b(?:blur|make\s+(?:it|the\s+(?:image|photo|picture))\s+blurry)\b"),
    ("vignette", r"\b(?:reverse\s+vignette|brighten\s+(?:the\s+)?corners)\b"),
)
_UNSUPPORTED_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("grain", r"\b(?:add|increase|more)\s+grain\b"),
    ("noise", r"\b(?:add|increase|more)\s+noise\b"),
    ("crop", r"\bcrop\b"),
    ("rotate", r"\brotate\b"),
    ("denoise", r"\b(?:denoise|noise\s+reduction)\b"),
    ("hue", r"\b(?:adjust|change|increase|decrease)\s+(?:the\s+)?hue\b"),
    ("background_removal", r"\bremove\s+(?:the\s+)?background\b"),
)
_DANGLING_ACTION_WORDS = {
    "add",
    "adjust",
    "boost",
    "boosting",
    "brighten",
    "bring",
    "change",
    "close",
    "cool",
    "crush",
    "cut",
    "cutting",
    "darken",
    "decrease",
    "decreasing",
    "deepen",
    "dehaze",
    "desaturate",
    "dial",
    "enhance",
    "enhancing",
    "flatten",
    "get",
    "give",
    "increase",
    "increasing",
    "let",
    "lift",
    "lighten",
    "lower",
    "lowering",
    "make",
    "need",
    "needs",
    "open",
    "prefer",
    "pull",
    "raise",
    "raising",
    "recover",
    "reduce",
    "reducing",
    "remove",
    "reset",
    "restore",
    "set",
    "sharpen",
    "soften",
    "tone",
    "turn",
    "want",
    "warm",
}


def analyze_english_prompt(prompt: str) -> EnglishPromptAnalysis:
    """Compile one English prompt into safe language-neutral operations."""

    original = str(prompt or "").strip()
    if len(original) > MAX_ENGLISH_PROMPT_LENGTH:
        _error(
            "adaptive_prompt_too_long",
            "The prompt is too long to parse safely.",
            source_clause=original[:160],
            reason="prompt_length_limit",
            maximum=MAX_ENGLISH_PROMPT_LENGTH,
        )
    if not is_english_prompt(original):
        return EnglishPromptAnalysis(handled=False, kind="unhandled")

    text = normalize_prompt_text(original)
    if (
        len(original) > MAX_ENGLISH_PROMPT_LENGTH
        or len(text) > MAX_ENGLISH_PROMPT_LENGTH
    ):
        _error(
            "adaptive_prompt_too_long",
            "The English prompt is too long to parse safely.",
            source_clause=original[:160],
            reason="english_prompt_length_limit",
            maximum=MAX_ENGLISH_PROMPT_LENGTH,
        )
    semantic = _TERMINAL_PUNCTUATION.sub("", text)
    if not semantic:
        _error(
            "adaptive_clarification_required",
            "Please specify a supported edit parameter and direction.",
            reason="empty_english_prompt",
        )
    if semantic in {"cool", "warm"}:
        _error(
            "adaptive_clarification_required",
            "A bare temperature word could be feedback or an edit request; please state the intended adjustment.",
            source_clause=original,
            reason="ambiguous_bare_temperature_word",
        )

    preset_name = ENGLISH_PRESET_ALIASES.get(semantic)
    if preset_name is not None:
        return EnglishPromptAnalysis(
            handled=True,
            kind="preset",
            preset_name=preset_name,
        )
    if _has_preset_signal(semantic):
        _error(
            "adaptive_preset_request_unsupported",
            "Only the exact supported preset phrases can be applied; modifiers and negated styles are not ignored.",
            source_clause=original,
            reason="preset_not_exact_allowlist",
        )

    if semantic in {
        "reset all",
        "reset all adjustments",
        "start over",
        "restore the original",
        "return to the original",
    }:
        return EnglishPromptAnalysis(handled=True, kind="global_reset")
    if semantic in {
        "just right",
        "it is just right",
        "it looks just right",
        "good now",
        "this is good now",
        "that looks good now",
    }:
        return EnglishPromptAnalysis(handled=True, kind="satisfied")
    if semantic in ENGLISH_CONTEXT_FEEDBACK_ALIASES:
        return EnglishPromptAnalysis(handled=True, kind="context_feedback")

    _reject_bare_already_comparatives(semantic, original)
    _validate_deictic_there(semantic, original)
    _validate_strength_contract(semantic, original)

    if re.search(r"^\s*(?:and|but|while)\b|\b(?:and|but|while)\s*$", semantic):
        _error(
            "adaptive_clarification_required",
            "A connector is missing one side of the edit request.",
            source_clause=original,
            reason="dangling_connector",
        )
    if re.search(
        r"\b(?:the|a|an|my|in|on|for|to|from|of|around|at|by|"
        r"can|could|would|is|are|be|feel|feels|look|looks|"
        r"seem|seems|looking)\s*$",
        semantic,
        flags=re.IGNORECASE,
    ):
        _error(
            "adaptive_clarification_required",
            "The request ends with an incomplete article or preposition.",
            source_clause=original,
            reason="dangling_function_word",
        )
    _validate_modal_frames(semantic, original)
    if re.search(
        rf"{_STATE_CLAUSE_BOUNDARY}(?:"
        rf"{_STATE_SUBJECT}{_STATE_LINK}\s+{_STATE_FILLERS}|"
        rf"there\s+(?:is|are|seems?\s+to\s+be|appears?\s+to\s+be)\s+"
        rf"{_STATE_FILLERS})not\s+{_STATE_FILLERS}too\b",
        semantic,
        flags=re.IGNORECASE,
    ):
        _error(
            "adaptive_clarification_required",
            "This describes the current image as not excessive; it does not state an edit direction.",
            source_clause=original,
            reason="negated_observation_no_edit",
        )
    if re.search(
        rf"{_STATE_CLAUSE_BOUNDARY}{_STATE_SUBJECT}"
        rf"{_STATE_LINK}\s+{_STATE_FILLERS}"
        rf"{_COMPARATIVE_DESCRIPTOR}\b",
        semantic,
        flags=re.IGNORECASE,
    ):
        _error(
            "adaptive_clarification_required",
            "This is a comparison about the current image, not an edit command.",
            source_clause=original,
            reason="comparative_observation_no_edit",
        )
    axis_state_pattern = "|".join(
        rf"(?:{_axis_pattern(axis)})" for axis in ADAPTIVE_AXIS_ORDER
    )
    state_or_existential_prefix = (
        rf"(?:{_STATE_SUBJECT}{_STATE_LINK}|"
        rf"(?:i|we|he|she|they)\s+(?:am|is|are|was|were)|"
        rf"there\s+(?:is|are|was|were|seems?\s+to\s+be|"
        rf"appears?\s+to\s+be))"
    )
    if re.search(
        rf"{_STATE_CLAUSE_BOUNDARY}{state_or_existential_prefix}\s+"
        rf"{_STATE_FILLERS}(?:more|less)\s+(?:{axis_state_pattern})\b",
        semantic,
        flags=re.IGNORECASE,
    ):
        _error(
            "adaptive_clarification_required",
            "This describes a current parameter comparison rather than requesting another edit.",
            source_clause=original,
            reason="declarative_axis_comparison_no_edit",
        )
    if re.search(
        rf"{_STATE_CLAUSE_BOUNDARY}{state_or_existential_prefix}\s+"
        rf"{_STATE_FILLERS}(?:increasing|raising|boosting|enhancing|"
        rf"decreasing|lowering|reducing|cutting)\s+(?:the\s+)?"
        rf"(?:{axis_state_pattern})\b",
        semantic,
        flags=re.IGNORECASE,
    ):
        _error(
            "adaptive_clarification_required",
            "This describes an edit already in progress rather than requesting another edit.",
            source_clause=original,
            reason="declarative_progressive_no_edit",
        )
    if re.search(
        rf"{_STATE_CLAUSE_BOUNDARY}(?:the\s+)?"
        rf"(?:{axis_state_pattern})\s+"
        rf"(?:(?:is|are)\s+)?{_STATE_FILLERS}"
        rf"(?:increasing|raising|boosting|enhancing|"
        rf"decreasing|lowering|reducing|cutting)\b",
        semantic,
        flags=re.IGNORECASE,
    ):
        _error(
            "adaptive_clarification_required",
            "This axis-first phrase describes a parameter changing rather than clearly requesting another edit.",
            source_clause=original,
            reason="axis_first_progressive_no_edit",
        )
    if re.search(
        rf"{_STATE_CLAUSE_BOUNDARY}(?:the\s+)?"
        rf"(?:{axis_state_pattern})\s+"
        rf"(?:is|are|looks?|feels?|seems?|appears?)\s+"
        rf"{_STATE_FILLERS}(?:higher|lower|stronger|weaker|up|down)\b",
        semantic,
        flags=re.IGNORECASE,
    ):
        _error(
            "adaptive_clarification_required",
            "This compares the current parameter state but does not request another edit.",
            source_clause=original,
            reason="axis_state_observation_no_edit",
        )
    if re.search(r"\b(?:or|either)\b", semantic):
        _error(
            "adaptive_disjunction_not_supported",
            "Alternative edits joined with 'or' are ambiguous; choose one edit.",
            source_clause=original,
            reason="english_disjunction",
        )
    for pattern in _EXCLUSION_PATTERNS:
        if pattern.search(semantic):
            _error(
                "adaptive_exclusion_not_supported",
                "Exclusion-style region or axis guards are not supported in one prompt.",
                source_clause=original,
                reason="english_exclusion_guard",
            )

    if re.search(r"\b(?:darken|make)\s+(?:the\s+)?corners?\b", semantic):
        _error(
            "adaptive_axis_region_ambiguous",
            "Darkening corners may mean vignette, edge masking, or brightness; name the intended parameter.",
            source_clause=original,
            reason="corners_axis_region_ambiguity",
            candidates=["vignette", "edges", "brightness"],
        )
    if re.search(
        r"\b(?:me\s+(?:is\s+)?too\s+(?:bright|dark)|"
        r"too\s+(?:bright|dark)\s+me)\b",
        semantic,
    ):
        _error(
            "adaptive_axis_region_ambiguous",
            "This may describe the person or the whole image; use 'brighten me' or name the region.",
            source_clause=original,
            reason="person_whole_image_ambiguity",
            candidates=["person", "all"],
        )
    if re.search(r"\b(?:brighten|darken)\s+(?:the\s+)?highlights?\b", semantic):
        _error(
            "adaptive_axis_region_ambiguous",
            "This may mean the highlights parameter or brightness in the highlight region.",
            source_clause=original,
            reason="highlights_axis_region_ambiguity",
            candidates=["highlights", "brightness"],
        )
    if re.search(r"\b(?:brighten|darken)\s+(?:the\s+)?shadows?\b", semantic):
        _error(
            "adaptive_axis_region_ambiguous",
            "This may mean the shadows parameter or brightness in the shadow region.",
            source_clause=original,
            reason="shadows_axis_region_ambiguity",
            candidates=["shadows", "brightness"],
        )
    if re.search(r"\boverexpos(?:ed|ure)\b", semantic):
        _error(
            "adaptive_axis_region_ambiguous",
            "Overexposure may refer to exposure, brightness, or highlights; name the intended parameter.",
            source_clause=original,
            reason="overexposure_axis_ambiguity",
            candidates=["exposure", "brightness", "highlights"],
        )

    for axis, pattern in _UNSUPPORTED_DIRECTIONS:
        if re.search(pattern, semantic, flags=re.IGNORECASE):
            _error(
                "adaptive_unsupported_direction",
                f"The requested reverse effect is not supported for {axis}.",
                axis=axis,
                source_clause=original,
                reason="unsupported_direction",
            )
    for operation, pattern in _UNSUPPORTED_OPERATIONS:
        if re.search(pattern, semantic, flags=re.IGNORECASE):
            _error(
                "adaptive_unsupported_operation",
                "This OpenCV adjustment is not supported in the current stage.",
                operation=operation,
                source_clause=original,
                reason="unsupported_edit_operation",
            )

    region, region_spans, contextual_all = _detect_region(
        semantic,
        original,
    )
    _reject_action_bearing_region_compound(semantic, original)
    numeric = _parse_numeric_operations(
        semantic,
        original,
        region_spans=region_spans,
    )
    if numeric is not None:
        if _REGION_SUBJECT_BRIGHTNESS_OBSERVATION.search(semantic) is not None:
            _error(
                "adaptive_operation_conflict",
                "A regional observation cannot be combined with a numeric edit.",
                source_clause=original,
                reason="region_observation_with_numeric_edit",
            )
        _validate_region_scope_contract(
            semantic,
            original,
            region=region,
            region_spans=region_spans,
            observation_spans=(),
            action_spans=(),
            operations=numeric,
        )
        return EnglishPromptAnalysis(
            handled=True,
            kind="adaptive",
            operations=tuple(numeric),
            region=region,
            contextual_all=contextual_all,
        )

    resets, reset_spans = _parse_reset_operations(semantic, original)
    if resets:
        if _REGION_SUBJECT_BRIGHTNESS_OBSERVATION.search(semantic) is not None:
            _error(
                "adaptive_operation_conflict",
                "A regional observation cannot be combined with a reset edit.",
                source_clause=original,
                reason="region_observation_with_reset",
            )
        _validate_region_scope_contract(
            semantic,
            original,
            region=region,
            region_spans=region_spans,
            observation_spans=(),
            action_spans=(),
            operations=resets,
        )
        _validate_operation_set(resets, original)
        _validate_residue(
            semantic,
            [*region_spans, *reset_spans],
            original,
        )
        return EnglishPromptAnalysis(
            handled=True,
            kind="adaptive",
            operations=tuple(resets),
            region=region,
            contextual_all=contextual_all,
        )

    observation_operations, observation_spans = _parse_observations(
        semantic,
        original,
    )
    masked_for_negation = _mask_spans(semantic, observation_spans)
    if _NEGATED_ACTION.search(masked_for_negation):
        _error(
            "adaptive_clarification_required",
            "This sentence only forbids an edit and does not state a supported replacement.",
            source_clause=original,
            reason="negated_action_noop",
        )

    action_operations, action_spans = _parse_actions(
        semantic,
        original,
        region_spans=region_spans,
        observation_claims={
            operation.axis: span
            for operation, span in zip(
                observation_operations,
                observation_spans,
                strict=True,
            )
        },
    )
    unmerged_operations = [
        *observation_operations,
        *action_operations,
    ]
    _validate_region_scope_contract(
        semantic,
        original,
        region=region,
        region_spans=region_spans,
        observation_spans=observation_spans,
        action_spans=action_spans,
        operations=unmerged_operations,
    )
    operations = _merge_reinforcing_observation_actions(
        unmerged_operations,
    )
    _validate_operation_set(operations, original)
    if not operations:
        _error(
            "adaptive_clarification_required",
            "No supported English edit operation could be identified safely.",
            source_clause=original,
            reason="no_supported_operation",
        )

    consumed_spans = [*region_spans, *observation_spans, *action_spans]
    _validate_residue(semantic, consumed_spans, original)
    return EnglishPromptAnalysis(
        handled=True,
        kind="adaptive",
        operations=tuple(operations),
        region=region,
        contextual_all=contextual_all,
    )


def detect_exact_english_preset(prompt: str) -> str | None:
    """Return an allowlisted English preset only for an exact surface phrase."""

    if not is_english_prompt(prompt):
        return None
    text = _TERMINAL_PUNCTUATION.sub("", normalize_prompt_text(prompt))
    return ENGLISH_PRESET_ALIASES.get(text)


def _parse_numeric_operations(
    text: str,
    original: str,
    *,
    region_spans: Iterable[tuple[int, int]],
) -> list[EnglishOperation] | None:
    has_digit = re.search(r"\d", text) is not None
    # A number word is numeric only in a numeric-value frame. This avoids
    # misclassifying ordinary continuation phrases such as "the last one".
    has_word_number = re.search(
        r"\b(?:to|by|at|plus|minus)\s+"
        r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"hundred|thousand)\b",
        text,
        flags=re.IGNORECASE,
    ) is not None
    has_non_finite = re.search(r"\b(?:nan|inf|infinity)\b", text) is not None
    if not (has_digit or has_word_number or has_non_finite):
        return None

    if re.search(r"\d(?:\.\d+)?\s*%", text):
        _error(
            "adaptive_unsupported_numeric_unit",
            "Percent values are ambiguous across edit parameters; use the schema unit.",
            source_clause=original,
            reason="percentage_not_supported",
        )
    if re.search(r"(?:\d+(?:\.\d+)?|\.\d+)[eE][+-]?\d+", text):
        _error(
            "adaptive_invalid_numeric",
            "Scientific notation is not accepted for edit parameters.",
            source_clause=original,
            reason="scientific_notation_not_supported",
        )
    if re.search(r"\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?", text):
        _error(
            "adaptive_invalid_numeric",
            "Fractions are not accepted for edit parameters.",
            source_clause=original,
            reason="fraction_not_supported",
        )
    if has_word_number or has_non_finite:
        _error(
            "adaptive_invalid_numeric",
            "Use one finite decimal number for the requested parameter.",
            source_clause=original,
            reason="non_decimal_numeric",
        )

    operations: list[EnglishOperation] = []
    consumed: list[tuple[int, int]] = []
    all_scope = (
        r"(?:\s+of\s+(?:(?:the|my|this|that|a|an)\s+(?:image|photo|picture)|"
        r"(?:the\s+)?(?:whole|entire)\s+(?:image|photo|picture)))?"
    )
    for axis in ADAPTIVE_AXIS_ORDER:
        label = _axis_pattern(axis)
        absolute_patterns = (
            re.compile(
                rf"\b(?:set|adjust)\s+(?:the\s+)?(?:{label}){all_scope}\s+"
                rf"(?:to|at)\s+{_NUMBER}\b",
                re.IGNORECASE,
            ),
            re.compile(
                rf"\b(?:the\s+)?(?:{label}){all_scope}\s+"
                rf"(?:to|at)\s+{_NUMBER}\b",
                re.IGNORECASE,
            ),
            re.compile(
                rf"\b(?:{label}){all_scope}\s*(?:=|:)\s*{_NUMBER}\b",
                re.IGNORECASE,
            ),
        )
        relative_patterns = (
            (
                1,
                re.compile(
                    rf"\b(?:increase|raise|add)\s+(?:the\s+)?(?:{label})"
                    rf"{all_scope}\s+by\s+{_NUMBER}\b",
                    re.IGNORECASE,
                ),
            ),
            (
                -1,
                re.compile(
                    rf"\b(?:decrease|lower|reduce|subtract)\s+(?:the\s+)?"
                    rf"(?:{label}){all_scope}\s+by\s+{_NUMBER}\b",
                    re.IGNORECASE,
                ),
            ),
            (
                1,
                re.compile(
                    rf"\b(?:{label}){all_scope}\s+"
                    rf"(?:increase|raise|go\s+up)\s+by\s+{_NUMBER}\b",
                    re.IGNORECASE,
                ),
            ),
            (
                -1,
                re.compile(
                    rf"\b(?:{label}){all_scope}\s+"
                    rf"(?:decrease|lower|go\s+down)\s+by\s+{_NUMBER}\b",
                    re.IGNORECASE,
                ),
            ),
        )
        for pattern in absolute_patterns:
            match = pattern.search(text)
            if match is None:
                continue
            value = _finite_decimal(match.group("number"), original)
            operations.append(
                _numeric_operation(
                    axis=axis,
                    relation="absolute",
                    direction=0,
                    value=value,
                    original=original,
                    marker=match.group(0),
                )
            )
            consumed.append(match.span())
            break
        for direction, pattern in relative_patterns:
            match = pattern.search(text)
            if match is None:
                continue
            value = _finite_decimal(match.group("number"), original)
            if value < 0:
                _error(
                    "adaptive_invalid_numeric",
                    "A direction verb cannot be combined with a negative number.",
                    axis=axis,
                    source_clause=original,
                    reason="signed_relative_direction_conflict",
                )
            operations.append(
                _numeric_operation(
                    axis=axis,
                    relation="relative_numeric",
                    direction=direction,
                    value=direction * value,
                    original=original,
                    marker=match.group(0),
                )
            )
            consumed.append(match.span())

    if not operations:
        _error(
            "adaptive_invalid_numeric",
            "The number was not attached to a complete supported numeric edit.",
            source_clause=original,
            reason="unconsumed_numeric",
        )
    _validate_operation_set(operations, original)
    masked = _mask_spans(text, consumed)
    if re.search(r"\d", masked):
        _error(
            "adaptive_invalid_numeric",
            "One or more numeric values were not consumed safely.",
            source_clause=original,
            reason="partially_consumed_numeric",
        )
    if _explicit_strength(masked) is not None:
        _error(
            "adaptive_invalid_numeric",
            "A numeric edit cannot also carry a vague strength modifier.",
            source_clause=original,
            reason="numeric_with_strength_modifier",
        )
    _validate_residue(text, [*region_spans, *consumed], original)
    return operations


def _parse_reset_operations(
    text: str,
    original: str,
) -> tuple[list[EnglishOperation], list[tuple[int, int]]]:
    if re.search(
        r"\b(?:reset|restore)\b|\bback\s+to\s+(?:default|neutral)\b",
        text,
        flags=re.IGNORECASE,
    ) is None:
        return [], []

    operations: list[EnglishOperation] = []
    spans: list[tuple[int, int]] = []
    for axis in ADAPTIVE_AXIS_ORDER:
        label = _axis_pattern(axis)
        patterns = (
            re.compile(rf"\breset\s+(?:the\s+)?(?:{label})\b", re.IGNORECASE),
            re.compile(
                rf"\brestore\s+(?:the\s+)?(?:{label})\s+to\s+(?:default|neutral)\b",
                re.IGNORECASE,
            ),
            re.compile(
                rf"\bset\s+(?:the\s+)?(?:{label})\s+back\s+to\s+(?:default|neutral)\b",
                re.IGNORECASE,
            ),
        )
        match = next(
            (
                candidate
                for pattern in patterns
                if (candidate := pattern.search(text)) is not None
            ),
            None,
        )
        if match is None:
            continue
        operations.append(
            EnglishOperation(
                axis=axis,
                direction=0,
                relation="reset",
                strength="subtle",
                source_clause=original,
                source_marker=match.group(0),
                source_intent="axis_reset",
                explicitness="explicit_axis",
            )
        )
        spans.append(match.span())
    if operations and _ACTION_VERBS.search(text):
        _error(
            "adaptive_operation_conflict",
            "Reset and directional edits must be submitted separately.",
            source_clause=original,
            reason="reset_with_operation",
        )
    if operations and _explicit_strength(
        _mask_spans(text, spans)
    ) is not None:
        _error(
            "adaptive_clarification_required",
            "Reset does not accept a partial strength modifier.",
            source_clause=original,
            reason="reset_with_strength_modifier",
        )
    return operations, spans


def _parse_observations(
    text: str,
    original: str,
) -> tuple[list[EnglishOperation], list[tuple[int, int]]]:
    if _OBSERVATION_SIGNAL.search(text) is None:
        return [], []

    # Match the semantic core on a same-length view with natural strength
    # modifiers blanked out.  Keeping the length unchanged preserves source
    # spans for residue validation and provenance.
    match_text = _mask_regex_matches(text, _OBSERVATION_MODIFIER)
    candidates: list[tuple[str, int, int, int, bool]] = []
    seen_candidates: set[tuple[str, int, int, int]] = set()
    for axis in ADAPTIVE_AXIS_ORDER:
        scoped_axis_observation = re.compile(
            rf"\b(?:the\s+)?(?:{_axis_pattern(axis)})\s+of\s+"
            r"(?:(?:the|my|this|that|a|an)\s+(?:image|photo|picture)|"
            r"(?:the\s+)?(?:whole|entire)\s+(?:image|photo|picture))\s+"
            r"(?:is|are|looks?|feels?|seems?|appears?)\s+too\s+"
            r"(?P<level>low|high)\b",
            flags=re.IGNORECASE,
        )
        for match in scoped_axis_observation.finditer(match_text):
            direction = 1 if match.group("level").casefold() == "low" else -1
            key = (axis, direction, match.start(), match.end())
            seen_candidates.add(key)
            candidates.append(
                (
                    axis,
                    direction,
                    match.start(),
                    match.end(),
                    True,
                )
            )
        for direction, pattern in _OBSERVATION_PATTERNS[axis]:
            for match in re.finditer(pattern, match_text, flags=re.IGNORECASE):
                key = (axis, direction, match.start(), match.end())
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                marker = text[match.start() : match.end()]
                explicit_axis = (
                    re.search(
                        rf"\b(?:{_axis_pattern(axis)})\b",
                        marker,
                        flags=re.IGNORECASE,
                    )
                    is not None
                )
                candidates.append(
                    (
                        axis,
                        direction,
                        match.start(),
                        match.end(),
                        explicit_axis,
                    )
                )
    _reject_elliptical_observation_scope(text, candidates, original)

    # A named-axis observation owns its clause before a generic whole-image
    # descriptor.  For example, "the highlights are too bright" must not also
    # create a brightness operation merely because "too bright" appears.
    selected: list[tuple[str, int, int, int, bool]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            0 if item[4] else 1,
            -(item[3] - item[2]),
            item[2],
            ADAPTIVE_AXIS_ORDER.index(item[0]),
        ),
    ):
        _, _, start, end, _ = candidate
        if any(start < kept_end and kept_start < end for _, _, kept_start, kept_end, _ in selected):
            continue
        selected.append(candidate)

    operations: list[EnglishOperation] = []
    spans: list[tuple[int, int]] = []
    for axis, direction, start, end, explicit_axis in sorted(
        selected,
        key=lambda item: (item[2], ADAPTIVE_AXIS_ORDER.index(item[0])),
    ):
        marker = text[start:end]
        operations.append(
            EnglishOperation(
                axis=axis,
                direction=direction,
                relation="correct",
                strength=_feedback_strength(text, start),
                source_clause=original,
                source_marker=marker,
                source_intent=_intent(axis, direction),
                explicitness="feedback",
                confidence="high" if explicit_axis else "medium",
            )
        )
        spans.append((start, end))
    return operations, spans


def _parse_actions(
    text: str,
    original: str,
    *,
    region_spans: Iterable[tuple[int, int]],
    observation_claims: dict[str, tuple[int, int]],
) -> tuple[list[EnglishOperation], list[tuple[int, int]]]:
    operations: list[EnglishOperation] = []
    spans: list[tuple[int, int]] = []
    axis_region_spans = [
        span
        for span in region_spans
        if re.search(
            r"\b(?:highlight|shadow)\s+areas?\b",
            text[span[0]:span[1]],
            flags=re.IGNORECASE,
        )
    ]
    axis_region_spans.extend(
        match.span()
        for match in re.finditer(
            r"\b(?:in|on|for)\s+(?:the\s+)?(?:highlights?|shadows?)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    masked_regions = _mask_spans(text, axis_region_spans)
    segments = _segments(masked_regions)
    shared_trailing_strength = _shared_coordination_trailing_strength(
        masked_regions,
        segments=segments,
    )
    inherited_direction: int | None = None
    inherited_strength: str | None = None
    inherited_direction_is_shareable = False
    for segment, offset, connector in segments:
        if not segment.strip():
            continue
        segment_operations: list[EnglishOperation] = []
        segment_spans: list[tuple[int, int]] = []
        direction_match = _first_direction_verb(segment)
        direction = direction_match[0] if direction_match is not None else None
        explicit_strength = _explicit_strength(segment)
        strength = (
            explicit_strength
            or shared_trailing_strength
            or (
                inherited_strength
                if direction_match is None
                and connector in {"and", ","}
                and inherited_strength is not None
                else None
            )
            or "normal"
        )
        axis_matches = _axis_matches(segment)
        if direction is not None and axis_matches:
            for axis, start, end, marker in axis_matches:
                if _overlaps_claim(
                    axis,
                    (offset + start, offset + end),
                    observation_claims,
                ):
                    continue
                segment_operations.append(
                    EnglishOperation(
                        axis=axis,
                        direction=direction,
                        relation="initial",
                        strength=strength,
                        source_clause=original,
                        source_marker=marker,
                        source_intent=_intent(axis, direction),
                        explicitness="explicit_axis",
                    )
                )
                segment_spans.append((start, end))
            if direction_match is not None and segment_operations:
                segment_spans.append(direction_match[1])
            inherited_direction = direction
            inherited_strength = strength
            inherited_direction_is_shareable = True

        for axis in ADAPTIVE_AXIS_ORDER:
            label = _axis_pattern(axis)
            suffix_patterns = (
                (
                    1,
                    re.compile(
                        rf"\b(?:turn|bring)\s+(?:the\s+)?(?:{label})\s+"
                        rf"(?:up|higher)\b",
                        re.IGNORECASE,
                    ),
                ),
                (
                    -1,
                    re.compile(
                        rf"\b(?:turn|bring)\s+(?:the\s+)?(?:{label})\s+"
                        rf"(?:down|lower)\b",
                        re.IGNORECASE,
                    ),
                ),
                (
                    1,
                    re.compile(
                        rf"\b(?:give\s+(?:it|the\s+(?:image|photo|picture))\s+|"
                        rf"(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|"
                        rf"could\s+i\s+get)\s+)"
                        rf"(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?"
                        rf"more\s+(?:{label})\b",
                        re.IGNORECASE,
                    ),
                ),
                (
                    -1,
                    re.compile(
                        rf"\b(?:give\s+(?:it|the\s+(?:image|photo|picture))\s+|"
                        rf"(?:i\s+(?:want|need|prefer|would\s+(?:like|prefer))|"
                        rf"could\s+i\s+get)\s+)"
                        rf"(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?"
                        rf"less\s+(?:{label})\b",
                        re.IGNORECASE,
                    ),
                ),
                (
                    1,
                    re.compile(
                        rf"\b(?:{label})(?:\s+value)?\s+"
                        rf"(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?"
                        rf"(?:up|higher|more)\b",
                        re.IGNORECASE,
                    ),
                ),
                (
                    -1,
                    re.compile(
                        rf"\b(?:{label})(?:\s+value)?\s+"
                        rf"(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?"
                        rf"(?:down|lower|less)\b",
                        re.IGNORECASE,
                    ),
                ),
                (
                    1,
                    re.compile(
                        rf"\bmore\s+(?:{label})\b",
                        re.IGNORECASE,
                    ),
                ),
                (
                    -1,
                    re.compile(
                        rf"\bless\s+(?:{label})\b",
                        re.IGNORECASE,
                    ),
                ),
            )
            match_item = next(
                (
                    (action_direction, pattern.search(segment))
                    for action_direction, pattern in suffix_patterns
                    if pattern.search(segment) is not None
                ),
                None,
            )
            if match_item is None:
                continue
            action_direction, match = match_item
            assert match is not None
            covering_specific_match = next(
                (
                    specific_match
                    for specific_direction, specific_pattern in (
                        _AXIS_SPECIFIC_VERBS.get(axis) or ()
                    )
                    if specific_direction == action_direction
                    and (
                        specific_match := re.search(
                            specific_pattern,
                            segment,
                            flags=re.IGNORECASE,
                        )
                    )
                    is not None
                    and specific_match.start() <= match.start()
                    and match.end() <= specific_match.end()
                ),
                None,
            )
            effective_match = covering_specific_match or match
            if _overlaps_claim(
                axis,
                (
                    offset + effective_match.start(),
                    offset + effective_match.end(),
                ),
                observation_claims,
            ):
                continue
            existing_axis_operations = [
                item for item in segment_operations if item.axis == axis
            ]
            if existing_axis_operations:
                segment_spans.append(effective_match.span())
                if all(
                    item.direction == action_direction
                    for item in existing_axis_operations
                ):
                    continue
            segment_operations.append(
                EnglishOperation(
                    axis=axis,
                    direction=action_direction,
                    relation="initial",
                    strength=strength,
                    source_clause=original,
                    source_marker=effective_match.group(0),
                    source_intent=_intent(axis, action_direction),
                    explicitness="explicit_axis",
                )
            )
            segment_spans.append(effective_match.span())
            inherited_direction = action_direction
            inherited_strength = strength
            inherited_direction_is_shareable = False

        for axis, action_direction, pattern, include_companions in _SPECIAL_ACTIONS:
            if any(item.axis == axis for item in segment_operations):
                continue
            inherited_axes = axis_matches
            if (
                connector in {"and", ","}
                and inherited_direction is not None
                and inherited_direction_is_shareable
                and inherited_direction == action_direction
                and any(item[0] == axis for item in inherited_axes)
                and _is_bare_shared_axis_segment(segment, inherited_axes)
            ):
                # A bare named axis after a shared verb is an explicit axis,
                # not a standalone macro command.  For example,
                # "increase exposure and sharpen" must not add clarity.
                continue
            match = re.search(pattern, segment, flags=re.IGNORECASE)
            if match is None:
                continue
            shadowed_by_specific_axis = any(
                specific_axis != axis
                and (
                    specific_match := re.search(
                        specific_pattern,
                        segment,
                        flags=re.IGNORECASE,
                    )
                )
                is not None
                and specific_match.start() < match.end()
                and match.start() < specific_match.end()
                for specific_axis, specific_patterns in _AXIS_SPECIFIC_VERBS.items()
                for _, specific_pattern in specific_patterns
            )
            if shadowed_by_specific_axis:
                # Prefer the longer named-axis command over an overlapping
                # bare whole-image macro.  "Soften contrast" is contrast-only;
                # the leading "soften" must not also add a sharpen operation.
                continue
            if _overlaps_claim(
                axis,
                (offset + match.start(), offset + match.end()),
                observation_claims,
            ):
                continue
            segment_operations.append(
                EnglishOperation(
                    axis=axis,
                    direction=action_direction,
                    relation="initial",
                    strength=strength,
                    source_clause=original,
                    source_marker=match.group(0),
                    source_intent=_intent(axis, action_direction),
                    explicitness="macro_primary" if include_companions else "explicit_axis",
                    include_companions=include_companions,
                )
            )
            segment_spans.append(match.span())
            inherited_direction = action_direction
            inherited_strength = strength
            inherited_direction_is_shareable = False

        for axis, patterns in _AXIS_SPECIFIC_VERBS.items():
            if any(item.axis == axis for item in segment_operations):
                continue
            for action_direction, pattern in patterns:
                match = re.search(pattern, segment, flags=re.IGNORECASE)
                if match is None:
                    continue
                if _overlaps_claim(
                    axis,
                    (offset + match.start(), offset + match.end()),
                    observation_claims,
                ):
                    continue
                segment_operations.append(
                    EnglishOperation(
                        axis=axis,
                        direction=action_direction,
                        relation="initial",
                        strength=strength,
                        source_clause=original,
                        source_marker=match.group(0),
                        source_intent=_intent(axis, action_direction),
                        explicitness="explicit_axis",
                    )
                )
                segment_spans.append(match.span())
                inherited_direction = action_direction
                inherited_strength = strength
                inherited_direction_is_shareable = False
                break

        if (
            not segment_operations
            and connector in {"and", ","}
            and inherited_direction is not None
            and inherited_direction_is_shareable
        ):
            bare_axes = axis_matches
            if bare_axes and _is_bare_shared_axis_segment(segment, bare_axes):
                for axis, start, end, marker in bare_axes:
                    segment_operations.append(
                        EnglishOperation(
                            axis=axis,
                            direction=inherited_direction,
                            relation="initial",
                            strength=strength,
                            source_clause=original,
                            source_marker=marker,
                            source_intent=_intent(axis, inherited_direction),
                            # The verb is shared by surface coordination, but
                            # every named axis is still an explicit user
                            # request.  Keep the parser provenance in the
                            # source marker while matching the controller
                            # precedence of the equivalent Chinese compound.
                            explicitness="explicit_axis",
                        )
                    )
                    segment_spans.append((start, end))
                inherited_strength = strength

        operations.extend(segment_operations)
        spans.extend((offset + start, offset + end) for start, end in segment_spans)
    return operations, spans


def _reject_elliptical_observation_scope(
    text: str,
    candidates: list[tuple[str, int, int, int, bool]],
    original: str,
) -> None:
    explicit_candidates = [item for item in candidates if item[4]]
    if not explicit_candidates:
        return
    for _, _, start, end, explicit_axis in candidates:
        if explicit_axis:
            continue
        for segment, offset, connector in _segments(text):
            segment_end = offset + len(segment)
            if not (start < segment_end and offset < end):
                continue
            if connector not in {"and", "but", "while", ","}:
                break
            has_subject = re.search(
                r"\b(?:it|this|image|photo|picture|shot|colors?)\b",
                segment,
                flags=re.IGNORECASE,
            )
            has_explicit_axis = bool(_axis_matches(segment))
            has_prior_named_observation = any(
                item_end <= offset for _, _, _, item_end, _ in explicit_candidates
            )
            if (
                has_subject is None
                and not has_explicit_axis
                and has_prior_named_observation
            ):
                _error(
                    "adaptive_clarification_required",
                    "An elliptical observation could refer to the previous axis or the whole image.",
                    source_clause=original,
                    reason="elliptical_observation_scope",
                )
            break


def _detect_region(
    text: str,
    original: str,
) -> tuple[str | None, list[tuple[int, int]], bool]:
    nested_scope = _NESTED_HIGHLIGHT_SHADOW_SCOPE.search(text)
    if nested_scope is not None:
        _error(
            "adaptive_multi_region_not_supported",
            "Nested English region masks are not supported in one prompt.",
            source_clause=original,
            source_marker=nested_scope.group(0),
            reason="nested_region_not_supported",
        )

    shared_region_list = _SHARED_PREPOSITION_REGION_LIST.search(text)
    if shared_region_list is not None:
        _error(
            "adaptive_multi_region_not_supported",
            "One English prompt can edit only one region.",
            source_clause=original,
            source_marker=shared_region_list.group(0),
            reason="shared_preposition_region_list",
        )

    found: list[tuple[str, tuple[int, int], str]] = []
    for region, patterns in _REGION_PATTERNS:
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                found.append((region, match.span(), match.group(0)))
    for match in re.finditer(
        _NAMED_REGION_BRIGHTNESS_ACTION,
        text,
        flags=re.IGNORECASE,
    ):
        named = match.group("named_region").casefold()
        found.append(
            (
                {
                    "portrait": "person",
                    "middle": "center",
                    "edge": "edges",
                }.get(named, named),
                match.span(),
                match.group(0),
            )
        )
    named_regions = [
        item for item in found if item[0] != "all"
    ]
    hard_all_found = any(item[0] == "all" for item in found)
    contextual_all_found = False
    for pattern, consume_span in _EXPLICIT_FULL_IMAGE_CONTEXTS:
        for match in pattern.finditer(text):
            attached_to_named_region = any(
                re.search(
                    r"[,.;:!?]|\b(?:and|but|while)\b",
                    text[
                        min(match.end(), named_span[1])
                        : max(match.start(), named_span[0])
                    ],
                    flags=re.IGNORECASE,
                )
                is None
                for _, named_span, _ in named_regions
            )
            if attached_to_named_region:
                continue
            contextual_all_found = True
            found.append(
                (
                    "all",
                    match.span()
                    if consume_span
                    else (match.start(), match.start()),
                    match.group(0),
                )
            )

    def is_attached_all_container(
        item: tuple[str, tuple[int, int], str],
    ) -> bool:
        if item[0] != "all" or not named_regions:
            return False
        if re.fullmatch(
            r"(?:in|on|for|of)\s+(?:(?:the|my|this|that|a|an)\s+)?"
            r"(?:(?:whole|entire)\s+)?(?:image|photo|picture|shot)",
            item[2],
            flags=re.IGNORECASE,
        ) is None:
            return False
        all_start, all_end = item[1]
        for _, (named_start, named_end), _ in named_regions:
            gap_start = min(all_end, named_end)
            gap_end = max(all_start, named_start)
            gap = text[gap_start:gap_end]
            if re.search(
                r"[,.;:!?]|\b(?:and|but|while)\b",
                gap,
                flags=re.IGNORECASE,
            ) is None:
                return True
        return False

    effective = [
        item
        for item in found
        if not is_attached_all_container(item)
    ]
    regions = sorted({region for region, _, _ in effective})
    if len(regions) > 1:
        raise EnglishPromptContractError(
            code="adaptive_multi_region_not_supported",
            message="One English prompt can edit only one region.",
            issues=tuple(
                {
                    "region": region,
                    "reason": "multiple_regions",
                }
                for region in regions
            ),
        )
    return (
        regions[0] if regions else None,
        [span for _, span, _ in found],
        bool(
            regions == ["all"]
            and contextual_all_found
            and not hard_all_found
        ),
    )


def _reject_action_bearing_region_compound(
    text: str,
    original: str,
) -> None:
    matches = list(_ACTION_BEARING_REGION.finditer(text))
    if not matches:
        return
    masked = _mask_spans(text, (match.span() for match in matches))
    residue_words = {
        match.group(0).casefold()
        for match in _ASCII_WORD.finditer(masked)
    }
    unsupported = sorted(
        residue_words - _ACTION_REGION_SAFE_RESIDUE_WORDS
    )
    if unsupported:
        _error(
            "adaptive_multi_region_not_supported",
            "An action-scoped region cannot be combined with another unscoped edit clause.",
            source_clause=original,
            reason="action_region_scope_with_other_clause",
            residue=unsupported,
        )


def _validate_region_scope_contract(
    text: str,
    original: str,
    *,
    region: str | None,
    region_spans: Iterable[tuple[int, int]],
    observation_spans: Iterable[tuple[int, int]],
    action_spans: Iterable[tuple[int, int]],
    operations: Iterable[EnglishOperation],
) -> None:
    spans = list(region_spans)
    observation_span_list = list(observation_spans)
    action_span_list = list(action_spans)
    operation_list = list(operations)
    axes = {operation.axis for operation in operation_list}
    if region in {None, "all"} or not spans:
        return

    segments = _segments(text)
    if not segments:
        return
    if any(
        connector in {";", ".", "!", "?"}
        for _, _, connector in segments[1:]
    ):
        _error(
            "adaptive_multi_region_not_supported",
            "A local region cannot silently cross a sentence boundary.",
            source_clause=original,
            reason="region_scope_crosses_sentence_boundary",
            axes=sorted(axes, key=ADAPTIVE_AXIS_ORDER.index),
        )

    def segment_index_for_span(span: tuple[int, int]) -> int:
        start, end = span
        for index, (segment, offset, _) in enumerate(segments):
            if start < offset + len(segment) and offset < end:
                return index
        return max(0, len(segments) - 1)

    observation_indexes = {
        segment_index_for_span(span)
        for span in observation_span_list
    }
    if observation_indexes and action_span_list:
        action_region_indexes = {
            segment_index_for_span(span)
            for span in spans
            if not any(
                span[0] < observation_end
                and observation_start < span[1]
                for observation_start, observation_end in observation_span_list
            )
        }
        for action_index in {
            segment_index_for_span(span)
            for span in action_span_list
        }:
            action_segment = segments[action_index][0]
            pronoun_remedy = re.search(
                r"\b(?:brighten|lighten|darken)\s+it\b|"
                r"\b(?:make|let|get)\s+it\s+"
                r"(?:a\s+little\s+|a\s+bit\s+|slightly\s+)?"
                r"(?:brighter|darker)\b",
                action_segment,
                flags=re.IGNORECASE,
            )
            if (
                action_index not in action_region_indexes
                and pronoun_remedy is None
            ):
                _error(
                    "adaptive_multi_region_not_supported",
                    "A regional observation cannot silently scope an unqualified action.",
                    source_clause=original,
                    reason="region_observation_with_unscoped_action",
                    axes=sorted(axes, key=ADAPTIVE_AXIS_ORDER.index),
                )

    if len(axes) <= 1:
        return

    region_indexes = {
        segment_index_for_span(span)
        for span in spans
    }
    first_segment, first_offset, _ = segments[0]
    first_region_spans = [
        (max(0, start - first_offset), min(len(first_segment), end - first_offset))
        for start, end in spans
        if segment_index_for_span((start, end)) == 0
    ]
    first_residue = (
        _mask_spans(first_segment, first_region_spans)
        if first_region_spans
        else first_segment
    )
    first_words = {
        match.group(0).casefold()
        for match in _ASCII_WORD.finditer(first_residue)
    }
    prefix_scope_only = (
        region_indexes == {0}
        and first_words <= {"only", "overall", "please"}
    )
    if (
        _REGION_SUBJECT_BRIGHTNESS_OBSERVATION.search(text) is not None
        or _ACTION_BEARING_REGION.search(text) is not None
    ):
        _error(
            "adaptive_multi_region_not_supported",
            "A local region clause cannot silently scope another edit axis.",
            source_clause=original,
            reason="local_region_clause_with_other_axis",
            axes=sorted(axes, key=ADAPTIVE_AXIS_ORDER.index),
        )

    if prefix_scope_only:
        return

    if observation_indexes:
        _error(
            "adaptive_multi_region_not_supported",
            "A region attached to one observation cannot silently scope another edit axis.",
            source_clause=original,
            reason="observation_region_scope_with_other_axis",
            axes=sorted(axes, key=ADAPTIVE_AXIS_ORDER.index),
        )

    last_index = len(segments) - 1
    if region_indexes != {last_index}:
        _error(
            "adaptive_multi_region_not_supported",
            "Place one shared region before the whole request or after all coordinated edit axes.",
            source_clause=original,
            reason="non_trailing_region_scope_with_other_axis",
            axes=sorted(axes, key=ADAPTIVE_AXIS_ORDER.index),
        )

    last_segment, last_offset, _ = segments[last_index]
    last_region_spans = [
        (max(0, start - last_offset), min(len(last_segment), end - last_offset))
        for start, end in spans
        if segment_index_for_span((start, end)) == last_index
    ]
    trailing_start = max(end for _, end in last_region_spans)
    trailing_words = {
        match.group(0).casefold()
        for match in _ASCII_WORD.finditer(last_segment[trailing_start:])
    }
    if trailing_words - {
        "a",
        "bit",
        "dramatically",
        "far",
        "gently",
        "little",
        "lot",
        "much",
        "only",
        "really",
        "significantly",
        "slightly",
        "somewhat",
        "strongly",
        "subtly",
        "very",
        "way",
    }:
        _error(
            "adaptive_multi_region_not_supported",
            "A shared region must be the trailing scope of the coordinated edit.",
            source_clause=original,
            reason="region_scope_not_trailing",
            axes=sorted(axes, key=ADAPTIVE_AXIS_ORDER.index),
        )


def _segments(text: str) -> list[tuple[str, int, str | None]]:
    parts: list[tuple[str, int, str | None]] = []
    pattern = re.compile(
        r"\s+(and|but|while)\s+|([,;!?]|\.(?!\d))",
        re.IGNORECASE,
    )
    cursor = 0
    next_connector: str | None = None
    for match in pattern.finditer(text):
        segment = text[cursor:match.start()]
        parts.append((segment, cursor, next_connector))
        next_connector = (match.group(1) or match.group(2) or "").casefold()
        cursor = match.end()
    parts.append((text[cursor:], cursor, next_connector))
    return parts


def _first_direction_verb(
    segment: str,
) -> tuple[int, tuple[int, int]] | None:
    positive = _POSITIVE_VERBS.search(segment)
    negative = _NEGATIVE_VERBS.search(segment)
    candidates = [
        (1, positive.span()) if positive is not None else None,
        (-1, negative.span()) if negative is not None else None,
    ]
    present = [item for item in candidates if item is not None]
    if not present:
        return None
    present.sort(key=lambda item: item[1][0])
    return present[0]


def _axis_matches(segment: str) -> list[tuple[str, int, int, str]]:
    matches: list[tuple[str, int, int, str]] = []
    for axis in ADAPTIVE_AXIS_ORDER:
        pattern = _axis_match_pattern(axis)
        for match in pattern.finditer(segment):
            matches.append((axis, match.start(), match.end(), match.group(0)))
    matches.sort(key=lambda item: (item[1], ADAPTIVE_AXIS_ORDER.index(item[0])))
    return matches


def _overlaps_claim(
    axis: str,
    span: tuple[int, int],
    claims: dict[str, tuple[int, int]],
) -> bool:
    claimed = claims.get(axis)
    if claimed is None:
        return False
    return span[0] < claimed[1] and claimed[0] < span[1]


def _is_bare_shared_axis_segment(
    segment: str,
    axes: Iterable[tuple[str, int, int, str]],
) -> bool:
    masked = _mask_spans(
        segment,
        ((start, end) for _, start, end, _ in axes),
    )
    masked = _SINGLE_REGION_SCOPE.sub(" ", masked)
    words = {match.group(0).casefold() for match in _ASCII_WORD.finditer(masked)}
    return words <= {
        "a",
        "also",
        "bit",
        "dramatically",
        "far",
        "gently",
        "little",
        "much",
        "please",
        "significantly",
        "slightly",
        "somewhat",
        "strongly",
        "the",
        "very",
        "way",
    }


@lru_cache(maxsize=None)
def _axis_pattern(axis: str) -> str:
    return "|".join(f"(?:{item})" for item in _AXIS_LABEL_PATTERNS[axis])


@lru_cache(maxsize=None)
def _axis_match_pattern(axis: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{_axis_pattern(axis)})\b", re.IGNORECASE)


def _explicit_strength(text: str) -> str | None:
    if _SUBTLE_STRENGTH.search(text):
        return "subtle"
    if _STRONG_STRENGTH.search(text):
        return "strong"
    return None


def _reject_bare_already_comparatives(text: str, original: str) -> None:
    modifier = (
        r"(?:(?:a\s+(?:little(?:\s+bit)?|bit)|slightly|somewhat|"
        r"much|far|way|very|really)\s+)*"
    )
    comparative = rf"(?:{_COMPARATIVE_DESCRIPTOR})"
    subject = (
        rf"(?:(?:(?:the|my|this|that|a|an)\s+)?{_FULL_IMAGE_NOUN}|"
        r"it|this|that)"
    )
    for segment, _, _ in _segments(text):
        normalized = segment.strip()
        if re.fullmatch(
            rf"(?:(?:{subject}\s+)?already\s+{modifier}{comparative}|"
            rf"(?:{subject}\s+)?{modifier}{comparative}\s+already|"
            rf"already\s+{modifier}{comparative}\s+{_FULL_IMAGE_NOUN})",
            normalized,
            flags=re.IGNORECASE,
        ):
            _error(
                "adaptive_clarification_required",
                "This describes an adjustment that has already happened; it does not request another edit.",
                source_clause=original,
                reason="bare_already_comparative_observation",
                segment=normalized,
            )


def _validate_deictic_there(text: str, original: str) -> None:
    for match in re.finditer(r"\bthere\b", text, flags=re.IGNORECASE):
        if re.match(
            r"\s+(?:is|are|was|were|seems?\s+to\s+be|"
            r"appears?\s+to\s+be)\b",
            text[match.end() :],
            flags=re.IGNORECASE,
        ):
            continue
        _error(
            "adaptive_clarification_required",
            "The word 'there' names an unspecified location; please use a supported region.",
            source_clause=original,
            reason="unsupported_deictic_region",
            source_marker=match.group(0),
        )


def _validate_strength_contract(text: str, original: str) -> None:
    """Reject one clause that asks for both a subtle and a strong amount."""

    for segment, _, _ in _segments(text):
        subtle_matches = list(_SUBTLE_STRENGTH.finditer(segment))
        strong_matches = list(_STRONG_STRENGTH.finditer(segment))
        if not subtle_matches or not strong_matches:
            continue

        conflicting_strong_matches: list[re.Match[str]] = []
        for strong_match in strong_matches:
            strong_marker = strong_match.group(0).casefold()
            if strong_marker in {"very", "really"} and any(
                re.fullmatch(
                    r"\s*",
                    segment[strong_match.end() : subtle_match.start()],
                )
                is not None
                for subtle_match in subtle_matches
                if subtle_match.start() >= strong_match.end()
            ):
                # "very slightly" and "really subtly" are one small-amount
                # idiom, not two competing requested strengths.
                continue
            if strong_marker == "much" and any(
                re.fullmatch(
                    r"\s+too\s+",
                    segment[subtle_match.end() : strong_match.start()],
                    flags=re.IGNORECASE,
                )
                is not None
                for subtle_match in subtle_matches
                if subtle_match.end() <= strong_match.start()
            ):
                # "a little too much contrast" describes a small excess.
                continue
            conflicting_strong_matches.append(strong_match)

        if conflicting_strong_matches:
            _error(
                "adaptive_strength_conflict",
                "One edit clause requests both a subtle and a strong amount.",
                source_clause=original,
                reason="conflicting_strength_modifiers",
                segment=segment.strip(),
                subtle=[
                    match.group(0)
                    for match in subtle_matches
                ],
                strong=[
                    match.group(0)
                    for match in conflicting_strong_matches
                ],
            )


def _shared_coordination_trailing_strength(
    text: str,
    *,
    segments: Iterable[tuple[str, int, str | None]],
) -> str | None:
    """Apply one trailing modifier to every axis sharing one direction verb."""

    segment_list = list(segments)
    direction_indexes = [
        index
        for index, (segment, _, _) in enumerate(segment_list)
        if _first_direction_verb(segment) is not None
    ]
    if len(direction_indexes) != 1:
        return None
    direction_index = direction_indexes[0]
    explicit_axes = {
        axis
        for segment, _, _ in segment_list
        for axis, *_ in _axis_matches(segment)
    }
    if len(explicit_axes) < 2:
        return None
    inherited_axis_count = 0
    for index, (segment, _, connector) in enumerate(segment_list):
        axes = _axis_matches(segment)
        if not axes:
            continue
        if index < direction_index:
            return None
        if index == direction_index:
            continue
        if (
            connector not in {"and", ","}
            or not _is_bare_shared_axis_segment(segment, axes)
        ):
            return None
        inherited_axis_count += len(axes)
    if inherited_axis_count < 1:
        return None
    matches = list(
        re.finditer(
            r"\b(?:a\s+little\s+bit|a\s+little|a\s+bit|slightly|somewhat|"
            r"gently|subtly|very\s+much|a\s+lot|much|significantly|"
            r"dramatically|strongly|far|way|very|really)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    if len(matches) != 1:
        return None
    match = matches[0]
    if re.fullmatch(
        r"\s*(?:(?:please|kindly)\s*)?",
        text[match.end() :],
        flags=re.IGNORECASE,
    ) is None:
        return None
    return _explicit_strength(match.group(0))


def _strength(text: str) -> str:
    explicit = _explicit_strength(text)
    if explicit is not None:
        return explicit
    return "normal"


def _feedback_strength(text: str, position: int) -> str:
    for segment, offset, _ in _segments(text):
        if offset <= position <= offset + len(segment):
            normalized = re.sub(
                r"\bnot\s+too\s+(?:much|little)\b",
                " ",
                segment,
                flags=re.IGNORECASE,
            )
            return _explicit_strength(normalized) or "subtle"
    normalized = re.sub(
        r"\bnot\s+too\s+(?:much|little)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return _explicit_strength(normalized) or "subtle"


def _intent(axis: str, direction: int) -> str:
    policy = AXIS_POLICIES[axis]
    return policy.positive_intent if direction > 0 else policy.negative_intent


def _numeric_operation(
    *,
    axis: str,
    relation: str,
    direction: int,
    value: float,
    original: str,
    marker: str,
) -> EnglishOperation:
    return EnglishOperation(
        axis=axis,
        direction=direction,
        relation=relation,
        strength="subtle",
        source_clause=original,
        source_marker=marker,
        source_intent=(
            "explicit_numeric"
            if relation == "absolute"
            else "explicit_relative_numeric"
        ),
        explicitness="explicit_axis",
        numeric_value=value if relation == "absolute" else None,
        relative_delta=value if relation == "relative_numeric" else None,
    )


def _finite_decimal(value: str, original: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise EnglishPromptContractError(
            code="adaptive_invalid_numeric",
            message="The numeric value is not a valid decimal.",
            issues=(
                {
                    "source_clause": original,
                    "reason": "invalid_decimal",
                },
            ),
        ) from exc
    if not math.isfinite(numeric):
        _error(
            "adaptive_invalid_numeric",
            "The numeric value must be finite.",
            source_clause=original,
            reason="non_finite_numeric",
        )
    return numeric


def _merge_reinforcing_observation_actions(
    operations: list[EnglishOperation],
) -> list[EnglishOperation]:
    """Collapse one observation plus one matching action into one correction.

    Natural requests often state the problem and the remedy together, for
    example "too bright, darken a little". This is not a duplicate edit: the
    action confirms the correction implied by the observation. Keep this
    deliberately narrow so repeated actions and opposite directions still
    reach the conflict validator.
    """

    by_axis: dict[str, list[tuple[int, EnglishOperation]]] = {}
    for index, operation in enumerate(operations):
        by_axis.setdefault(operation.axis, []).append((index, operation))

    replacements: dict[int, EnglishOperation] = {}
    removed: set[int] = set()
    for items in by_axis.values():
        if len(items) != 2:
            continue
        observation_items = [
            item for item in items if item[1].relation == "correct"
        ]
        action_items = [
            item for item in items if item[1].relation == "initial"
        ]
        if len(observation_items) != 1 or len(action_items) != 1:
            continue
        observation_index, observation = observation_items[0]
        action_index, action = action_items[0]
        if observation.direction != action.direction:
            continue
        replacements[observation_index] = replace(
            observation,
            # The explicit remedy controls the requested amount.  In
            # "way too bright, darken just a little", the strong observation
            # explains the problem but must not override "a little".  A plain
            # "darken it" carries no amount of its own, so it keeps the
            # observation's explicit "way too" strength instead.
            strength=(
                action.strength
                if action.strength != "normal"
                else observation.strength
            ),
            source_marker=(
                f"{observation.source_marker}; {action.source_marker}"
            ),
            confidence="high",
        )
        removed.add(action_index)

    return [
        replacements.get(index, operation)
        for index, operation in enumerate(operations)
        if index not in removed
    ]


def _validate_operation_set(
    operations: list[EnglishOperation],
    original: str,
) -> None:
    if len(operations) > 3:
        raise EnglishPromptContractError(
            code="adaptive_operation_limit_exceeded",
            message="One prompt supports at most three primary operations.",
            issues=tuple(
                {
                    "axis": operation.axis,
                    "source_clause": operation.source_clause,
                    "reason": "operation_limit",
                }
                for operation in operations
            ),
        )
    by_axis: dict[str, list[EnglishOperation]] = {}
    for operation in operations:
        by_axis.setdefault(operation.axis, []).append(operation)
    conflicts = [
        axis
        for axis, items in by_axis.items()
        if len(items) > 1
        and (
            len({item.direction for item in items}) > 1
            or len({item.relation for item in items}) > 1
        )
    ]
    if conflicts:
        _error(
            "adaptive_operation_conflict",
            "The same axis has conflicting English instructions.",
            axis=conflicts[0],
            source_clause=original,
            reason="same_axis_conflict",
        )
    duplicates = [axis for axis, items in by_axis.items() if len(items) > 1]
    if duplicates:
        _error(
            "adaptive_operation_conflict",
            "Repeat edits for the same axis in separate requests.",
            axis=duplicates[0],
            source_clause=original,
            reason="same_axis_duplicate",
        )


def _validate_residue(
    text: str,
    spans: Iterable[tuple[int, int]],
    original: str,
) -> None:
    span_list = list(spans)
    for segment, offset, connector in _segments(text):
        segment_words = [
            match.group(0).casefold() for match in _ASCII_WORD.finditer(segment)
        ]
        if not segment_words:
            continue
        segment_end = offset + len(segment)
        has_consumed_semantics = any(
            start < segment_end and offset < end for start, end in span_list
        )
        if has_consumed_semantics:
            continue
        normalized_segment = " ".join(segment_words)
        is_polite_fragment = normalized_segment in {
            "please",
            "kindly",
            "could you",
            "can you",
            "would you",
            "could you please",
            "can you please",
            "would you please",
            "please could you",
            "please can you",
            "please would you",
        }
        if is_polite_fragment and connector in {None, ","}:
            continue
        _error(
            "adaptive_clarification_required",
            "A clause did not contain a complete supported edit; no partial edit was applied.",
            source_clause=original,
            reason="unconsumed_english_clause",
            unconsumed=segment_words,
        )

    masked = _mask_spans(text, span_list)
    masked = re.sub(
        r"\b(?:in|on|for|around)\s+(?:the\s+)?(?:sky|person|portrait|"
        r"background|highlights?|shadows?|center|middle|edges?)\b",
        lambda match: " " * len(match.group(0)),
        masked,
        flags=re.IGNORECASE,
    )
    _validate_residual_function_words(
        text=text,
        masked=masked,
        spans=span_list,
        original=original,
    )
    words = [match.group(0).casefold() for match in _ASCII_WORD.finditer(masked)]
    dangling_actions = [
        word for word in words if word in _DANGLING_ACTION_WORDS
    ]
    if dangling_actions:
        _error(
            "adaptive_clarification_required",
            "An action-like word was not attached to a complete supported edit.",
            source_clause=original,
            reason="unconsumed_action_clause",
            unconsumed=dangling_actions,
        )
    unknown = [word for word in words if word not in _SAFE_RESIDUE_WORDS]
    if unknown:
        _error(
            "adaptive_clarification_required",
            "Part of the English prompt was not understood; no partial edit was applied.",
            source_clause=original,
            reason="unconsumed_english_clause",
            unconsumed=unknown,
        )
    punctuation_checked = re.sub(
        r"\s+-\s+(?=(?:a\s+little|a\s+bit|slightly|somewhat|gently|"
        r"subtly|much|very|far|way|really|strongly)\b)",
        " ",
        masked,
        flags=re.IGNORECASE,
    )
    remaining_characters = _ASCII_WORD.sub(" ", punctuation_checked)
    invalid_characters = sorted(
        {
            character
            for character in remaining_characters
            if not character.isspace()
            and character not in {".", ",", "!", "?", ";", ":", "'", '"', "(", ")"}
        }
    )
    if invalid_characters:
        _error(
            "adaptive_clarification_required",
            "Unsupported characters remained after parsing; no partial edit was applied.",
            source_clause=original,
            reason="unconsumed_characters",
            unconsumed=invalid_characters,
        )


def _validate_residual_function_words(
    *,
    text: str,
    masked: str,
    spans: list[tuple[int, int]],
    original: str,
) -> None:
    segments = _segments(text)

    def segment_for(position: int) -> tuple[str, int, int]:
        for segment, offset, _ in segments:
            end = offset + len(segment)
            if offset <= position <= end:
                return segment, offset, end
        return text, 0, len(text)

    for match in re.finditer(
        r"\b(?:the|a|an|my)\b",
        masked,
        flags=re.IGNORECASE,
    ):
        word = match.group(0).casefold()
        if word == "a" and re.match(
            r"\s+(?:little|bit|lot)\b",
            masked[match.end() :],
            flags=re.IGNORECASE,
        ):
            continue
        _, _, segment_end = segment_for(match.start())
        following_spans = sorted(
            (start, end)
            for start, end in spans
            if match.end() <= start < segment_end and end > start
        )
        if not following_spans:
            _error(
                "adaptive_clarification_required",
                "An article or possessive word is not attached to a parsed object.",
                source_clause=original,
                reason="orphan_article",
                unconsumed=[word],
            )
        next_start, next_end = following_spans[0]
        gap = masked[match.end() : next_start]
        if re.fullmatch(
            r"\s+(?:image|photo|picture|shot|colors?)\s+"
            r"(?:is\s+looking|are\s+looking|is|are|looks?|feels?|seems?|"
            r"appears?)"
            r"(?:\s+(?:still|way|far|very|really|too|a\s+bit|a\s+little|"
            r"slightly|somewhat))*\s*",
            gap,
            flags=re.IGNORECASE,
        ):
            continue
        if _ASCII_WORD.search(gap):
            _error(
                "adaptive_clarification_required",
                "Words between an article and the parsed object were not understood.",
                source_clause=original,
                reason="orphan_article",
                unconsumed=[word],
            )
        next_fragment = text[next_start:next_end]
        starts_with_axis = any(
            start == 0
            for _, start, _, _ in _axis_matches(next_fragment)
        )
        starts_with_object = re.match(
            r"\s*(?:whole|entire|overall|image|photo|picture|shot|colors?|"
            r"sky|person|portrait|background|highlights?|shadows?|center|"
            r"middle|edges?)\b",
            next_fragment,
            flags=re.IGNORECASE,
        ) is not None
        if not starts_with_axis and not starts_with_object:
            _error(
                "adaptive_clarification_required",
                "An article or possessive word is not attached to a parsed object.",
                source_clause=original,
                reason="orphan_article",
                unconsumed=[word],
            )

    leftover_preposition = re.search(
        r"\b(?:in|on|for|around|to|from|of|at|by)\b",
        masked,
        flags=re.IGNORECASE,
    )
    if leftover_preposition is not None:
        _error(
            "adaptive_clarification_required",
            "A preposition was not attached to a complete supported edit or region.",
            source_clause=original,
            reason="orphan_preposition",
            unconsumed=[leftover_preposition.group(0).casefold()],
        )

    perception_words = list(
        re.finditer(
            r"\b(?:is|are|be|feel|feels|look|looks|seem|seems|appear|"
            r"appears|looking)\b",
            masked,
            flags=re.IGNORECASE,
        )
    )
    if not perception_words:
        return
    for match in perception_words:
        _, segment_start, segment_end = segment_for(match.start())
        following_starts = sorted(
            start
            for start, end in spans
            if match.end() <= start < segment_end and end > start
        )
        if not following_starts:
            _error(
                "adaptive_clarification_required",
                "A perception or copula frame is incomplete.",
                source_clause=original,
                reason="orphan_perception_frame",
                unconsumed=[match.group(0).casefold()],
            )
        prefix = " ".join(
            masked[segment_start : following_starts[0]].split()
        )
        if re.fullmatch(
            r"(?:(?:(?:the|my|this|that|a|an)\s+)?"
            r"(?:image|photo|picture|shot|colors?)|it|this|that|there)\s+"
            r"(?:is\s+looking|are\s+looking|is|are|looks?|feels?|seems?|"
            r"appears?)"
            r"(?:\s+(?:still|way|far|very|really|too|a\s+bit|a\s+little|"
            r"slightly|somewhat))*",
            prefix,
            flags=re.IGNORECASE,
        ) is None:
            _error(
                "adaptive_clarification_required",
                "A perception or copula frame is incomplete.",
                source_clause=original,
                reason="orphan_perception_frame",
                unconsumed=[match.group(0).casefold()],
            )


def _mask_spans(text: str, spans: Iterable[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        safe_start = max(0, min(len(chars), int(start)))
        safe_end = max(safe_start, min(len(chars), int(end)))
        for index in range(safe_start, safe_end):
            chars[index] = " "
    return "".join(chars)


def _mask_regex_matches(text: str, pattern: re.Pattern[str]) -> str:
    return _mask_spans(text, (match.span() for match in pattern.finditer(text)))


def _validate_modal_frames(text: str, original: str) -> None:
    if re.search(r"\b(?:can|could|would)\b", text) is None:
        return
    allowed_patterns = (
        re.compile(
            r"^(?:please\s+)?(?:can|could|would)\s+you(?:\s+please)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r",\s*(?:can|could|would)\s+you(?:\s+please)?\s*$",
            re.IGNORECASE,
        ),
        re.compile(r"^could\s+i\s+get\b", re.IGNORECASE),
        re.compile(r"^i\s+would\s+(?:like|prefer)\b", re.IGNORECASE),
    )
    allowed_spans = [
        match.span()
        for pattern in allowed_patterns
        if (match := pattern.search(text)) is not None
    ]
    residual = _mask_spans(text, allowed_spans)
    if re.search(r"\b(?:can|could|would)\b", residual):
        _error(
            "adaptive_clarification_required",
            "The modal wrapper is incomplete or does not use a supported request frame.",
            source_clause=original,
            reason="unsupported_modal_frame",
        )


def _has_preset_signal(text: str) -> bool:
    return re.search(
        r"\b(?:retro|vintage|cinematic|movie|japanese|film)\b|"
        r"\bold\s+camera\b",
        text,
        flags=re.IGNORECASE,
    ) is not None


def _error(code: str, message: str, **issue: Any) -> None:
    raise EnglishPromptContractError(
        code=code,
        message=message,
        issues=(issue,) if issue else (),
    )
