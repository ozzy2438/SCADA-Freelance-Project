"""Physics formula and clipping tests."""

from __future__ import annotations

import pytest

from ges_intel.domain.physics import clip_power_kw, generator_true_expected_kw, physics_expected_kw


class TestPhysics:
    def test_night_is_zero(self) -> None:
        assert physics_expected_kw(100.0, 0.0, 20.0) == 0.0

    def test_stc_like_output_near_pr_times_capacity(self) -> None:
        p = physics_expected_kw(100.0, 1000.0, 25.0)
        assert 80.0 <= p <= 90.0

    def test_clip_rejects_over_capacity_and_negative(self) -> None:
        assert clip_power_kw(999.0, 100.0) == 100.0
        assert clip_power_kw(-5.0, 100.0) == 0.0

    def test_hot_day_derates_vs_stc(self) -> None:
        cool = physics_expected_kw(100.0, 1000.0, 15.0)
        hot = physics_expected_kw(100.0, 1000.0, 45.0)
        assert hot < cool

    def test_generator_truth_applies_iam_vs_simple_physics(self) -> None:
        simple = physics_expected_kw(100.0, 900.0, 30.0)
        morning = generator_true_expected_kw(100.0, 900.0, 30.0, 8.0)
        noon = generator_true_expected_kw(100.0, 900.0, 30.0, 12.0)
        assert morning < simple
        assert noon == pytest.approx(simple, rel=1e-9)

    def test_negative_capacity_rejected(self) -> None:
        with pytest.raises(ValueError):
            clip_power_kw(1.0, -1.0)
