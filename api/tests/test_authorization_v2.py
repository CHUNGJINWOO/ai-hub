"""
Unit tests for Authorization Architecture v2 Core Engine & Types.

Verifies:
A. Context invariant (write_project_ids ⊆ read_project_ids, identity validation)
B. Project read (read_project_ids, PROJECT_READ_ALL)
C. Project write (write_project_ids, read-only denial, PROJECT_ADMIN distinction)
D. Global read (RESOURCE_READ_GLOBAL, PROJECT_READ_ALL isolation)
E. Global write (RESOURCE_WRITE_GLOBAL, RESOURCE_READ_GLOBAL isolation)
F. Project administration (PROJECT_CREATE, PROJECT_ADMIN isolation)
G. Legacy claims mapping (allowed_project_ids, allow_global_read, allow_write)
H. Explicit scope enforcement (ProjectScope, GlobalScope, denial of None)
"""

import dataclasses
import unittest

from app.core.capabilities import Capability
from app.core.scopes import GlobalScope, ProjectScope
from app.core.authorization import (
    AuthorizationContext,
    AuthorizationDenied,
    authorize_read,
    authorize_write,
    authorize_project_create,
    authorize_project_admin,
    claims_to_authorization_context,
)


class AuthorizationContextInvariantTests(unittest.TestCase):
    """Test group A: Context invariant and immutability."""

    def test_write_subset_of_read_succeeds(self):
        ctx = AuthorizationContext(
            identity="agent-1",
            read_project_ids=frozenset({1, 2, 3}),
            write_project_ids=frozenset({1, 2}),
            capabilities=frozenset({Capability.RESOURCE_READ_GLOBAL}),
        )
        self.assertEqual(ctx.identity, "agent-1")
        self.assertEqual(ctx.read_project_ids, frozenset({1, 2, 3}))
        self.assertEqual(ctx.write_project_ids, frozenset({1, 2}))
        self.assertIn(Capability.RESOURCE_READ_GLOBAL, ctx.capabilities)

    def test_write_equal_to_read_succeeds(self):
        ctx = AuthorizationContext(
            identity="agent-1",
            read_project_ids=frozenset({5}),
            write_project_ids=frozenset({5}),
        )
        self.assertEqual(ctx.read_project_ids, frozenset({5}))
        self.assertEqual(ctx.write_project_ids, frozenset({5}))

    def test_empty_write_succeeds(self):
        ctx = AuthorizationContext(
            identity="reader",
            read_project_ids=frozenset({1, 2}),
            write_project_ids=frozenset(),
        )
        self.assertEqual(ctx.write_project_ids, frozenset())

    def test_write_not_subset_of_read_raises_value_error(self):
        with self.assertRaises(ValueError) as cm:
            AuthorizationContext(
                identity="agent-bad",
                read_project_ids=frozenset({1}),
                write_project_ids=frozenset({1, 2}),
            )
        self.assertIn("subset", str(cm.exception))

    def test_empty_identity_raises_value_error(self):
        with self.assertRaises(ValueError) as cm:
            AuthorizationContext(identity="")
        self.assertIn("identity", str(cm.exception))

    def test_none_identity_raises_value_error(self):
        with self.assertRaises(ValueError) as cm:
            AuthorizationContext(identity=None)  # type: ignore[arg-type]
        self.assertIn("identity", str(cm.exception))

    def test_context_is_immutable(self):
        ctx = AuthorizationContext(
            identity="agent-imm",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.identity = "mutated"  # type: ignore[misc]

    def test_legacy_fields_are_not_formal_dataclass_fields(self):
        field_names = {f.name for f in dataclasses.fields(AuthorizationContext)}
        self.assertEqual(
            field_names,
            {"identity", "read_project_ids", "write_project_ids", "capabilities"},
        )
        self.assertNotIn("allowed_project_ids", field_names)
        self.assertNotIn("allow_global_read", field_names)
        self.assertNotIn("allow_write", field_names)


class ProjectReadAuthorizationTests(unittest.TestCase):
    """Test group B: Project read scoping."""

    def test_authorized_project_read_succeeds(self):
        ctx = AuthorizationContext(
            identity="reader",
            read_project_ids=frozenset({10, 20}),
            write_project_ids=frozenset(),
        )
        # Should not raise
        authorize_read(ctx, ProjectScope(10))
        authorize_read(ctx, ProjectScope(20))
        self.assertTrue(ctx.can_read_project(10))
        self.assertTrue(ctx.can_read_project(20))

    def test_unauthorized_project_read_raises_denied(self):
        ctx = AuthorizationContext(
            identity="reader",
            read_project_ids=frozenset({10}),
            write_project_ids=frozenset(),
        )
        with self.assertRaises(AuthorizationDenied) as cm:
            authorize_read(ctx, ProjectScope(99))
        self.assertIn("not authorized to read project 99", str(cm.exception))
        self.assertFalse(ctx.can_read_project(99))

    def test_project_read_all_capability_permits_any_project(self):
        ctx = AuthorizationContext(
            identity="auditor",
            read_project_ids=frozenset(),
            write_project_ids=frozenset(),
            capabilities=frozenset({Capability.PROJECT_READ_ALL}),
        )
        authorize_read(ctx, ProjectScope(10))
        authorize_read(ctx, ProjectScope(999))
        self.assertTrue(ctx.can_read_project(10))
        self.assertTrue(ctx.can_read_project(999))

    def test_without_project_read_all_unscoped_project_denied(self):
        ctx = AuthorizationContext(
            identity="scoped-user",
            read_project_ids=frozenset({1}),
            capabilities=frozenset(),
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_read(ctx, ProjectScope(2))


class ProjectWriteAuthorizationTests(unittest.TestCase):
    """Test group C: Project write scoping."""

    def test_authorized_project_write_succeeds(self):
        ctx = AuthorizationContext(
            identity="writer",
            read_project_ids=frozenset({10, 20}),
            write_project_ids=frozenset({10}),
        )
        authorize_write(ctx, ProjectScope(10))
        self.assertTrue(ctx.can_write_project(10))

    def test_read_only_project_write_raises_denied(self):
        ctx = AuthorizationContext(
            identity="reader",
            read_project_ids=frozenset({10, 20}),
            write_project_ids=frozenset({10}),
        )
        with self.assertRaises(AuthorizationDenied) as cm:
            authorize_write(ctx, ProjectScope(20))
        self.assertIn("not authorized to write project 20", str(cm.exception))
        self.assertFalse(ctx.can_write_project(20))

    def test_project_read_all_does_not_grant_project_write(self):
        ctx = AuthorizationContext(
            identity="auditor",
            read_project_ids=frozenset({10}),
            write_project_ids=frozenset(),
            capabilities=frozenset({Capability.PROJECT_READ_ALL}),
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_write(ctx, ProjectScope(10))
        self.assertFalse(ctx.can_write_project(10))

    def test_project_admin_does_not_grant_general_resource_write(self):
        ctx = AuthorizationContext(
            identity="admin",
            read_project_ids=frozenset({10}),
            write_project_ids=frozenset(),
            capabilities=frozenset({Capability.PROJECT_ADMIN}),
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_write(ctx, ProjectScope(10))
        self.assertFalse(ctx.can_write_project(10))


class GlobalResourceReadTests(unittest.TestCase):
    """Test group D: Global resource read scoping."""

    def test_resource_read_global_permits_global_scope(self):
        ctx = AuthorizationContext(
            identity="global-reader",
            read_project_ids=frozenset(),
            capabilities=frozenset({Capability.RESOURCE_READ_GLOBAL}),
        )
        authorize_read(ctx, GlobalScope())
        self.assertTrue(ctx.can_read_global())

    def test_without_resource_read_global_global_scope_denied(self):
        ctx = AuthorizationContext(
            identity="project-reader",
            read_project_ids=frozenset({1, 2}),
            capabilities=frozenset(),
        )
        with self.assertRaises(AuthorizationDenied) as cm:
            authorize_read(ctx, GlobalScope())
        self.assertIn("global resource read", str(cm.exception))
        self.assertFalse(ctx.can_read_global())

    def test_project_read_all_does_not_grant_global_read(self):
        ctx = AuthorizationContext(
            identity="all-project-reader",
            read_project_ids=frozenset(),
            capabilities=frozenset({Capability.PROJECT_READ_ALL}),
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_read(ctx, GlobalScope())
        self.assertFalse(ctx.can_read_global())

    def test_resource_read_global_does_not_grant_project_read(self):
        ctx = AuthorizationContext(
            identity="global-reader",
            read_project_ids=frozenset(),
            capabilities=frozenset({Capability.RESOURCE_READ_GLOBAL}),
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_read(ctx, ProjectScope(1))
        self.assertFalse(ctx.can_read_project(1))


class GlobalResourceWriteTests(unittest.TestCase):
    """Test group E: Global resource write scoping."""

    def test_resource_write_global_permits_global_write(self):
        ctx = AuthorizationContext(
            identity="global-writer",
            capabilities=frozenset({Capability.RESOURCE_WRITE_GLOBAL}),
        )
        authorize_write(ctx, GlobalScope())
        self.assertTrue(ctx.can_write_global())

    def test_resource_read_global_alone_denies_global_write(self):
        ctx = AuthorizationContext(
            identity="global-reader",
            capabilities=frozenset({Capability.RESOURCE_READ_GLOBAL}),
        )
        with self.assertRaises(AuthorizationDenied) as cm:
            authorize_write(ctx, GlobalScope())
        self.assertIn("global resource write", str(cm.exception))
        self.assertFalse(ctx.can_write_global())

    def test_project_write_does_not_grant_global_write(self):
        ctx = AuthorizationContext(
            identity="project-writer",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_write(ctx, GlobalScope())
        self.assertFalse(ctx.can_write_global())


class ProjectAdministrationTests(unittest.TestCase):
    """Test group F: Project administration separation."""

    def test_project_create_permitted_with_capability(self):
        ctx = AuthorizationContext(
            identity="creator",
            capabilities=frozenset({Capability.PROJECT_CREATE}),
        )
        authorize_project_create(ctx)
        self.assertTrue(ctx.can_create_project())

    def test_project_create_denied_without_capability(self):
        ctx = AuthorizationContext(
            identity="regular-user",
            read_project_ids=frozenset({1}),
            write_project_ids=frozenset({1}),
        )
        with self.assertRaises(AuthorizationDenied) as cm:
            authorize_project_create(ctx)
        self.assertIn("create projects", str(cm.exception))
        self.assertFalse(ctx.can_create_project())

    def test_project_admin_permitted_with_capability(self):
        ctx = AuthorizationContext(
            identity="admin",
            capabilities=frozenset({Capability.PROJECT_ADMIN}),
        )
        authorize_project_admin(ctx, project_id=10)
        self.assertTrue(ctx.can_admin_project(10))

    def test_project_admin_denied_without_capability(self):
        ctx = AuthorizationContext(
            identity="regular-user",
            read_project_ids=frozenset({10}),
            write_project_ids=frozenset({10}),
        )
        with self.assertRaises(AuthorizationDenied) as cm:
            authorize_project_admin(ctx, project_id=10)
        self.assertIn("administer project 10", str(cm.exception))
        self.assertFalse(ctx.can_admin_project(10))

    def test_project_admin_does_not_grant_resource_write_global(self):
        ctx = AuthorizationContext(
            identity="admin",
            capabilities=frozenset({Capability.PROJECT_ADMIN}),
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_write(ctx, GlobalScope())
        self.assertFalse(ctx.can_write_global())


class LegacyClaimsAdapterTests(unittest.TestCase):
    """Test group G: Legacy claims adapter and backward compatibility."""

    def test_allowed_project_ids_maps_to_read_project_ids(self):
        claims = {"allowed_project_ids": [1, 2, 3]}
        ctx = claims_to_authorization_context("agent", claims)
        self.assertEqual(ctx.read_project_ids, frozenset({1, 2, 3}))
        self.assertEqual(ctx.write_project_ids, frozenset())
        self.assertEqual(ctx.capabilities, frozenset())

    def test_allow_global_read_maps_to_resource_read_global(self):
        claims = {"allowed_project_ids": [1], "allow_global_read": True}
        ctx = claims_to_authorization_context("agent", claims)
        self.assertIn(Capability.RESOURCE_READ_GLOBAL, ctx.capabilities)
        self.assertTrue(ctx.can_read_global())

    def test_allow_write_maps_to_write_project_ids_equal_to_read(self):
        claims = {
            "allowed_project_ids": [1, 2],
            "allow_write": True,
        }
        ctx = claims_to_authorization_context("agent", claims)
        self.assertEqual(ctx.read_project_ids, frozenset({1, 2}))
        self.assertEqual(ctx.write_project_ids, frozenset({1, 2}))

    def test_allow_write_does_not_grant_project_create_or_admin(self):
        claims = {
            "allowed_project_ids": [1, 2],
            "allow_write": True,
        }
        ctx = claims_to_authorization_context("agent", claims)
        self.assertNotIn(Capability.PROJECT_CREATE, ctx.capabilities)
        self.assertNotIn(Capability.PROJECT_ADMIN, ctx.capabilities)
        self.assertFalse(ctx.can_create_project())
        self.assertFalse(ctx.can_admin_project(1))
        with self.assertRaises(AuthorizationDenied):
            authorize_project_create(ctx)
        with self.assertRaises(AuthorizationDenied):
            authorize_project_admin(ctx, project_id=1)

    def test_write_subset_of_read_enforced_in_claims(self):
        claims = {
            "read_project_ids": [1],
            "write_project_ids": [1, 2],
        }
        with self.assertRaises(AuthorizationDenied) as cm:
            claims_to_authorization_context("agent", claims)
        self.assertIn("subset", str(cm.exception))

    def test_v2_standard_claims_override_legacy(self):
        claims = {
            "read_project_ids": [10],
            "allowed_project_ids": [99],
            "write_project_ids": [10],
            "allow_write": False,
            "capabilities": ["PROJECT_READ_ALL"],
        }
        ctx = claims_to_authorization_context("agent", claims)
        self.assertEqual(ctx.read_project_ids, frozenset({10}))
        self.assertEqual(ctx.write_project_ids, frozenset({10}))
        self.assertIn(Capability.PROJECT_READ_ALL, ctx.capabilities)


class ExplicitScopeEnforcementTests(unittest.TestCase):
    """Test group H: Explicit scopes and rejection of None."""

    def test_project_scope_rejects_none(self):
        with self.assertRaises(ValueError) as cm:
            ProjectScope(project_id=None)  # type: ignore[arg-type]
        self.assertIn("must not be None", str(cm.exception))

    def test_project_scope_rejects_non_integer(self):
        with self.assertRaises(TypeError):
            ProjectScope(project_id="one")  # type: ignore[arg-type]

    def test_authorize_read_rejects_none_scope(self):
        ctx = AuthorizationContext(identity="user")
        with self.assertRaises(ValueError) as cm:
            authorize_read(ctx, None)  # type: ignore[arg-type]
        self.assertIn("scope must not be None", str(cm.exception))

    def test_authorize_write_rejects_none_scope(self):
        ctx = AuthorizationContext(identity="user")
        with self.assertRaises(ValueError) as cm:
            authorize_write(ctx, None)  # type: ignore[arg-type]
        self.assertIn("scope must not be None", str(cm.exception))

    def test_authorize_read_rejects_invalid_scope_type(self):
        ctx = AuthorizationContext(identity="user")
        with self.assertRaises(TypeError):
            authorize_read(ctx, "project:1")  # type: ignore[arg-type]

    def test_authorize_write_rejects_invalid_scope_type(self):
        ctx = AuthorizationContext(identity="user")
        with self.assertRaises(TypeError):
            authorize_write(ctx, 123)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
