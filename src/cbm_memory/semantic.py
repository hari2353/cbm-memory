"""Offline semantic similarity: hashed n-gram TF-IDF vectors (stdlib only).

No embedding models on purpose - uvx distribution must stay zero-setup and
network-free. Word tokens + char 4-grams catch stem/typo variants ("jwt" ~
"jwtauth"), IDF weighs rare words, L2-normalized hashed vectors make cosine
cheap and deterministic.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

_DIM = 512
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SEG = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+")
_WORD = re.compile(r"[a-z]{2,}")


def tokenize_words(text: str) -> list[str]:
    """Lowercased word tokens (camelCase/snake_case split) - also the df basis."""
    out = []
    for tok in _TOKEN.findall(text or ""):
        out += [seg.lower() for seg in _SEG.findall(tok)]
    return out


def embed(text: str, idf: dict[str, float] | None = None) -> dict[int, float]:
    words = Counter()
    for w in tokenize_words(text):
        if len(w) >= 2:
            words[w] += 1
            for i in range(max(len(w) - 3, 0)):  # char 4-grams: "jwt" in "jwtauth"
                words["#4:" + w[i:i + 4]] += 1
    vec: dict[int, float] = {}
    for w, c in words.items():
        h = int(hashlib.md5(w.encode("utf-8")).hexdigest()[:8], 16) % _DIM
        vec[h] = vec.get(h, 0.0) + c * ((idf or {}).get(w, 1.0))
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / norm for k, v in vec.items()}


def cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if len(b) < len(a):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())
