"""Small, explicit KYC vocabulary to complement general-purpose embeddings.

These are retrieval associations, not rules determining whether evidence is valid.
Only expand a complete known query: expanding one word in a qualified query would
silently discard constraints such as a person's name or a billing date.
"""

import re

from nevis_search.text import normalize

CONCEPTS = (
    (
        "address proof",
        "proof of address",
        "proof of residence",
        "residency evidence",
        "utility bill",
        "electricity bill",
        "water bill",
        "gas bill",
        "council tax bill",
    ),
    (
        "identity verification",
        "proof of identity",
        "photo identification",
        "passport",
        "driving licence",
        "driver's license",
        "national identity card",
    ),
    (
        "income verification",
        "proof of income",
        "salary evidence",
        "payslip",
        "pay slip",
        "pay stub",
        "salary slip",
    ),
)


def related_terms(query: str) -> tuple[str, ...]:
    normalized = normalize(query)
    for terms in CONCEPTS:
        if normalized in terms:
            return tuple(term for term in terms if term != normalized)
    return ()


def matches_concept(terms: tuple[str, ...], text: str) -> bool:
    normalized = normalize(text)
    return any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalized) for term in terms)
