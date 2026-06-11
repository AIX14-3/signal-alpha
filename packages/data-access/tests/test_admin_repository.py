import unittest

from signal_alpha_data_access.repositories.admin import AdminRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1}

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"


class AdminRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_admin_account_uses_email_conflict(self):
        connection = FakeConnection()
        repository = AdminRepository(connection)

        await repository.upsert_admin_account(
            email="admin@example.com",
            password_hash="hashed",
        )

        self.assertIn("ON CONFLICT (email)", connection.calls[0][1])

    async def test_create_admin_session_uses_session_token_conflict(self):
        connection = FakeConnection()
        repository = AdminRepository(connection)

        await repository.create_session(
            admin_id=1,
            session_token="token",
            expires_at="2026-06-09T00:00:00+09:00",
        )

        self.assertIn("ON CONFLICT (session_token)", connection.calls[0][1])
