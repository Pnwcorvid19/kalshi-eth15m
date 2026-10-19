"""Basic unit tests for the probability model."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.model import Eth15mModel


def test_deep_itm_high_prob():
    m = Eth15mModel(min_confidence=0.55)
    # Price well above floor, short time left → high P(YES)
    p = m.p_finish_above(current=2500.0, floor=2400.0, seconds_left=30)
    assert p > 0.9


def test_deep_otm_low_prob():
    m = Eth15mModel(min_confidence=0.55)
    p = m.p_finish_above(current=2300.0, floor=2400.0, seconds_left=30)
    assert p < 0.1


def test_at_the_money_near_half():
    m = Eth15mModel(min_confidence=0.55)
    p = m.p_finish_above(current=2400.0, floor=2400.0, seconds_left=300)
    assert 0.4 < p < 0.6


def test_predict_side():
    m = Eth15mModel(min_confidence=0.55)
    # Force some price history
    for _ in range(20):
        m.update_price(2450.0)
    pred = m.predict(
        ticker="TEST",
        floor_strike=2400.0,
        current_price=2450.0,
        price_source="test",
        seconds_left=60,
    )
    assert pred.side in ("YES", "NO", "NEUTRAL")
    assert 0.0 <= pred.p_yes <= 1.0
    assert abs(pred.p_yes + pred.p_no - 1.0) < 1e-9


if __name__ == "__main__":
    test_deep_itm_high_prob()
    test_deep_otm_low_prob()
    test_at_the_money_near_half()
    test_predict_side()
    print("All tests passed")
