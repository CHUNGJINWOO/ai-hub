import tempfile
import unittest
from pathlib import Path

from app.core.migration_contract import (
    MigrationContractError,
    MigrationMetadata,
    calculate_checksum,
    load_migration_files,
    parse_migration_filename,
    validate_applied_migrations,
)


class MigrationContractTests(unittest.TestCase):
    def write_migrations(self, directory: Path, names: list[str]) -> list[Path]:
        paths = []
        for name in names:
            path = directory / name
            path.write_text(f"-- {name}\n", encoding="utf-8")
            paths.append(path)
        return paths

    def test_valid_chain_is_sorted_and_checksums_are_calculated(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            paths = self.write_migrations(
                directory,
                ["0002_workspace.sql", "0001_baseline.sql"],
            )

            migrations = load_migration_files(paths)

            self.assertEqual(
                [migration.revision for migration in migrations],
                [1, 2],
            )
            self.assertEqual(
                migrations[0].checksum,
                calculate_checksum(directory / "0001_baseline.sql"),
            )

    def test_invalid_filename_is_rejected(self):
        with self.assertRaises(MigrationContractError):
            parse_migration_filename("foo.sql")

    def test_duplicate_revision_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            paths = self.write_migrations(
                Path(raw_directory),
                ["0001_baseline.sql", "0001_other.sql"],
            )

            with self.assertRaises(MigrationContractError):
                load_migration_files(paths)

    def test_gap_is_rejected_by_default(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            paths = self.write_migrations(
                Path(raw_directory),
                ["0001_baseline.sql", "0003_workspace.sql"],
            )

            with self.assertRaises(MigrationContractError):
                load_migration_files(paths)

    def test_gap_can_be_explicitly_allowed(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            paths = self.write_migrations(
                Path(raw_directory),
                ["0001_baseline.sql", "0003_workspace.sql"],
            )

            migrations = load_migration_files(paths, allow_gaps=True)

            self.assertEqual(
                [migration.revision for migration in migrations],
                [1, 3],
            )

    def test_applied_metadata_with_same_checksum_is_valid(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            path = self.write_migrations(
                Path(raw_directory),
                ["0001_baseline.sql"],
            )[0]
            migration = load_migration_files([path])[0]
            metadata = MigrationMetadata(
                version=1,
                applied_at="2026-09-30T08:00:00Z",
                checksum=migration.checksum,
            )

            validate_applied_migrations([migration], {1: metadata})

    def test_changed_file_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            path = self.write_migrations(
                Path(raw_directory),
                ["0001_baseline.sql"],
            )[0]
            migration = load_migration_files([path])[0]
            metadata = MigrationMetadata(
                version=1,
                applied_at="2026-09-30T08:00:00Z",
                checksum="0" * 64,
            )

            with self.assertRaises(MigrationContractError):
                validate_applied_migrations([migration], {1: metadata})

    def test_unknown_applied_revision_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            path = self.write_migrations(
                Path(raw_directory),
                ["0001_baseline.sql"],
            )[0]
            metadata = MigrationMetadata(
                version=2,
                applied_at="2026-09-30T08:00:00Z",
                checksum="0" * 64,
            )

            with self.assertRaises(MigrationContractError):
                validate_applied_migrations(
                    load_migration_files([path]),
                    {2: metadata},
                )

    def test_invalid_metadata_checksum_is_rejected(self):
        with self.assertRaises(MigrationContractError):
            validate_applied_migrations(
                [],
                {
                    1: MigrationMetadata(
                        version=1,
                        applied_at="2026-09-30T08:00:00Z",
                        checksum="not-a-checksum",
                    )
                },
            )


if __name__ == "__main__":
    unittest.main()
