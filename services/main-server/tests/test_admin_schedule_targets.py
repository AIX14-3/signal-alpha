from app.api.routes.admin import _validate_targets


def test_validate_targets_allows_alternative_scheduler_target():
    assert _validate_targets([" alternative ", "price"]) == ["alternative", "price"]
