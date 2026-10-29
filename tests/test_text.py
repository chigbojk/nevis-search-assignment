import pytest
from conftest import FakeEncoder

from nevis_search.text import chunk_text, lexical_score, summarize


def test_chunking_covers_tail_with_overlap_and_rejects_bad_sizes():
    words = [f"word{i}" for i in range(401)]
    chunks = chunk_text(" ".join(words), size=160, overlap=30)

    assert len(chunks) == 3
    chunk_words = [chunk.split() for chunk in chunks]
    assert chunk_words[1][:30] == chunk_words[0][-30:]
    assert chunk_words[2][:30] == chunk_words[1][-30:]
    assert chunk_words[-1][0] == "word260"
    assert chunk_words[-1][-1] == "word400"
    assert {word for chunk in chunk_words for word in chunk} == set(words)

    for size, overlap in ((0, 0), (10, -1), (10, 10)):
        with pytest.raises(ValueError, match="Chunk size"):
            chunk_text("some words", size=size, overlap=overlap)


def test_lexical_matching_is_casefolded_and_accepts_complete_terms_in_any_order():
    assert lexical_score("NORTH MIRA", "Mira North") == 0.85
    assert lexical_score("north mira", "Mira North client") == 0.85
    assert lexical_score("northland", "North Mira") == 0.0


def test_long_summary_uses_only_source_sentences_in_original_order():
    source = "First source sentence. Second source sentence. Third source sentence. " * 20
    summary = summarize(source, FakeEncoder())

    assert 0 < len(summary) <= 600
    parts = summary.split(". ")
    assert len(parts) <= 3
    assert all(part.rstrip(".") in source for part in parts)
    assert source.index(parts[0]) <= source.index(parts[-1])
