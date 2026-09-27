"""Conservative normalization of facts stated in a French caller transcript."""

from __future__ import annotations

import re


_NUMBER_WORDS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11,
    "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15,
    "seize": 16, "vingt": 20, "trente": 30, "quarante": 40,
    "cinquante": 50, "soixante": 60,
}
_NUMBER = r"\d{1,3}|[a-zéû]+(?:[ -](?:et[ -])?[a-zéû]+){0,2}"
_AGO = re.compile(rf"\b(?:il\s+y\s+a(?:\s+eu)?|y\s+a|ça\s+fait)\s+(?P<number>{_NUMBER})\s+minutes?\b", re.I)
_HOURS_AGO = re.compile(r"\b(?:il\s+y\s+a(?:\s+eu)?|y\s+a|ça\s+fait)\s+"
                        r"(?P<number>\d{1,2}|une?|deux|trois)\s+heures?\b", re.I)
_STREET = re.compile(
    r"\b(?:(?P<number>\d{1,4})\s*(?P<suffix>bis|ter)?\s+)?"
    r"(?P<kind>rue|avenue|boulevard|place|route|chemin|impasse|allée|quai|cours)\s+"
    r"(?P<tail>[^.!?;\n]{2,100})",
    re.I,
)
_CONNECTORS = {"de", "du", "des", "la", "le", "les", "d'", "l'", "et"}


def minutes_ago(text: str) -> int | None:
    """Return an exact relative duration, leaving approximate speech unparsed."""
    match = _AGO.search(text.casefold())
    if not match:
        hours = _HOURS_AGO.search(text.casefold())
        if not hours:
            return None
        spoken_hours = hours["number"].casefold()
        count = int(spoken_hours) if spoken_hours.isdecimal() else _NUMBER_WORDS.get(spoken_hours)
        return count * 60 if count is not None and 1 <= count <= 3 else None
    spoken = re.sub(r"[ -]+", " ", match["number"].casefold()).strip()
    if spoken.isdecimal():
        minutes = int(spoken)
    elif spoken in _NUMBER_WORDS:
        minutes = _NUMBER_WORDS[spoken]
    else:
        parts = spoken.replace(" et ", " ").split()
        if (len(parts) != 2 or parts[0] not in {"vingt", "trente", "quarante", "cinquante", "soixante"}
                or parts[1] not in _NUMBER_WORDS or _NUMBER_WORDS[parts[1]] > 16):
            return None
        minutes = _NUMBER_WORDS[parts[0]] + _NUMBER_WORDS[parts[1]]
    return minutes if 1 <= minutes <= 180 else None


def normalize_address(text: str) -> str | None:
    """Format a stated street address without guessing missing components."""
    match = _STREET.search(text.strip())
    if not match:
        return None
    tail = re.split(r"\s+(?:et\s+(?:je|il|elle|on|nous|un|une|le|la)|je\s+|c'est\s+)", match["tail"], maxsplit=1, flags=re.I)[0].strip(" ,")
    postal_city = re.search(r",?\s*(?P<postal>\d{5})\s+(?P<city>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' -]{1,50})$", tail)
    city = None
    postal = None
    if postal_city:
        road = tail[:postal_city.start()].strip(" ,")
        postal = postal_city["postal"]
        city = postal_city["city"]
    elif re.search(r"\s+à\s+", tail, re.I):
        road, city = re.split(r"\s+à\s+", tail, maxsplit=1, flags=re.I)
    else:
        road = tail
    road = road.strip(" ,")
    if not road or len(road.split()) > 7 or not any(word.casefold() not in _CONNECTORS for word in road.split()):
        return None
    if city:
        city = city.strip(" ,")
        if not city or len(city.split()) > 4:
            return None
    number = match["number"] or ""
    suffix = f" {match['suffix'].lower()}" if match["suffix"] else ""
    street = f"{number}{suffix} {match['kind'].capitalize()} {road}".strip()
    locality = " ".join(part for part in (postal, city) if part)
    return f"{street}, {locality}" if locality else street
