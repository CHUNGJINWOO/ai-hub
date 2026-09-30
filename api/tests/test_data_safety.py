import unittest

from app.core.data_safety import (
    FAIL,
    NOT_APPLICABLE,
    PASS,
    UNKNOWN,
    DataSafetyPolicy,
    ObservedDataSafety,
    SequenceObservation,
    SequencePolicy,
    evaluate_data_safety,
)


class DataSafetyTests(unittest.TestCase):
    def test_database_and_schema_identity(self):
        policy = DataSafetyPolicy(expected_database="aihub", expected_schema="public")
        self.assertEqual(
            evaluate_data_safety(policy, ObservedDataSafety("aihub", "public")).safe,
            True,
        )
        result = evaluate_data_safety(
            policy, ObservedDataSafety("other", "private")
        )
        self.assertEqual([check.status for check in result.failures], [FAIL, FAIL])
        self.assertEqual(
            evaluate_data_safety(policy, ObservedDataSafety()).unknowns[0].status,
            UNKNOWN,
        )
        self.assertTrue(evaluate_data_safety(DataSafetyPolicy(), ObservedDataSafety()).safe)

    def test_row_null_and_orphan_policies(self):
        policy = DataSafetyPolicy(
            expected_row_counts={"projects": 3},
            max_null_counts={("documents", "project_id"): 1},
            max_orphan_counts={"documents.project_id->projects.id": 0},
        )
        observed = ObservedDataSafety(
            row_counts={"projects": 3},
            null_counts={("documents", "project_id"): 1},
            orphan_counts={"documents.project_id->projects.id": 0},
        )
        self.assertTrue(evaluate_data_safety(policy, observed).safe)

        failed = evaluate_data_safety(
            policy,
            ObservedDataSafety(
                row_counts={"projects": 2},
                null_counts={("documents", "project_id"): 2},
                orphan_counts={"documents.project_id->projects.id": 1},
            ),
        )
        self.assertEqual(len(failed.failures), 3)
        unknown = evaluate_data_safety(policy, ObservedDataSafety())
        self.assertEqual(len(unknown.unknowns), 3)

    def test_sequence_state_is_policy_driven(self):
        policy = DataSafetyPolicy(
            sequence_policies={
                "projects_id_seq": SequencePolicy(
                    minimum=1, maximum=100, expected_owner="public.projects.id"
                )
            }
        )
        valid = ObservedDataSafety(
            sequence_states={
                "projects_id_seq": SequenceObservation(10, "public.projects.id")
            }
        )
        self.assertTrue(evaluate_data_safety(policy, valid).safe)
        invalid = ObservedDataSafety(
            sequence_states={"projects_id_seq": SequenceObservation(101, "other.id")}
        )
        self.assertFalse(evaluate_data_safety(policy, invalid).safe)
        self.assertEqual(
            evaluate_data_safety(policy, ObservedDataSafety()).unknowns[0].status,
            UNKNOWN,
        )

    def test_backup_and_restore_evidence(self):
        policy = DataSafetyPolicy(
            require_backup_confirmation=True,
            require_restore_rehearsal=True,
        )
        confirmed = ObservedDataSafety(
            backup_confirmed=True,
            backup_evidence="backup-2026-09-30",
            restore_rehearsal_confirmed=True,
            restore_evidence="restore-rehearsal-1",
        )
        self.assertTrue(evaluate_data_safety(policy, confirmed).safe)
        self.assertFalse(
            evaluate_data_safety(
                policy, ObservedDataSafety(backup_confirmed=False)
            ).safe
        )
        self.assertEqual(
            len(evaluate_data_safety(policy, ObservedDataSafety()).unknowns), 2
        )
        self.assertTrue(
            evaluate_data_safety(DataSafetyPolicy(), ObservedDataSafety()).safe
        )

    def test_unknown_is_not_safe_and_order_is_deterministic(self):
        policy = DataSafetyPolicy(
            expected_row_counts={"z": 1, "a": 1},
            max_orphan_counts={"relationship-b": 0, "relationship-a": 0},
        )
        first = evaluate_data_safety(policy, ObservedDataSafety())
        second = evaluate_data_safety(
            DataSafetyPolicy(
                expected_row_counts={"a": 1, "z": 1},
                max_orphan_counts={"relationship-a": 0, "relationship-b": 0},
            ),
            ObservedDataSafety(),
        )
        self.assertFalse(first.safe)
        self.assertEqual(
            [check.check_name for check in first.checks],
            [check.check_name for check in second.checks],
        )

    def test_negative_observations_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_data_safety(
                DataSafetyPolicy(expected_row_counts={"projects": 0}),
                ObservedDataSafety(row_counts={"projects": -1}),
            )


if __name__ == "__main__":
    unittest.main()
