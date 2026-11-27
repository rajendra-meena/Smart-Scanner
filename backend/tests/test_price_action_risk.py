import pytest

from app.core.price_action_service import PriceActionService


@pytest.mark.parametrize(
    "entry, expected_stop, expected_target",
    [
        (18.30, 15.55, 23.80),
        (9.57, 8.15, 12.40),
        (50.65, 43.05, 65.85),
        (3.00, 2.55, 3.90),
    ],
)
def test_percentage_stop_and_two_r_target_are_tick_aligned(entry, expected_stop, expected_target):
    stop, target = PriceActionService._premium_levels(entry, 15, 2)
    assert stop == expected_stop
    assert target == expected_target
    assert 0 < stop < entry < target
    assert round(stop / 0.05) == pytest.approx(stop / 0.05)
    assert round(target / 0.05) == pytest.approx(target / 0.05)


@pytest.mark.parametrize("entry, percent, reward", [(0, 15, 2), (10, 0, 2), (10, 100, 2), (10, 15, 0.5)])
def test_invalid_premium_risk_inputs_are_rejected(entry, percent, reward):
    with pytest.raises(ValueError):
        PriceActionService._premium_levels(entry, percent, reward)
