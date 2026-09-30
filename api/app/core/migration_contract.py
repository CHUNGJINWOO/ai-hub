"""Validation primitives for the future versioned schema migration system.

This module deliberately has no database dependency. It validates migration
filenames, ordering, checksums, and recorded metadata without applying SQL.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


_MIGRATION_FILENAME = re.compile(
    r"^(?P<revision>[0-9]{4})_(?P<name>[a-z][a-z0-9]*(?:_[a-z0-9]+)*)\.sql$"
)
_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")


class MigrationContractError(ValueError):
    """Raised when migration files or metadata violate the contract."""


@dataclass(frozen=True)
class MigrationFile:
    revision: int
    name: str
    path: Path
    checksum: str


@dataclass(frozen=True)
class MigrationMetadata:
    version: int
    applied_at: str
    checksum: str


def parse_migration_filename(path: str | Path) -> tuple[int, str]:
    """Return the revision and slug encoded by a migration filename."""
    filename = Path(path).name
    match = _MIGRATION_FILENAME.fullmatch(filename)
    if match is None:
        raise MigrationContractError(
            "migration filename must match NNNN_slug.sql: "
            f"{filename}"
        )

    revision = int(match.group("revision"))
    if revision == 0:
        raise MigrationContractError("migration revision must be positive")

    return revision, match.group("name")


def calculate_checksum(path: str | Path) -> str:
    """Calculate the SHA-256 checksum of a migration file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_migration_files(
    paths: Iterable[str | Path],
    *,
    allow_gaps: bool = False,
) -> tuple[MigrationFile, ...]:
    """Parse, checksum, sort, and validate a set of migration files."""
    migrations: list[MigrationFile] = []
    seen_revisions: set[int] = set()

    for raw_path in paths:
        path = Path(raw_path)
        revision, name = parse_migration_filename(path)
        if revision in seen_revisions:
            raise MigrationContractError(
                f"duplicate migration revision: {revision:04d}"
            )
        if not path.is_file():
            raise MigrationContractError(f"migration file not found: {path}")

        seen_revisions.add(revision)
        migrations.append(
            MigrationFile(
                revision=revision,
                name=name,
                path=path,
                checksum=calculate_checksum(path),
            )
        )

    migrations.sort(key=lambda migration: migration.revision)
    validate_migration_chain(migrations, allow_gaps=allow_gaps)
    return tuple(migrations)


def validate_migration_chain(
    migrations: Iterable[MigrationFile],
    *,
    allow_gaps: bool = False,
) -> None:
    """Validate uniqueness, ordering, and the default contiguous numbering."""
    ordered = list(migrations)
    revisions = [migration.revision for migration in ordered]

    if revisions != sorted(revisions):
        raise MigrationContractError(
            "migration revisions must be provided in ascending order"
        )
    if len(revisions) != len(set(revisions)):
        raise MigrationContractError("migration revisions must be unique")
    if not allow_gaps and revisions:
        expected = list(range(1, revisions[-1] + 1))
        if revisions != expected:
            raise MigrationContractError(
                "migration revisions must be contiguous from 0001"
            )


def validate_metadata(metadata: MigrationMetadata) -> None:
    """Validate one row from the logical schema migration metadata table."""
    if metadata.version <= 0:
        raise MigrationContractError("metadata version must be positive")
    if not metadata.applied_at.strip():
        raise MigrationContractError("metadata applied_at must not be empty")
    if _CHECKSUM.fullmatch(metadata.checksum) is None:
        raise MigrationContractError(
            "metadata checksum must be a lowercase SHA-256 hex digest"
        )


def validate_applied_migrations(
    migrations: Iterable[MigrationFile],
    applied: Mapping[int, MigrationMetadata],
) -> None:
    """Ensure recorded revisions exist and have immutable checksums."""
    by_revision = {migration.revision: migration for migration in migrations}

    for metadata in applied.values():
        validate_metadata(metadata)
        migration = by_revision.get(metadata.version)
        if migration is None:
            raise MigrationContractError(
                f"applied migration is not present on disk: "
                f"{metadata.version:04d}"
            )
        if migration.checksum != metadata.checksum:
            raise MigrationContractError(
                f"migration checksum mismatch: {metadata.version:04d}"
            )
