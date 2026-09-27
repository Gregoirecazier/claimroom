"""Search normalization shared by SQL parameters and test adapters.

Fold Latin diacritics and ignore spaces/hyphens, retaining punctuation such as
percent and underscore as literal characters. No PostgreSQL extension is needed.
"""
import re
import unicodedata

# Latin letters whose canonical decomposition is one base letter plus accents.
_FOLDS = {chr(code): unicodedata.normalize("NFD", chr(code))[0]
          for code in range(0xC0, 0x250)
          if unicodedata.normalize("NFD", chr(code))[0].isascii()
          and unicodedata.normalize("NFD", chr(code))[0].isalpha()}
_COMBINING = "".join(chr(code) for code in range(0x300, 0x370))
SEARCH_ACCENTS = "".join(_FOLDS) + _COMBINING
SEARCH_BASES = "".join(_FOLDS.values())
SEARCH_TRANSLATION = str.maketrans({**_FOLDS, **{mark: None for mark in _COMBINING}})


def normalize_case_search(value: str) -> str:
    return re.sub(r"[\s-]+", "", value.lower().translate(SEARCH_TRANSLATION))
