import unittest

from signal_alpha_data_access.repositories import UserSessionRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1, "user_id": args[0] if args else 1}

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "UPDATE 1"


class UserSessionRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_session_stores_refresh_token_hash(self):
        connection = FakeConnection()
        repository = UserSessionRepository(connection)

        row = await repository.create_session(
            user_id=1,
            refresh_token_hash="hashed-refresh",
            expires_at="2026-07-17T00:00:00Z",
            user_agent="pytest",
            ip_address="127.0.0.1",
        )

        self.assertEqual(row["user_id"], 1)
        sql = connection.calls[0][1]
        self.assertIn("INSERT INTO user_sessions", sql)
        self.assertEqual(connection.calls[0][2][1], "hashed-refresh")

    async def test_get_active_session_by_refresh_hash_joins_user(self):
        connection = FakeConnection()
        repository = UserSessionRepository(connection)

        await repository.get_active_session_by_refresh_hash("hashed-refresh")

        sql = connection.calls[0][1]
        self.assertIn("FROM user_sessions", sql)
        self.assertIn("INNER JOIN users", sql)
        self.assertIn("revoked_at IS NULL", sql)

    async def test_revoke_session_by_refresh_hash_sets_revoked_at(self):
        connection = FakeConnection()
        repository = UserSessionRepository(connection)

        await repository.revoke_session_by_refresh_hash("hashed-refresh")

        sql = connection.calls[0][1]
        self.assertIn("UPDATE user_sessions", sql)
        self.assertIn("revoked_at = NOW()", sql)


if __name__ == "__main__":
    unittest.main()
