import unittest

from signal_alpha_data_access.repositories.users_billing import UserBillingRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 1


class UserBillingRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_user_uses_member_code_conflict(self):
        connection = FakeConnection()
        repository = UserBillingRepository(connection)

        await repository.create_user(member_code="U001", email="user@example.com")

        self.assertIn("ON CONFLICT (member_code)", connection.calls[0][1])

    async def test_upsert_subscription_plan_uses_plan_type_conflict(self):
        connection = FakeConnection()
        repository = UserBillingRepository(connection)

        await repository.upsert_subscription_plan(
            plan_type="free",
            plan_display_name="무료",
        )

        self.assertIn("ON CONFLICT (plan_type)", connection.calls[0][1])

    async def test_create_social_account_uses_provider_user_conflict(self):
        connection = FakeConnection()
        repository = UserBillingRepository(connection)

        await repository.upsert_social_account(
            user_id=1,
            provider="kakao",
            provider_user_id="kakao-1",
        )

        self.assertIn("ON CONFLICT (provider, provider_user_id)", connection.calls[0][1])

    async def test_record_terms_agreement_uses_unique_terms_key(self):
        connection = FakeConnection()
        repository = UserBillingRepository(connection)

        await repository.record_terms_agreement(user_id=1, terms_type="risk", version="1.0")

        self.assertIn("ON CONFLICT (user_id, terms_type, version)", connection.calls[0][1])
