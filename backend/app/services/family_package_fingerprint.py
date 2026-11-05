"""The fingerprint of a local package folder: what it holds, by name, size and modification time.

An import is validated, queued and run at different moments, and the folder it reads is a
pipeline's output on a share that other hosts write: the package can change between the
dry run a user reviewed and the import that writes the family, or while the import reads it.
The fingerprint tells that it did, without reading a file's content: it is the SHA-256 of
one record per entry of the folder, at any depth, in name order:

- a file: its path in the package, its size and its modification time in nanoseconds;
- a symbolic link: its path and the target it names (not followed: a file the import reads
  through one lies in the package, ``_resolve_package_path``, and has its own record);
- an entry that is neither (a FIFO, a socket, a device), and a folder that cannot be listed
  or an entry that cannot be looked up, by its path and what it is;

and of the manifest the import reads instead of the folder's own, when CoGA keeps one
(``kept_manifest_sha256``): a folder manifest is a file of the folder. A folder's own
records are its entries': an empty folder adds none.

Left out are the files an operating system or a file browser writes into a folder it shows,
which no import reads: ``.DS_Store``, ``Thumbs.db``, ``desktop.ini`` and AppleDouble files
(``._*``, hidden from every glob a package is read by). Browsing the share does not change a
package's fingerprint.

The fingerprint of a package staged from a bucket is not taken: the job downloads it again,
and the copy's times are the download's.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import errno
import hashlib
import json
import os
import stat

# Hashed first, so a change of what the records hold makes another fingerprint.
FINGERPRINT_FORMAT = "coga-package-fingerprint 1"

# More entries than a family's package holds: a folder that holds more is most likely an
# import folder or a share named by mistake, and is not walked further.
PACKAGE_FINGERPRINT_MAX_ENTRIES = 100_000

_IGNORED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
_IGNORED_PREFIX = "._"


class PackageFolderTooLarge(Exception):
    """The folder holds more than PACKAGE_FINGERPRINT_MAX_ENTRIES entries; the message says so."""

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"The package folder holds more than {limit:,} files and folders: CoGA does not import "
            "a folder this large. Check that the path names one family's package."
        )


@dataclass(frozen=True, slots=True)
class PackageFingerprint:
    """A package folder's fingerprint (``sha256``, 64 hexadecimal digits) and the number of
    files it covers."""

    sha256: str
    files: int

    @property
    def short(self) -> str:
        """The first twelve digits, as a log line or the import panel names it."""
        return self.sha256[:12]


def _ignored(name: str) -> bool:
    return name in _IGNORED_NAMES or name.startswith(_IGNORED_PREFIX)


def _error_name(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, "error")


def _records(folder: str) -> Iterator[list[object]]:
    """One record per entry under ``folder``, as ``package_fingerprint`` hashes them, in no
    particular order. Raises OSError when ``folder`` itself cannot be listed, and
    PackageFolderTooLarge past PACKAGE_FINGERPRINT_MAX_ENTRIES entries."""
    # The folder itself must list: a package that cannot be read has no fingerprint.
    with os.scandir(folder):
        pass
    pending: list[tuple[str, str]] = [(folder, "")]
    seen = 0
    while pending:
        current, relative = pending.pop()
        try:
            with os.scandir(current) as listing:
                entries = list(listing)
        except OSError as exc:
            yield [relative, "unlisted", _error_name(exc)]
            continue
        for entry in entries:
            if _ignored(entry.name):
                continue
            seen += 1
            if seen > PACKAGE_FINGERPRINT_MAX_ENTRIES:
                raise PackageFolderTooLarge(PACKAGE_FINGERPRINT_MAX_ENTRIES)
            path = f"{relative}/{entry.name}" if relative else entry.name
            try:
                if entry.is_symlink():
                    yield [path, "link", os.readlink(entry.path)]
                    continue
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                yield [path, "unreadable", _error_name(exc)]
                continue
            if stat.S_ISDIR(info.st_mode):
                pending.append((entry.path, path))
            elif stat.S_ISREG(info.st_mode):
                yield [path, "file", info.st_size, info.st_mtime_ns]
            else:
                yield [path, "other", stat.S_IFMT(info.st_mode)]


def package_fingerprint(root: str | os.PathLike[str], *, kept_manifest_sha256: str | None) -> PackageFingerprint:
    """The fingerprint of the package folder ``root`` (its symlinks resolved first), read with
    the manifest CoGA keeps for the import (``kept_manifest_sha256``) or, given None, with the
    folder's own.

    Raises OSError when the folder cannot be listed, and PackageFolderTooLarge when it holds
    more than PACKAGE_FINGERPRINT_MAX_ENTRIES entries. A name is hashed as the file system
    gives it (a name that is no UTF-8 as its escapes), each record as JSON: no name can make
    two folders' records read the same."""
    folder = os.path.realpath(root)
    found = list(_records(folder))
    records = sorted(json.dumps(record) for record in found)
    digest = hashlib.sha256()
    digest.update(FINGERPRINT_FORMAT.encode("ascii") + b"\n")
    manifest = ["manifest kept in CoGA", kept_manifest_sha256] if kept_manifest_sha256 else ["manifest in folder"]
    digest.update(json.dumps(manifest).encode("ascii") + b"\n")
    for record in records:
        digest.update(record.encode("ascii") + b"\n")
    files = sum(1 for record in found if record[1] == "file")
    return PackageFingerprint(sha256=digest.hexdigest(), files=files)
