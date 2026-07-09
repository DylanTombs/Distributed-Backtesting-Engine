"""Unit tests for strategy resolution and spec serialisation (Phase 8.3)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from research.api.schemas import RuleCondition, StrategyRules, StrategySpec
from research.api.strategies import (
    StrategyError,
    resolve,
    to_spec_file,
)


# ---------------------------------------------------------------------------
# Schema validation (boundary)
# ---------------------------------------------------------------------------

class TestStrategySchemas:
    def test_template_and_rules_are_mutually_exclusive(self):
        rules = StrategyRules(entry=[RuleCondition(
            indicator="PRICE", op=">", value=0)])
        with pytest.raises(ValidationError):
            StrategySpec(template="buy_hold", rules=rules)
        with pytest.raises(ValidationError):
            StrategySpec()

    def test_price_takes_no_period(self):
        with pytest.raises(ValidationError):
            RuleCondition(indicator="PRICE", period=10, op=">", value=0)

    def test_indicator_requires_period(self):
        with pytest.raises(ValidationError):
            RuleCondition(indicator="SMA", op=">", value=0)

    def test_value_xor_other_indicator(self):
        with pytest.raises(ValidationError):
            RuleCondition(indicator="SMA", period=5, op=">")
        with pytest.raises(ValidationError):
            RuleCondition(indicator="SMA", period=5, op=">", value=1,
                          other_indicator="SMA", other_period=10)

    def test_cross_requires_other_indicator(self):
        with pytest.raises(ValidationError):
            RuleCondition(indicator="SMA", period=5, op="crosses_above",
                          value=100)

    def test_condition_caps_enforced(self):
        cond = RuleCondition(indicator="PRICE", op=">", value=0)
        with pytest.raises(ValidationError):
            StrategyRules(entry=[cond] * 9)
        with pytest.raises(ValidationError):
            StrategyRules(entry=[cond], exit=[cond] * 9)

    def test_period_bounds(self):
        with pytest.raises(ValidationError):
            RuleCondition(indicator="SMA", period=1, op=">", value=0)
        with pytest.raises(ValidationError):
            RuleCondition(indicator="SMA", period=251, op=">", value=0)


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------

class TestTemplates:
    def test_none_resolves_to_buy_hold_default(self):
        resolved = resolve(None)
        assert resolved.name == "buy_hold"
        assert not resolved.is_ml
        assert len(resolved.rules.entry) == 1
        assert resolved.rules.exit == []

    def test_ma_cross_defaults_and_params(self):
        resolved = resolve(StrategySpec(template="ma_cross",
                                        params={"fast": 5, "slow": 20}))
        spec_text = to_spec_file(resolved)
        assert "entry: SMA:5 crosses_above SMA:20" in spec_text
        assert "exit: SMA:5 crosses_below SMA:20" in spec_text

    def test_ma_cross_rejects_fast_ge_slow(self):
        with pytest.raises(StrategyError, match="smaller than"):
            resolve(StrategySpec(template="ma_cross",
                                 params={"fast": 50, "slow": 10}))

    def test_rsi_reversion_spec_lines(self):
        resolved = resolve(StrategySpec(template="rsi_reversion"))
        text = to_spec_file(resolved)
        assert "entry: RSI:14 < 30" in text
        assert "exit: RSI:14 > 70" in text

    def test_breakout_uses_high_low_channels(self):
        resolved = resolve(StrategySpec(template="breakout",
                                        params={"lookback": 55}))
        text = to_spec_file(resolved)
        assert "entry: PRICE > HIGH_N:55" in text
        assert "exit: PRICE < LOW_N:55" in text

    def test_buy_hold_rejects_params(self):
        with pytest.raises(StrategyError):
            resolve(StrategySpec(template="buy_hold", params={"x": 1}))

    def test_non_integer_period_param_rejected(self):
        with pytest.raises(StrategyError):
            resolve(StrategySpec(template="ma_cross", params={"fast": 2.5}))

    def test_ml_template_flags_experimental(self):
        resolved = resolve(StrategySpec(template="ml_transformer"))
        assert resolved.is_ml
        assert resolved.rules is None
        with pytest.raises(StrategyError):
            to_spec_file(resolved)


# ---------------------------------------------------------------------------
# Custom rules → spec file
# ---------------------------------------------------------------------------

class TestCustomRules:
    def _spec(self):
        return StrategySpec(rules=StrategyRules(
            entry=[
                RuleCondition(indicator="RSI", period=7, op="<", value=25),
                RuleCondition(indicator="PRICE", op=">",
                              other_indicator="SMA", other_period=200),
            ],
            exit=[RuleCondition(indicator="RSI", period=7, op=">", value=60)],
        ), name="my dip buyer")

    def test_serialises_all_conditions(self):
        text = to_spec_file(resolve(self._spec()))
        assert text.startswith("version: 1\n")
        assert "name: my dip buyer" in text
        assert "entry: RSI:7 < 25" in text
        assert "entry: PRICE > SMA:200" in text
        assert "exit: RSI:7 > 60" in text

    def test_warmup_bars_mirrors_engine_rule(self):
        # SMA:200 needs 200; RSI:7 needs 8 → max is 200
        assert resolve(self._spec()).warmup_bars() == 200

    def test_hash_is_stable_and_order_sensitive_content(self):
        a = resolve(self._spec())
        b = resolve(self._spec())
        assert a.hash == b.hash
        different = resolve(StrategySpec(template="buy_hold"))
        assert a.hash != different.hash

    def test_template_hash_ignores_param_dict_order(self):
        a = resolve(StrategySpec(template="ma_cross",
                                 params={"fast": 5, "slow": 20}))
        b = resolve(StrategySpec(template="ma_cross",
                                 params={"slow": 20, "fast": 5}))
        assert a.hash == b.hash


# ---------------------------------------------------------------------------
# Spec v2: direction (Phase 9.2, ADR-049)
# ---------------------------------------------------------------------------

class TestDirection:
    def _short_spec(self) -> StrategySpec:
        return StrategySpec(rules=StrategyRules(
            direction="short",
            entry=[RuleCondition(indicator="RSI", period=7, op=">", value=75)],
            exit=[RuleCondition(indicator="RSI", period=7, op="<", value=40)],
        ), name="fade the rip")

    def test_short_rules_emit_version_2_spec(self):
        text = to_spec_file(resolve(self._short_spec()))
        assert text.startswith("version: 2\n")
        assert "direction: short" in text
        assert "entry: RSI:7 > 75" in text

    def test_long_rules_still_emit_version_1(self):
        long_spec = StrategySpec(rules=StrategyRules(
            entry=[RuleCondition(indicator="PRICE", op=">", value=0)],
        ))
        text = to_spec_file(resolve(long_spec))
        assert text.startswith("version: 1\n")
        assert "direction" not in text

    def test_templates_remain_version_1(self):
        for template in ("buy_hold", "ma_cross", "rsi_reversion", "breakout"):
            text = to_spec_file(resolve(StrategySpec(template=template)))
            assert text.startswith("version: 1\n"), template

    def test_direction_changes_the_cache_hash(self):
        short = resolve(self._short_spec())
        long_twin = resolve(StrategySpec(rules=StrategyRules(
            entry=[RuleCondition(indicator="RSI", period=7, op=">", value=75)],
            exit=[RuleCondition(indicator="RSI", period=7, op="<", value=40)],
        ), name="fade the rip"))
        assert short.hash != long_twin.hash

    def test_long_hash_unchanged_by_explicit_default(self):
        """Pre-v2 saved strategies (no direction field) must hit the same
        cache entries as ones that now send direction='long' explicitly."""
        implicit = resolve(StrategySpec(rules=StrategyRules(
            entry=[RuleCondition(indicator="PRICE", op=">", value=0)],
        )))
        explicit = resolve(StrategySpec(rules=StrategyRules(
            direction="long",
            entry=[RuleCondition(indicator="PRICE", op=">", value=0)],
        )))
        assert implicit.hash == explicit.hash

    def test_invalid_direction_rejected_at_boundary(self):
        with pytest.raises(ValidationError):
            StrategyRules(
                direction="sideways",
                entry=[RuleCondition(indicator="PRICE", op=">", value=0)],
            )
