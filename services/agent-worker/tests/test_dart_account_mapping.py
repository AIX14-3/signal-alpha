import unittest

from app.collectors.dart.account_mapping import map_account


class AccountMappingTest(unittest.TestCase):
    def test_maps_by_standard_account_id(self):
        self.assertEqual(map_account("ifrs-full_Revenue", "매출액"), "revenue")
        self.assertEqual(map_account("dart_OperatingIncomeLoss", "영업이익"), "operating_income")
        self.assertEqual(map_account("ifrs-full_Liabilities", "부채총계"), "total_liabilities")

    def test_falls_back_to_account_nm_when_id_missing_or_unknown(self):
        self.assertEqual(map_account(None, "매출액"), "revenue")
        self.assertEqual(map_account("", "자본총계"), "total_equity")
        # 비표준 account_id 라도 account_nm 폴백
        self.assertEqual(map_account("custom_xyz", "재고자산"), "inventories")

    def test_returns_none_for_unmapped(self):
        self.assertIsNone(map_account("custom_xyz", "알수없는계정"))
        self.assertIsNone(map_account(None, None))


if __name__ == "__main__":
    unittest.main()
