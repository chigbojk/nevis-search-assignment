import re
import unicodedata

import numpy as np

from nevis_search.embeddings import Encoder


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def lexical_score(query: str, text: str) -> float:
    """Prefer exact substrings; also accept all query words in a different order."""
    query, text = normalize(query), normalize(text)
    if query in text:
        return 1.0
    terms = set(re.findall(r"\w+", query))
    words = set(re.findall(r"\w+", text))
    return 0.85 if terms and terms <= words else 0.0


def chunk_text(text: str, size: int = 160, overlap: int = 30) -> list[str]:
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("Chunk size must be positive and overlap smaller than size")
    words = list(re.finditer(r"\S+", text))
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(text[words[start].start() : words[end - 1].end()])
        if end == len(words):
            break
        start = end - overlap
    return chunks


def summarize(content: str, encoder: Encoder, max_chars: int = 600) -> str:
    """Choose representative source sentences, preserving their original order."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", content) if s.strip()]
    if len(content) <= max_chars:
        return content
    # Bound work on documents made of thousands of tiny sentences.
    candidates = list(dict.fromkeys(sentences[:200]))
    vectors = encoder.encode(candidates)
    centroid = vectors.mean(axis=0)
    ranked = np.argsort(-(vectors @ centroid), kind="stable")
    selected = []
    length = 0
    for index in ranked:
        sentence = candidates[index]
        if length + len(sentence) + bool(selected) <= max_chars:
            selected.append(int(index))
            length += len(sentence) + (len(selected) > 1)
        if len(selected) == 3:
            break
    if not selected:
        return content[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return " ".join(candidates[index] for index in sorted(selected))
