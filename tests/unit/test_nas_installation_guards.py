"""Trust and cluster-role guards must run before migrations or password changes."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from scripts import initialize_postgres as installer


def _migration(version=1):
    item = next(
        m
        for m in installer.load_migrations(installer.ROOT / "migrations")
        if m.version == version
    )
    return {
        "version": item.version,
        "filename": item.path.name,
        "checksum_sha256": item.checksum,
    }


LEDGER_COLUMNS = [
    ("version", "integer", True),
    ("filename", "text", True),
    ("checksum_sha256", "character(64)", True),
    ("applied_at", "timestamp with time zone", True),
]
MARKER_COLUMNS = [
    ("singleton", "boolean", True),
    ("initial_cleanup_done", "boolean", True),
    ("created_at", "timestamp with time zone", True),
]


class TargetCatalog:
    def __init__(self, *, ledger=False, marker=False, applied=None, marker_done=False):
        self.ledger, self.marker = ledger, marker
        self.applied = [] if applied is None else deepcopy(applied)
        self.marker_state = {
            "row_count": 1,
            "singleton_valid": True,
            "cleanup_done": marker_done,
        }
        self.structure = {
            "is_table": True,
            "owner_matches": True,
            "primary_key_valid": True,
            "singleton_check_valid": True,
        }
        self.columns = {"ledger": LEDGER_COLUMNS[:], "marker": MARKER_COLUMNS[:]}
        self.ddl_present = True
        self.other_objects = 0
        self.statements = []

    async def fetchval(self, sql, *args):
        self.statements.append(sql)
        if "to_regclass('public.wealthsail_schema_migrations')" in sql:
            return self.ledger
        if "to_regclass('public.wealthsail_nas_installation')" in sql:
            return self.marker
        if "auth.uid()" in sql:
            return self.ddl_present
        return self.other_objects

    async def fetchrow(self, sql, *args):
        self.statements.append(sql)
        if "row_count" in sql:
            return self.marker_state
        return self.structure

    async def fetch(self, sql, *args):
        self.statements.append(sql)
        if "pg_attribute" in sql:
            kind = "marker" if "nas_installation" in str(args) else "ledger"
            return [
                dict(column_name=name, data_type=kind_, not_null=not_null)
                for name, kind_, not_null in self.columns[kind]
            ]
        return self.applied


@pytest.mark.asyncio
async def test_empty_dry_run_ledger_does_not_establish_trust():
    with pytest.raises(RuntimeError, match="empty|untrusted"):
        await installer._inspect_existing_target(TargetCatalog(ledger=True))


@pytest.mark.asyncio
async def test_real_initial_migration_and_ddl_establish_legacy_trust():
    assert await installer._inspect_existing_target(
        TargetCatalog(ledger=True, applied=[_migration()])
    ) == (True, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"checksum_sha256": "a" * 64},
        {"filename": "001_unrelated.sql"},
    ],
)
async def test_legacy_ledger_requires_the_initial_migration_and_matching_history(
    change,
):
    row = {**_migration(), **change}
    with pytest.raises(RuntimeError, match="ledger|migration"):
        await installer._inspect_existing_target(
            TargetCatalog(ledger=True, applied=[row])
        )


@pytest.mark.asyncio
async def test_initial_migration_record_requires_its_schema_ddl():
    conn = TargetCatalog(ledger=True, applied=[_migration()])
    conn.ddl_present = False
    with pytest.raises(RuntimeError, match="schema|DDL|ledger"):
        await installer._inspect_existing_target(conn)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"is_table": False},
        {"owner_matches": False},
        {"primary_key_valid": False},
    ],
)
async def test_ledger_must_be_an_owned_table_with_expected_structure(change):
    conn = TargetCatalog(ledger=True, applied=[_migration()])
    conn.structure.update(change)
    with pytest.raises(RuntimeError, match="structure|owner|trusted"):
        await installer._inspect_existing_target(conn)


@pytest.mark.asyncio
async def test_a_ledger_with_different_columns_is_not_trusted():
    conn = TargetCatalog(ledger=True, applied=[_migration()])
    conn.columns["ledger"][2] = ("checksum_sha256", "text", True)
    with pytest.raises(RuntimeError, match="structure|column"):
        await installer._inspect_existing_target(conn)


@pytest.mark.asyncio
@pytest.mark.parametrize("ledger", [False, True])
async def test_owned_pending_marker_allows_retry_before_first_migration(ledger):
    assert await installer._inspect_existing_target(
        TargetCatalog(ledger=ledger, marker=True)
    ) == (True, True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"owner_matches": False},
        {"is_table": False},
        {"primary_key_valid": False},
        {"singleton_check_valid": False},
    ],
)
async def test_untrusted_marker_never_authorizes_installation(change):
    conn = TargetCatalog(marker=True)
    conn.structure.update(change)
    with pytest.raises(RuntimeError, match="structure|owner|trusted"):
        await installer._inspect_existing_target(conn)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"row_count": 0},
        {"row_count": 2},
        {"singleton_valid": False},
    ],
)
async def test_marker_requires_exactly_one_true_singleton_row(change):
    conn = TargetCatalog(marker=True)
    conn.marker_state.update(change)
    with pytest.raises(RuntimeError, match="singleton|marker"):
        await installer._inspect_existing_target(conn)


@pytest.mark.asyncio
async def test_completed_marker_requires_migration_history():
    with pytest.raises(RuntimeError, match="ledger|migration|marker"):
        await installer._inspect_existing_target(
            TargetCatalog(marker=True, marker_done=True)
        )


def _role(name, **overrides):
    return {
        "rolname": name,
        "rolcanlogin": name.endswith("_login"),
        "rolinherit": name in {"wealthsail_api", "authenticated"},
        "rolbypassrls": name == "wealthsail_worker",
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolreplication": False,
        **overrides,
    }


def _membership(member, parent, **overrides):
    return {
        "member_name": member,
        "role_name": parent,
        "set_option": True,
        "inherit_option": member == "wealthsail_api",
        "admin_option": False,
        **overrides,
    }


def _memberships():
    return [
        _membership("wealthsail_api", "authenticated"),
        _membership("wealthsail_worker", "wealthsail_api"),
        _membership("wealthsail_api_login", "wealthsail_api"),
        _membership("wealthsail_worker_login", "wealthsail_worker"),
    ]


class RoleCatalog:
    def __init__(
        self, *, roles=None, memberships=None, unsafe_auth=False, dependencies=None
    ):
        self.roles = (
            [_role(name) for name in installer.DEDICATED_ROLES]
            if roles is None
            else roles
        )
        self.memberships = _memberships() if memberships is None else memberships
        self.unsafe_auth = unsafe_auth
        self.dependencies = dependencies or []
        self.statements = []

    async def fetchval(self, sql, *args):
        self.statements.append(sql)
        if (
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated')"
            in sql
        ):
            return True
        # Return a dangerous predefined-role path only to a check that actually
        # examines SET/USAGE reachability beyond the five direct role attributes.
        if "USAGE" in sql and "r.oid <>" in sql:
            return self.unsafe_auth
        return False

    async def fetch(self, sql, *args):
        self.statements.append(sql)
        if "pg_shdepend" in sql:
            return self.dependencies
        if "pg_auth_members" in sql:
            return self.memberships
        return self.roles


@pytest.mark.asyncio
async def test_authenticated_cannot_reach_a_privileged_predefined_role():
    with pytest.raises(RuntimeError, match="authenticated.*unsafe"):
        await installer._validate_authenticated_role(RoleCatalog(unsafe_auth=True))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,attribute",
    [
        ("wealthsail_api", "rolbypassrls"),
        ("wealthsail_api", "rolsuper"),
        ("wealthsail_worker", "rolsuper"),
        ("wealthsail_api_login", "rolcreatedb"),
        ("wealthsail_worker_login", "rolcreaterole"),
        ("wealthsail_worker_login", "rolreplication"),
    ],
)
async def test_existing_unsafe_role_attributes_are_rejected_before_repair(
    name, attribute
):
    roles = [
        _role(role, **{attribute: True}) if role == name else _role(role)
        for role in installer.DEDICATED_ROLES
    ]
    with pytest.raises(RuntimeError, match="unsafe|attribute"):
        await installer._validate_dedicated_roles(RoleCatalog(roles=roles))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        _membership("wealthsail_api", "pg_execute_server_program"),
        _membership("wealthsail_worker", "pg_read_server_files"),
        _membership("wealthsail_api_login", "wealthsail_worker"),
        _membership("wealthsail_worker_login", "wealthsail_api"),
    ],
)
async def test_only_the_documented_direct_memberships_are_allowed(extra):
    with pytest.raises(RuntimeError, match="membership"):
        await installer._validate_dedicated_roles(
            RoleCatalog(memberships=[*_memberships(), extra])
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"admin_option": True},
        {"set_option": False},
        {"inherit_option": True},
    ],
)
async def test_runtime_login_memberships_keep_set_without_inherit_or_admin(options):
    memberships = _memberships()
    memberships[2].update(options)
    with pytest.raises(RuntimeError, match="membership"):
        await installer._validate_dedicated_roles(RoleCatalog(memberships=memberships))


@pytest.mark.asyncio
async def test_safe_partial_roles_can_finish_migration_and_provisioning():
    await installer._validate_dedicated_roles(
        RoleCatalog(roles=[_role("wealthsail_api")], memberships=[])
    )


@pytest.mark.asyncio
async def test_only_pre_013_retries_can_keep_the_initial_worker_inherit_attribute():
    conn = RoleCatalog(
        roles=[_role("wealthsail_worker", rolinherit=True)], memberships=[]
    )
    with pytest.raises(RuntimeError, match="attribute|unsafe"):
        await installer._validate_dedicated_roles(conn)
    await installer._validate_dedicated_roles(conn, allow_initial_worker_inherit=True)


@pytest.mark.asyncio
async def test_post_provision_readback_requires_all_roles_and_memberships():
    with pytest.raises(RuntimeError, match="missing|incomplete"):
        await installer._validate_dedicated_roles(
            RoleCatalog(roles=[], memberships=[]), require_complete=True
        )
    await installer._validate_dedicated_roles(RoleCatalog(), require_complete=True)


@pytest.mark.asyncio
async def test_database_acl_and_ordinary_cross_database_dependencies_are_rejected():
    conn = RoleCatalog(
        dependencies=[
            {"rolname": "wealthsail_api_login", "database_name": "prismscore_db"}
        ]
    )
    with pytest.raises(RuntimeError, match="other database|cluster role"):
        await installer._validate_role_database_dependencies(conn, "wealthsail")
    query = "\n".join(conn.statements)
    assert "pg_shdepend" in query
    assert "pg_database" in query and "objid" in query and "dbid" in query


@pytest.mark.asyncio
async def test_cluster_dependency_failure_precedes_migrate_and_password_mutations(
    monkeypatch,
):
    maintenance = RoleCatalog()
    maintenance.execute = AsyncMock()
    maintenance.close = AsyncMock()
    maintenance.fetchval = AsyncMock(side_effect=[True, "180000", True])
    target = TargetCatalog(ledger=True, applied=[_migration(), _migration(13)])
    target.close = AsyncMock()
    monkeypatch.setattr(
        installer.asyncpg, "connect", AsyncMock(side_effect=[maintenance, target])
    )
    monkeypatch.setattr(installer, "_validate_authenticated_role", AsyncMock())
    monkeypatch.setattr(installer, "_validate_dedicated_roles", AsyncMock())
    monkeypatch.setattr(
        installer,
        "_validate_role_database_dependencies",
        AsyncMock(side_effect=RuntimeError("cluster role belongs to other database")),
        raising=False,
    )
    migrate = AsyncMock()
    monkeypatch.setattr(installer, "migrate", migrate)
    with pytest.raises(RuntimeError, match="other database"):
        await installer.initialize_database(
            "postgresql://wealthsail_api_login:api-secret@postgres/wealthsail",
            "postgresql://wealthsail_worker_login:worker-secret@postgres/wealthsail",
            admin_user="admin",
            admin_password="secret",
            confirm_target="postgres:5432/wealthsail",
        )
    migrate.assert_not_awaited()
    assert not any(
        "ALTER ROLE" in call.args[0] for call in maintenance.execute.call_args_list
    )
