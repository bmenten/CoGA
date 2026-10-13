"""A family's own folders in the local data directory, and the paths kept inside them.

With local storage a family's files lie in two folders of the data directory: the flat
``<data>/<family>`` and its imported package, ``<data>/families/<family>``. The endpoints that
serve a family's files from there look a file up only inside those folders: the aligned reads
(``routers/cram.py``), the CNV caller's signal files (``routers/signal_tracks.py``) and the
sequencing-QC report (``routers/family_qc_reports.py``). What names the file is data: the
family and sample IDs come from the request, and the ID rule lets them hold ``/`` and ``..``
(SEC-15); a recorded path comes back from ``samples.metadata``, where an edited row can hold
``..`` or an absolute path.

So a path is built and checked as a string first: joined to the family's folder, normalised
(``os.path.normpath``) and dropped when it leaves that folder, before anything is looked up by
it. One that stays is kept only when its real path, symlinks resolved, lies inside one of the
family's own folders too, so a link cannot lead into another family's folder or out of the
data directory (SEC-20, SEC-25). A link between the family's own folders still works.
"""

from __future__ import annotations

from collections.abc import Iterable
import os

# The folder of the data directory that holds the imported family packages.
PACKAGES_FOLDER = "families"


def within(path: str, folder: str) -> bool:
    """Whether the normalised ``path`` lies inside ``folder``, told from the strings."""
    return path.startswith(folder + os.sep)


def family_folders(data_dir: str | os.PathLike[str], family_id: str) -> dict[str, str]:
    """The family's own folders, ``<data>/<family>`` and ``<data>/families/<family>``, keyed by
    the folder of the data directory that holds them (``""`` and ``"families"``).

    Each is the family ID joined to the real path of the data directory (or of its packages
    folder, when that lies inside it) and normalised as a string; nothing is looked up to
    tell it. A family ID that is no single folder name (empty, ``.``, ``..``, or holding a
    ``/``) has no folder. The packages folder holds every package, so no family has it, or a
    folder holding it, as its flat folder: a family named ``families`` has its package
    folder only.
    """
    data = os.path.realpath(data_dir)
    packages = os.path.realpath(os.path.join(data, PACKAGES_FOLDER))
    parents = {"": data}
    if within(packages, data):
        parents[PACKAGES_FOLDER] = packages
    folders: dict[str, str] = {}
    for name, parent in parents.items():
        folder = os.path.normpath(os.path.join(parent, family_id))
        if os.path.dirname(folder) != parent or os.path.basename(folder) != family_id:
            continue
        if not name and (folder == packages or within(packages, folder)):
            continue
        folders[name] = folder
    return folders


def real_path_within(path: str, folders: Iterable[str]) -> bool:
    """Whether ``path`` lies, symlinks resolved, inside one of ``folders``: a symlinked
    folder or file that leads out of the family's folders is not followed."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        # A NUL in an ID or a recorded path (ValueError).
        return False
    return any(within(real, folder) for folder in folders)


def path_in_family_folder(folder: str, own_folders: Iterable[str], *parts: str) -> str | None:
    """``parts`` joined to ``folder``, one of the family's folders, and normalised as a string,
    when that stays inside ``folder`` and its real path inside one of ``own_folders`` (the
    family's folders); ``None`` otherwise.

    A path that leaves ``folder`` (``..`` that climbs out, an absolute path elsewhere) is
    dropped before anything is looked up by it.
    """
    path = os.path.normpath(os.path.join(folder, *parts))
    if within(path, folder) and real_path_within(path, own_folders):
        return path
    return None
