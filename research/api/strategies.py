"""Strategy resolution: template/rules spec → engine spec file (Phase 8.3).

Templates are canned rule sets — the C++ engine only ever sees RuleSpec
lines, so "template vs. custom rules" is purely a UX distinction. The one
exception is ``ml_transformer``, which routes to the LibTorch binary and is
flagged experimental end-to-end.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from .schemas import RuleCondition, StrategyRules, StrategySpec

DEFAULT_TEMPLATE = "buy_hold"


class StrategyError(ValueError):
    """Invalid strategy parameters — safe to surface verbatim as a 422."""


@dataclass(frozen=True)
class ResolvedStrategy:
    name: str
    is_ml: bool = False
    rules: Optional[StrategyRules] = None
    canonical: dict = field(default_factory=dict)   # response echo + hash input

    @property
    def hash(self) -> str:
        digest = hashlib.sha1(
            json.dumps(self.canonical, sort_keys=True).encode()
        ).hexdigest()
        return digest[:12]

    def warmup_bars(self) -> int:
        """Longest indicator lookback — mirrors RuleSpec::warmupBars()."""
        if self.rules is None:
            return 1
        bars = 1
        for cond in [*self.rules.entry, *self.rules.exit]:
            for ind, period in (
                (cond.indicator, cond.period),
                (cond.other_indicator, cond.other_period),
            ):
                if ind is None or ind == "PRICE":
                    continue
                need = period + 1 if ind in ("RSI", "HIGH_N", "LOW_N") else period
                bars = max(bars, need)
        return bars


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _int_param(params: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = params.get(key, default)
    value = int(raw)
    if value != raw or not (lo <= value <= hi):
        raise StrategyError(f"'{key}' must be an integer in [{lo}, {hi}]")
    return value


def _float_param(params: dict, key: str, default: float,
                 lo: float, hi: float) -> float:
    value = float(params.get(key, default))
    if not (lo <= value <= hi):
        raise StrategyError(f"'{key}' must be in [{lo}, {hi}]")
    return value


def _cond(**kwargs) -> RuleCondition:
    return RuleCondition(**kwargs)


def _buy_hold(params: dict) -> StrategyRules:
    if params:
        raise StrategyError("buy_hold takes no params")
    return StrategyRules(entry=[_cond(indicator="PRICE", op=">", value=0)])


def _ma_cross(params: dict) -> StrategyRules:
    fast = _int_param(params, "fast", 10, 2, 250)
    slow = _int_param(params, "slow", 50, 2, 250)
    if fast >= slow:
        raise StrategyError("'fast' must be smaller than 'slow'")
    sma = {"other_indicator": "SMA", "other_period": slow}
    return StrategyRules(
        entry=[_cond(indicator="SMA", period=fast, op="crosses_above", **sma)],
        exit=[_cond(indicator="SMA", period=fast, op="crosses_below", **sma)],
    )


def _rsi_reversion(params: dict) -> StrategyRules:
    period = _int_param(params, "period", 14, 2, 250)
    buy_below = _float_param(params, "buy_below", 30, 1, 99)
    sell_above = _float_param(params, "sell_above", 70, 1, 99)
    if buy_below >= sell_above:
        raise StrategyError("'buy_below' must be smaller than 'sell_above'")
    return StrategyRules(
        entry=[_cond(indicator="RSI", period=period, op="<", value=buy_below)],
        exit=[_cond(indicator="RSI", period=period, op=">", value=sell_above)],
    )


def _breakout(params: dict) -> StrategyRules:
    lookback = _int_param(params, "lookback", 20, 2, 250)
    return StrategyRules(
        entry=[_cond(indicator="PRICE", op=">",
                     other_indicator="HIGH_N", other_period=lookback)],
        exit=[_cond(indicator="PRICE", op="<",
                    other_indicator="LOW_N", other_period=lookback)],
    )


_TEMPLATE_BUILDERS = {
    "buy_hold": _buy_hold,
    "ma_cross": _ma_cross,
    "rsi_reversion": _rsi_reversion,
    "breakout": _breakout,
}


# ---------------------------------------------------------------------------
# Resolution + serialisation
# ---------------------------------------------------------------------------

def resolve(spec: Optional[StrategySpec]) -> ResolvedStrategy:
    """Turn a request spec (or None) into an executable strategy."""
    if spec is None:
        spec = StrategySpec(template=DEFAULT_TEMPLATE)

    if spec.template == "ml_transformer":
        return ResolvedStrategy(
            name="ml_transformer", is_ml=True,
            canonical={"template": "ml_transformer"},
        )

    if spec.template is not None:
        rules = _TEMPLATE_BUILDERS[spec.template](spec.params)
        name = spec.name or spec.template
        canonical = {"template": spec.template,
                     "params": {k: spec.params[k] for k in sorted(spec.params)}}
    else:
        rules = spec.rules
        name = spec.name or "custom"
        canonical = {"rules": rules.model_dump(exclude_none=True)}
        # Keep pre-v2 cache hashes stable: "long" is the default and is
        # omitted from the canonical form (ADR-049).
        if rules.direction == "long":
            canonical["rules"].pop("direction", None)

    return ResolvedStrategy(name=name, rules=rules, canonical=canonical)


def _operand_token(indicator: str, period: Optional[int]) -> str:
    return "PRICE" if indicator == "PRICE" else f"{indicator}:{period}"


def _condition_line(cond: RuleCondition) -> str:
    lhs = _operand_token(cond.indicator, cond.period)
    if cond.other_indicator is not None:
        rhs = _operand_token(cond.other_indicator, cond.other_period)
    else:
        rhs = f"{cond.value:g}"
    return f"{lhs} {cond.op} {rhs}"


def to_spec_file(resolved: ResolvedStrategy) -> str:
    """Serialise to the flat line format RuleSpec::loadFromFile parses.

    Long strategies emit version 1 so their spec files (and cache hashes)
    stay byte-identical to Phase 8; only short strategies need v2 (ADR-049).
    """
    if resolved.rules is None:
        raise StrategyError("ml_transformer has no rule spec file")
    is_short = resolved.rules.direction == "short"
    lines = [f"version: {2 if is_short else 1}", f"name: {resolved.name}"]
    if is_short:
        lines.append("direction: short")
    lines += [f"entry: {_condition_line(c)}" for c in resolved.rules.entry]
    lines += [f"exit: {_condition_line(c)}" for c in resolved.rules.exit]
    return "\n".join(lines) + "\n"
