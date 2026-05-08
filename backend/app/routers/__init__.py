"""Aggregated FastAPI routers for the application."""

from . import (
    auth,
    ped,
    families,
    structural_variants,
    bed,
    chromosomes,
    genes,
    blacklist,
    cnvs,
    panels,
    projects,
    species,
    assemblies,
    admin,
    cram,
    reference,
    repeat_expansions,
    family_imports,
    product,
)

all_routers = [
    auth.router,
    ped.router,
    families.router,
    structural_variants.router,
    bed.router,
    chromosomes.router,
    genes.router,
    blacklist.router,
    cnvs.router,
    panels.router,
    projects.router,
    species.router,
    assemblies.router,
    admin.router,
    cram.router,
    reference.router,
    repeat_expansions.router,
    family_imports.router,
    product.router,
]

__all__ = ["all_routers"]
