"""
Database access. All SQL lives in this one file.

Every query that touches a dataset filters by user_id, so one user can never
read or change another user's data even if they guess an id. Not-found and
not-yours look identical from the outside (404).
"""

from typing import Protocol
from uuid import UUID

import logging

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.core.errors import AppError, ConflictError
from app.db.models import DatasetRecord, NewDataset, NewVersion, Usage, VersionRecord

logger = logging.getLogger("app.db")


class QuotaExceededError(AppError):
    status_code = 403
    code = "quota_exceeded"


class VersionConflictError(ConflictError):
    code = "version_conflict"

    def __init__(self, current_version: int) -> None:
        super().__init__(
            "This dataset was changed in another tab or request. Reload to see the latest version.",
            details={"current_version": current_version},
        )


class DatasetRepository(Protocol):
    def count_datasets(self, user_id: UUID) -> int: ...
    def usage(self, user_id: UUID) -> Usage: ...
    def create_dataset(self, dataset: NewDataset, version: NewVersion, max_datasets: int) -> DatasetRecord: ...
    def list_datasets(self, user_id: UUID) -> list[DatasetRecord]: ...
    def get_dataset(self, user_id: UUID, dataset_id: UUID) -> DatasetRecord | None: ...
    def get_version(self, user_id: UUID, dataset_id: UUID, version: int) -> VersionRecord | None: ...
    def get_dataset_with_version(
        self, user_id: UUID, dataset_id: UUID, version: int | None = None
    ) -> tuple[DatasetRecord, VersionRecord | None] | None: ...
    def rename_dataset(self, user_id: UUID, dataset_id: UUID, name: str) -> DatasetRecord | None: ...
    def delete_dataset(self, user_id: UUID, dataset_id: UUID) -> list[str] | None: ...
    def add_version(
        self, user_id: UUID, dataset_id: UUID, expected_version: int, version: NewVersion, keep_versions: int
    ) -> tuple[DatasetRecord, list[str]]: ...
    def list_versions(self, user_id: UUID, dataset_id: UUID) -> list[VersionRecord]: ...
    def get_analysis(self, dataset_id: UUID, version: int, kind: str, params_hash: str) -> dict | None: ...
    def put_analysis(self, dataset_id: UUID, version: int, kind: str, params_hash: str, result: dict) -> None: ...


_DATASET_COLUMNS = """
    id, user_id, name, source_filename, source_format, source_path, source_size_bytes,
    sheet_name, sheet_names, current_version, status, row_count, column_count, created_at, updated_at
"""
_VERSION_COLUMNS = """
    dataset_id, version, parquet_path, row_count, column_count, size_bytes, operation, summary, created_at
"""


def _dataset(row: dict) -> DatasetRecord:
    return DatasetRecord(**row)


def _version(row: dict) -> VersionRecord:
    return VersionRecord(**row)


class PostgresRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _read(self, query):
        """Run a read-only query; retry once if the pooled connection was dropped by the server."""
        try:
            with self._pool.connection() as conn:
                return query(conn)
        except psycopg.OperationalError:
            logger.warning("database connection dropped; retrying read once")
            with self._pool.connection() as conn:
                return query(conn)

    def count_datasets(self, user_id: UUID) -> int:
        with self._pool.connection() as conn:
            row = conn.execute("select count(*) from public.datasets where user_id = %s", (user_id,)).fetchone()
        return int(row[0])

    def usage(self, user_id: UUID) -> Usage:
        sql = """
            select
              (select count(*) from public.datasets where user_id = %(u)s) as datasets,
              coalesce((select sum(d.source_size_bytes) from public.datasets d where d.user_id = %(u)s), 0)
              + coalesce((
                  select sum(t.size_bytes) from (
                    select distinct on (v.parquet_path) v.size_bytes
                    from public.dataset_versions v join public.datasets d on d.id = v.dataset_id
                    where d.user_id = %(u)s
                  ) t), 0) as storage_bytes
        """
        with self._pool.connection() as conn:
            row = conn.execute(sql, {"u": user_id}).fetchone()
        return Usage(datasets=int(row[0]), storage_bytes=int(row[1]))

    def create_dataset(self, dataset: NewDataset, version: NewVersion, max_datasets: int) -> DatasetRecord:
        with self._pool.connection() as conn, conn.transaction():
            # Serialise concurrent uploads by the same user so the quota can't be raced.
            conn.execute("select pg_advisory_xact_lock(hashtext(%s::text))", (str(dataset.user_id),))
            count = conn.execute(
                "select count(*) from public.datasets where user_id = %s", (dataset.user_id,)
            ).fetchone()[0]
            if count >= max_datasets:
                raise QuotaExceededError(
                    f"You can keep up to {max_datasets} datasets. Delete one to upload another.",
                    details={"limit": max_datasets},
                )
            cur = conn.cursor(row_factory=dict_row)
            created = cur.execute(
                f"""
                insert into public.datasets
                  (id, user_id, name, source_filename, source_format, source_path, source_size_bytes,
                   sheet_name, sheet_names, current_version, status, row_count, column_count)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 'ready', %s, %s)
                returning {_DATASET_COLUMNS}
                """,
                (
                    dataset.id, dataset.user_id, dataset.name, dataset.source_filename, dataset.source_format,
                    dataset.source_path, dataset.source_size_bytes, dataset.sheet_name, dataset.sheet_names,
                    version.row_count, version.column_count,
                ),
            ).fetchone()
            self._insert_version(conn, dataset.id, 1, version)
        return _dataset(created)

    def list_datasets(self, user_id: UUID) -> list[DatasetRecord]:
        with self._pool.connection() as conn:
            rows = conn.cursor(row_factory=dict_row).execute(
                f"select {_DATASET_COLUMNS} from public.datasets where user_id = %s order by updated_at desc",
                (user_id,),
            ).fetchall()
        return [_dataset(r) for r in rows]

    def get_dataset(self, user_id: UUID, dataset_id: UUID) -> DatasetRecord | None:
        with self._pool.connection() as conn:
            row = conn.cursor(row_factory=dict_row).execute(
                f"select {_DATASET_COLUMNS} from public.datasets where id = %s and user_id = %s",
                (dataset_id, user_id),
            ).fetchone()
        return _dataset(row) if row else None

    def get_version(self, user_id: UUID, dataset_id: UUID, version: int) -> VersionRecord | None:
        with self._pool.connection() as conn:
            row = conn.cursor(row_factory=dict_row).execute(
                f"""
                select {", ".join("v." + c.strip() for c in _VERSION_COLUMNS.split(","))}
                from public.dataset_versions v join public.datasets d on d.id = v.dataset_id
                where v.dataset_id = %s and v.version = %s and d.user_id = %s
                """,
                (dataset_id, version, user_id),
            ).fetchone()
        return _version(row) if row else None

    def get_dataset_with_version(
        self, user_id: UUID, dataset_id: UUID, version: int | None = None
    ) -> tuple[DatasetRecord, VersionRecord | None] | None:
        """Dataset + one version (default: current) in a SINGLE query: every read starts here."""
        dataset_cols = ", ".join("d." + c.strip() for c in _DATASET_COLUMNS.split(","))
        version_cols = ", ".join(f"v.{c.strip()} as v_{c.strip()}" for c in _VERSION_COLUMNS.split(","))
        sql = f"""
            select {dataset_cols}, {version_cols}
            from public.datasets d
            left join public.dataset_versions v
              on v.dataset_id = d.id and v.version = coalesce(%(version)s, d.current_version)
            where d.id = %(id)s and d.user_id = %(user)s
        """

        def query(conn):
            return conn.cursor(row_factory=dict_row).execute(
                sql, {"id": dataset_id, "user": user_id, "version": version}
            ).fetchone()

        row = self._read(query)
        if row is None:
            return None
        version_fields = {k[2:]: row.pop(k) for k in list(row) if k.startswith("v_")}
        record = _version(version_fields) if version_fields["version"] is not None else None
        return _dataset(row), record

    def rename_dataset(self, user_id: UUID, dataset_id: UUID, name: str) -> DatasetRecord | None:
        with self._pool.connection() as conn:
            row = conn.cursor(row_factory=dict_row).execute(
                f"update public.datasets set name = %s where id = %s and user_id = %s returning {_DATASET_COLUMNS}",
                (name, dataset_id, user_id),
            ).fetchone()
        return _dataset(row) if row else None

    def delete_dataset(self, user_id: UUID, dataset_id: UUID) -> list[str] | None:
        """Delete the rows; return every storage path that belonged to the dataset."""
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "select source_path from public.datasets where id = %s and user_id = %s for update",
                (dataset_id, user_id),
            ).fetchone()
            if row is None:
                return None
            paths = [row[0]] + [
                r[0]
                for r in conn.execute(
                    "select distinct parquet_path from public.dataset_versions where dataset_id = %s", (dataset_id,)
                ).fetchall()
            ]
            conn.execute("delete from public.datasets where id = %s", (dataset_id,))  # versions + cache cascade
        return paths

    def add_version(
        self, user_id: UUID, dataset_id: UUID, expected_version: int, version: NewVersion, keep_versions: int
    ) -> tuple[DatasetRecord, list[str]]:
        """
        Optimistic concurrency: succeeds only if the dataset is still at
        `expected_version`. Then prunes old versions beyond `keep_versions` and
        returns the Parquet paths that no remaining version references.
        """
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "select current_version from public.datasets where id = %s and user_id = %s for update",
                (dataset_id, user_id),
            ).fetchone()
            if row is None:
                raise _not_found()
            current = row[0]
            if current != expected_version:
                raise VersionConflictError(current)

            new_number = current + 1
            self._insert_version(conn, dataset_id, new_number, version)
            updated = conn.cursor(row_factory=dict_row).execute(
                f"""
                update public.datasets
                set current_version = %s, row_count = %s, column_count = %s,
                    sheet_name = coalesce(%s, sheet_name)
                where id = %s
                returning {_DATASET_COLUMNS}
                """,
                (new_number, version.row_count, version.column_count, version.sheet_name, dataset_id),
            ).fetchone()

            pruned = conn.execute(
                "delete from public.dataset_versions where dataset_id = %s and version <= %s returning parquet_path",
                (dataset_id, new_number - keep_versions),
            ).fetchall()
            orphaned: list[str] = []
            if pruned:
                still_used = {
                    r[0]
                    for r in conn.execute(
                        "select parquet_path from public.dataset_versions where dataset_id = %s", (dataset_id,)
                    ).fetchall()
                }
                orphaned = sorted({r[0] for r in pruned} - still_used)
                conn.execute(
                    "delete from public.analysis_cache where dataset_id = %s and version <= %s",
                    (dataset_id, new_number - keep_versions),
                )
        return _dataset(updated), orphaned

    def list_versions(self, user_id: UUID, dataset_id: UUID) -> list[VersionRecord]:
        with self._pool.connection() as conn:
            rows = conn.cursor(row_factory=dict_row).execute(
                f"""
                select {", ".join("v." + c.strip() for c in _VERSION_COLUMNS.split(","))}
                from public.dataset_versions v join public.datasets d on d.id = v.dataset_id
                where v.dataset_id = %s and d.user_id = %s
                order by v.version desc
                """,
                (dataset_id, user_id),
            ).fetchall()
        return [_version(r) for r in rows]

    # The caller has already verified ownership of dataset_id.
    def get_analysis(self, dataset_id: UUID, version: int, kind: str, params_hash: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select result from public.analysis_cache where dataset_id = %s and version = %s and kind = %s and params_hash = %s",
                (dataset_id, version, kind, params_hash),
            ).fetchone()
        return row[0] if row else None

    def put_analysis(self, dataset_id: UUID, version: int, kind: str, params_hash: str, result: dict) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                insert into public.analysis_cache (dataset_id, version, kind, params_hash, result)
                values (%s, %s, %s, %s, %s)
                on conflict (dataset_id, version, kind, params_hash) do update set result = excluded.result
                """,
                (dataset_id, version, kind, params_hash, Jsonb(result)),
            )

    @staticmethod
    def _insert_version(conn, dataset_id: UUID, number: int, version: NewVersion) -> None:
        conn.execute(
            """
            insert into public.dataset_versions
              (dataset_id, version, parquet_path, row_count, column_count, size_bytes, operation, summary)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                dataset_id, number, version.parquet_path, version.row_count, version.column_count,
                version.size_bytes, Jsonb(version.operation), Jsonb(version.summary) if version.summary is not None else None,
            ),
        )


def _not_found():
    from app.core.errors import NotFoundError

    return NotFoundError("Dataset not found.", code="dataset_not_found")


def create_pool(database_url: str, max_size: int) -> ConnectionPool:
    """
    Connection pool tuned for Supabase's transaction pooler on a small server.

    Every network round trip to the database costs latency (measured: ~250 ms each
    from a laptop to a distant region), so the pool avoids unnecessary ones:
    - autocommit=True: single-statement reads don't send BEGIN/COMMIT (2 trips saved).
      Multi-statement writes still use explicit `conn.transaction()` blocks.
    - no per-checkout health check (1 trip saved). Instead idle connections are
      recycled after 2 minutes, and reads retry once if a connection was dropped.
    - prepare_threshold=None: the pooler can't handle server-side prepared statements.
    - opened lazily so the app can start even if the database is briefly unreachable.
    """
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=max_size,
        kwargs={"prepare_threshold": None, "connect_timeout": 10, "autocommit": True},
        max_idle=120,
        timeout=15,
        open=False,
        name="insightflow",
    )
