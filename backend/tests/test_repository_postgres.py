"""
Integration tests for the REAL SQL in app/db/repository.py.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres database
where supabase/migrations/0001_init.sql has been applied (with stub
`auth.users` and `storage.buckets` tables). NEVER point this at production:
it inserts and deletes rows.
"""

import os
import uuid

import pytest

from app.db.models import NewDataset, NewVersion
from app.db.repository import PostgresRepository, QuotaExceededError, VersionConflictError, create_pool

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def repo():
    pool = create_pool(DATABASE_URL, max_size=4)
    pool.open(wait=True)
    yield PostgresRepository(pool)
    pool.close()


@pytest.fixture
def user(repo):
    user_id = uuid.uuid4()
    with repo._pool.connection() as conn:
        conn.execute("insert into auth.users (id) values (%s)", (user_id,))
    yield user_id
    with repo._pool.connection() as conn:
        conn.execute("delete from auth.users where id = %s", (user_id,))  # cascades to datasets


def new_dataset(user_id, name="d"):
    dataset_id = uuid.uuid4()
    return NewDataset(
        id=dataset_id, user_id=user_id, name=name, source_filename=f"{name}.xlsx", source_format="xlsx",
        source_path=f"{user_id}/{dataset_id}/source.xlsx", source_size_bytes=1234,
        sheet_name="A", sheet_names=["A", "B"],
    )


def new_version(path="p1.parquet", rows=10, sheet=None):
    return NewVersion(
        parquet_path=path, row_count=rows, column_count=3, size_bytes=500,
        operation={"type": "upload"}, summary={"rows": rows, "suggestions": [{"kind": "x"}]}, sheet_name=sheet,
    )


def test_create_get_list_roundtrip(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version(), max_datasets=5)
    assert created.current_version == 1 and created.sheet_names == ["A", "B"]
    assert repo.get_dataset(user, created.id) == created
    assert [d.id for d in repo.list_datasets(user)] == [created.id]
    version = repo.get_version(user, created.id, 1)
    assert version.summary == {"rows": 10, "suggestions": [{"kind": "x"}]}  # JSONB round-trip
    assert version.operation == {"type": "upload"}


def test_other_user_sees_nothing(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version(), max_datasets=5)
    stranger = uuid.uuid4()
    assert repo.get_dataset(stranger, created.id) is None
    assert repo.get_version(stranger, created.id, 1) is None
    assert repo.rename_dataset(stranger, created.id, "x") is None
    assert repo.delete_dataset(stranger, created.id) is None
    assert repo.list_datasets(stranger) == []


def test_quota(repo, user):
    repo.create_dataset(new_dataset(user, "a"), new_version(), max_datasets=2)
    repo.create_dataset(new_dataset(user, "b"), new_version(), max_datasets=2)
    with pytest.raises(QuotaExceededError):
        repo.create_dataset(new_dataset(user, "c"), new_version(), max_datasets=2)
    assert repo.count_datasets(user) == 2


def test_rename_updates_timestamp(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version(), max_datasets=5)
    renamed = repo.rename_dataset(user, created.id, "New name")
    assert renamed.name == "New name" and renamed.updated_at >= created.updated_at


def test_add_version_conflict_and_prune(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version("v1.parquet"), max_datasets=5)
    updated, orphaned = repo.add_version(user, created.id, 1, new_version("v2.parquet", rows=7, sheet="B"), keep_versions=2)
    assert updated.current_version == 2 and updated.row_count == 7 and updated.sheet_name == "B"
    assert orphaned == []

    with pytest.raises(VersionConflictError) as info:
        repo.add_version(user, created.id, 1, new_version("x.parquet"), keep_versions=2)
    assert info.value.details == {"current_version": 2}

    # v3 reuses v1's file (what "revert" will do in Phase 4): pruning v1 must NOT orphan it
    _, orphaned = repo.add_version(user, created.id, 2, new_version("v1.parquet"), keep_versions=2)
    assert orphaned == []
    _, orphaned = repo.add_version(user, created.id, 3, new_version("v4.parquet"), keep_versions=2)
    assert orphaned == ["v2.parquet"]
    assert repo.get_version(user, created.id, 2) is None
    assert repo.get_version(user, created.id, 3).parquet_path == "v1.parquet"


def test_delete_returns_all_paths_and_cascades(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version("v1.parquet"), max_datasets=5)
    repo.add_version(user, created.id, 1, new_version("v2.parquet"), keep_versions=5)
    paths = repo.delete_dataset(user, created.id)
    assert sorted(paths) == sorted([created.source_path, "v1.parquet", "v2.parquet"])
    assert repo.get_dataset(user, created.id) is None
    with repo._pool.connection() as conn:
        assert conn.execute("select count(*) from public.dataset_versions where dataset_id = %s", (created.id,)).fetchone()[0] == 0


def test_usage_counts_shared_files_once(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version("v1.parquet"), max_datasets=5)
    repo.add_version(user, created.id, 1, new_version("v1.parquet"), keep_versions=5)  # same file twice
    usage = repo.usage(user)
    assert usage.datasets == 1
    assert usage.storage_bytes == 1234 + 500


def test_list_versions_newest_first_and_owned(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version("v1.parquet"), max_datasets=5)
    repo.add_version(user, created.id, 1, new_version("v2.parquet"), keep_versions=5)
    assert [v.version for v in repo.list_versions(user, created.id)] == [2, 1]
    assert repo.list_versions(uuid.uuid4(), created.id) == []


def test_analysis_cache_roundtrip_and_upsert(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version(), max_datasets=5)
    assert repo.get_analysis(created.id, 1, "overview", "") is None
    repo.put_analysis(created.id, 1, "overview", "", {"rows": 10, "nested": {"a": [1, None]}})
    repo.put_analysis(created.id, 1, "overview", "", {"rows": 11})
    assert repo.get_analysis(created.id, 1, "overview", "") == {"rows": 11}
    repo.delete_dataset(user, created.id)  # cascades
    assert repo.get_analysis(created.id, 1, "overview", "") is None


def test_dataset_with_version_in_one_query(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version("v1.parquet"), max_datasets=5)
    repo.add_version(user, created.id, 1, new_version("v2.parquet", rows=7), keep_versions=5)
    dataset, current = repo.get_dataset_with_version(user, created.id)
    assert dataset.current_version == 2 and current.version == 2 and current.parquet_path == "v2.parquet"
    assert current.summary["rows"] == 7 and current.created_at is not None
    _, old = repo.get_dataset_with_version(user, created.id, 1)
    assert old.parquet_path == "v1.parquet"
    assert repo.get_dataset_with_version(user, created.id, 99)[1] is None
    assert repo.get_dataset_with_version(uuid.uuid4(), created.id) is None


def test_read_survives_a_dropped_connection(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version(), max_datasets=5)
    # kill every other backend connection, as a pooler restart would
    with repo._pool.connection() as conn:
        conn.execute("select pg_terminate_backend(pid) from pg_stat_activity where pid <> pg_backend_pid() and datname = current_database()")
    assert repo.get_dataset_with_version(user, created.id) is not None


def test_concurrent_creates_cannot_exceed_quota(repo, user):
    from concurrent.futures import ThreadPoolExecutor

    def attempt(i):
        try:
            repo.create_dataset(new_dataset(user, f"c{i}"), new_version(), max_datasets=3)
            return True
        except QuotaExceededError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(results) == 3
    assert repo.count_datasets(user) == 3


def test_checks_roundtrip_and_ownership(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version(), max_datasets=5)
    assert repo.get_checks(user, created.id) == []  # default from migration 0002
    saved = [{"id": "age", "name": "Age is sane", "conditions": [{"column": "age", "op": "lt", "value": 0}], "combine": "and"}]
    assert repo.set_checks(user, created.id, saved) == saved
    assert repo.get_checks(user, created.id) == saved
    stranger = uuid.uuid4()
    assert repo.get_checks(stranger, created.id) is None
    assert repo.set_checks(stranger, created.id, []) is None
    assert repo.get_checks(user, created.id) == saved


def test_context_and_reviewed_roundtrip(repo, user):
    created = repo.create_dataset(new_dataset(user), new_version(), max_datasets=5)
    assert repo.get_context(user, created.id) == {"checks": [], "reviewed": []}
    marks = [{"issue_id": "outliers:x", "fingerprint": "a" * 16, "title": "t", "note": None}]
    assert repo.set_reviewed(user, created.id, marks) == marks
    assert repo.get_context(user, created.id)["reviewed"] == marks
    assert repo.get_context(uuid.uuid4(), created.id) is None
    assert repo.set_reviewed(uuid.uuid4(), created.id, []) is None
