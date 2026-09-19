"""
Interface Parity Test: Ensures PostgresStore implements 100% of the public methods
exposed by SQLiteStore so that routers never encounter missing attribute errors.
"""
import inspect
import unittest

from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.database.postgres_store import PostgresStore


class TestStoreInterfaceParity(unittest.TestCase):
    def test_method_signatures_parity(self):
        """Verify all public methods on SQLiteStore exist on PostgresStore with matching parameters."""
        sqlite_methods = {
            name: func
            for name, func in inspect.getmembers(SQLiteStore, predicate=inspect.isfunction)
            if not name.startswith("_") or name in ("_seed_default_categories", "_get_user_limits_dict", "_public_material")
        }

        postgres_methods = {
            name: func
            for name, func in inspect.getmembers(PostgresStore, predicate=inspect.isfunction)
        }

        missing_methods = []
        for name, sq_func in sqlite_methods.items():
            if name not in postgres_methods:
                missing_methods.append(name)

        self.assertEqual(
            missing_methods,
            [],
            f"PostgresStore is missing {len(missing_methods)} methods found in SQLiteStore: {missing_methods}",
        )

        # Check parameter count compatibility
        mismatched_params = []
        for name, sq_func in sqlite_methods.items():
            pg_func = postgres_methods[name]
            sq_params = inspect.signature(sq_func).parameters
            pg_params = inspect.signature(pg_func).parameters

            # Filter out 'self'
            sq_pnames = [p for p in sq_params.keys() if p != "self"]
            pg_pnames = [p for p in pg_params.keys() if p != "self"]

            if len(sq_pnames) != len(pg_pnames) and not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in pg_params.values()):
                mismatched_params.append((name, sq_pnames, pg_pnames))

        self.assertEqual(
            mismatched_params,
            [],
            f"Method signature parameter mismatch: {mismatched_params}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
