"""English-only application narrative; original evidence is validated separately."""

import unicodedata


def english_narrative(text: str) -> bool:
    return all(not char.isalpha() or unicodedata.name(char, "").startswith("LATIN ") for char in text)


def validate_narrative(result):
    from .schemas import Answer, Extraction

    if isinstance(result, Answer):
        narratives = [claim.text for claim in result.claims] + result.missing_context
    elif isinstance(result, Extraction):
        narratives = result.flags
    else:
        narratives = []
    if not all(english_narrative(text) for text in narratives):
        raise ValueError("Generated narrative must be in English")
    return result
