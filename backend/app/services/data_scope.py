from __future__ import annotations


def normalize_chromosome(chromosome: str) -> str:
    chromosome = str(chromosome or "").strip()
    return chromosome[3:] if chromosome.lower().startswith("chr") else chromosome


def chromosome_aliases(chromosome: str) -> list[str]:
    normalized = normalize_chromosome(chromosome)
    prefixed = f"chr{normalized}"
    if prefixed == chromosome:
        return [chromosome, normalized]
    return [normalized, prefixed]
