#pragma once

/**
 * Indicators — pure indicator math used by RuleStrategy (Phase 9.1).
 *
 * Free functions over a price window so the formulas can be pinned by the
 * shared cross-validation fixture (tests/fixtures/indicator_crossval.csv,
 * ADR-048) independently of RuleStrategy's window management.
 *
 * Contracts (documented divergences from the Python feature pipeline):
 *  - rsi: Cutler's variant — simple averages of gains/losses over the last
 *    `period` diffs. Matches technicalIndicators.calculateRsi (also simple
 *    -average) for period 14.
 *  - emaSeeded: seeded with the SMA of the first `period` values of the
 *    window, then the standard recursion over the rest. Deterministic from
 *    the window it is given. The Python pipeline's EMA is stream-stateful
 *    (seeded from the first bar of the whole feed), so the two are pinned
 *    to separate fixture columns, not to each other.
 *  - highestBefore/lowestBefore exclude the final (current) value, so
 *    "PRICE > HIGH_N:p" is a genuine breakout condition.
 *
 * Preconditions (callers guarantee via warm-up): window.size() >= period
 * (rsi: >= period + 1; highest/lowest: >= period + 1).
 */

#include <algorithm>
#include <cstddef>
#include <deque>
#include <numeric>

namespace indicators {

inline double sma(const std::deque<double>& window, int period) {
    const double sum = std::accumulate(window.end() - period, window.end(), 0.0);
    return sum / period;
}

inline double emaSeeded(const std::deque<double>& window, int period) {
    const double k = 2.0 / (period + 1);
    double ema = std::accumulate(window.begin(), window.begin() + period, 0.0) /
                 period;
    for (std::size_t i = period; i < window.size(); ++i)
        ema = window[i] * k + ema * (1.0 - k);
    return ema;
}

inline double rsi(const std::deque<double>& window, int period) {
    const std::size_t n = window.size();
    double gain = 0.0, loss = 0.0;
    for (std::size_t i = n - period; i < n; ++i) {
        const double d = window[i] - window[i - 1];
        if (d > 0) gain += d; else loss -= d;
    }
    if (loss == 0.0) return 100.0;
    const double rs = gain / loss;
    return 100.0 - 100.0 / (1.0 + rs);
}

inline double highestBefore(const std::deque<double>& window, int period) {
    return *std::max_element(window.end() - period - 1, window.end() - 1);
}

inline double lowestBefore(const std::deque<double>& window, int period) {
    return *std::min_element(window.end() - period - 1, window.end() - 1);
}

}  // namespace indicators
