"""Label classification derived only from public names.

label_kind is not an iBIS field. It follows the explicit label rules used
for the hand-collected careers: national, qualifier, cup, youth, the
"utveckling elit" bucket, playoff words, then league words. Anything that
matches none of those tokens stays None.
"""

from __future__ import annotations

import unicodedata

_NATIONAL = (
    "dam-vm",
    "herr-vm",
    "landslag",
    "wfc",
    "euro floorball",
    "finnkampen",
    "world championship",
)
_CUP = ("cup", "bäst i stan", "bast i stan", "innebandyfesten")
_YOUTH = (
    "jas ",
    "jas-",
    "juniorallsvenskan",
    "sdf-sm",
    "junior-sm",
    "junior sm",
    "ungdom",
    "pojkar",
    "flickor",
    "pojk",
    "flick",
)
_PLAYOFF = ("kvartsfinal", "semifinal", "slutspel", "play off", "playoff", "finalserie")
_REGULAR = ("division", "allsvenskan", "elitserien", "superligan", "ssl")


def _fold(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFC", value).casefold()
    return " ".join(text.split())


def label_kind(
    name: str | None,
    category: str | None = None,
    *,
    national_team: bool = False,
) -> str | None:
    if national_team:
        return "national_team"
    text = _fold(" ".join(part for part in (name, category) if part))
    if not text:
        return None
    padded = f" {text} "
    if any(token in text for token in _NATIONAL):
        return "national_team"
    if "kval" in text:
        return "qualification"
    if any(token in text for token in _CUP) or " dm" in padded or padded.startswith("dm "):
        return "cup"
    if any(token in padded for token in _YOUTH) or padded.startswith("jas "):
        return "youth"
    if "utveckling elit" in text:
        return "other"
    if any(token in text for token in _PLAYOFF):
        return "playoff"
    if any(token in text for token in _REGULAR):
        return "regular"
    return None
