/**
 * Unit tests for RuleStrategy and RuleSpec parsing (Phase 8.2).
 */
#include <cstdio>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

#include <gtest/gtest.h>

#include "events/EventQueue.hpp"
#include "events/MarketEvent.hpp"
#include "events/SignalEvent.hpp"
#include "strategy/RuleStrategy.hpp"

namespace {

/// Write a spec to a temp file and return its path.
std::string writeSpec(const std::string& body) {
    static int counter = 0;
    const std::string path =
        testing::TempDir() + "rule_spec_" + std::to_string(counter++) + ".txt";
    std::ofstream f(path);
    f << body;
    return path;
}

/// Feed a price series through the strategy; collect emitted signal types.
std::vector<SignalType> runSeries(RuleStrategy& strat,
                                  const std::vector<double>& prices) {
    EventQueue queue;
    std::vector<SignalType> signals;
    int day = 0;
    for (double p : prices) {
        MarketEvent ev("TEST", p, "2024-01-" + std::to_string(++day));
        strat.onMarketEvent(ev, queue);
        while (!queue.empty()) {
            auto e = queue.pop();
            if (e->getType() == EventType::SIGNAL)
                signals.push_back(
                    std::static_pointer_cast<SignalEvent>(e)->signalType);
        }
    }
    return signals;
}

}  // namespace

// ---------------------------------------------------------------------------
// Spec parsing
// ---------------------------------------------------------------------------

TEST(RuleSpecTest, ParsesValidSpec) {
    const auto path = writeSpec(
        "version: 1\n"
        "name: ma_cross\n"
        "entry: SMA:3 crosses_above SMA:5\n"
        "exit: SMA:3 crosses_below SMA:5\n");
    const RuleSpec spec = RuleSpec::loadFromFile(path);
    EXPECT_EQ(spec.name, "ma_cross");
    ASSERT_EQ(spec.entry.size(), 1u);
    ASSERT_EQ(spec.exit.size(), 1u);
    EXPECT_EQ(spec.warmupBars(), 5);
}

TEST(RuleSpecTest, ParsesValueComparison) {
    const auto path = writeSpec(
        "version: 1\n"
        "entry: RSI:14 < 30\n"
        "exit: RSI:14 > 70\n");
    const RuleSpec spec = RuleSpec::loadFromFile(path);
    EXPECT_TRUE(spec.entry[0].rhsIsValue);
    EXPECT_DOUBLE_EQ(spec.entry[0].value, 30.0);
    EXPECT_EQ(spec.warmupBars(), 15);  // RSI needs period+1 bars
}

TEST(RuleSpecTest, RejectsMissingVersion) {
    const auto path = writeSpec("entry: PRICE > 0\n");
    EXPECT_THROW(RuleSpec::loadFromFile(path), std::invalid_argument);
}

TEST(RuleSpecTest, RejectsEmptyEntry) {
    const auto path = writeSpec("version: 1\nexit: RSI:14 > 70\n");
    EXPECT_THROW(RuleSpec::loadFromFile(path), std::invalid_argument);
}

TEST(RuleSpecTest, RejectsUnknownIndicatorAndOperator) {
    EXPECT_THROW(RuleSpec::loadFromFile(writeSpec(
        "version: 1\nentry: MACD:12 > 0\n")), std::invalid_argument);
    EXPECT_THROW(RuleSpec::loadFromFile(writeSpec(
        "version: 1\nentry: PRICE >= 5\n")), std::invalid_argument);
}

TEST(RuleSpecTest, RejectsPeriodOutOfRange) {
    EXPECT_THROW(RuleSpec::loadFromFile(writeSpec(
        "version: 1\nentry: SMA:1 > 0\n")), std::invalid_argument);
    EXPECT_THROW(RuleSpec::loadFromFile(writeSpec(
        "version: 1\nentry: SMA:251 > 0\n")), std::invalid_argument);
}

TEST(RuleSpecTest, RejectsCrossAgainstConstant) {
    EXPECT_THROW(RuleSpec::loadFromFile(writeSpec(
        "version: 1\nentry: SMA:5 crosses_above 100\n")), std::invalid_argument);
}

TEST(RuleSpecTest, RejectsTooManyConditions) {
    std::string body = "version: 1\n";
    for (int i = 0; i < 9; ++i) body += "entry: PRICE > 0\n";
    EXPECT_THROW(RuleSpec::loadFromFile(writeSpec(body)), std::invalid_argument);
}

// ---------------------------------------------------------------------------
// Strategy behaviour
// ---------------------------------------------------------------------------

TEST(RuleStrategyTest, BuyAndHoldEntersOnceAndNeverExits) {
    const auto path = writeSpec("version: 1\nentry: PRICE > 0\n");
    RuleStrategy strat{RuleSpec::loadFromFile(path)};
    const auto signals = runSeries(strat, {10, 11, 9, 12, 8, 13});
    ASSERT_EQ(signals.size(), 1u);
    EXPECT_EQ(signals[0], SignalType::LONG);
}

TEST(RuleStrategyTest, SmaCrossEmitsLongThenExit) {
    // SMA:2 vs SMA:3 — falling series keeps fast below slow, then a sharp
    // rise crosses it above (LONG), then a sharp fall crosses back (EXIT).
    const auto path = writeSpec(
        "version: 1\n"
        "entry: SMA:2 crosses_above SMA:3\n"
        "exit: SMA:2 crosses_below SMA:3\n");
    RuleStrategy strat{RuleSpec::loadFromFile(path)};
    const auto signals =
        runSeries(strat, {10, 9, 8, 7, 20, 30, 30, 5, 4, 3});
    ASSERT_EQ(signals.size(), 2u);
    EXPECT_EQ(signals[0], SignalType::LONG);
    EXPECT_EQ(signals[1], SignalType::EXIT);
}

TEST(RuleStrategyTest, RsiMeanReversionEntersOversold) {
    // Strictly falling series → RSI 0 (oversold) → entry fires.
    const auto path = writeSpec(
        "version: 1\n"
        "entry: RSI:3 < 30\n"
        "exit: RSI:3 > 70\n");
    RuleStrategy strat{RuleSpec::loadFromFile(path)};
    const auto signals = runSeries(strat, {50, 48, 46, 44, 42});
    ASSERT_GE(signals.size(), 1u);
    EXPECT_EQ(signals[0], SignalType::LONG);
}

TEST(RuleStrategyTest, BreakoutRequiresNewHigh) {
    // PRICE > HIGH_N:3 — flat series never breaks out; spike does.
    const auto path = writeSpec("version: 1\nentry: PRICE > HIGH_N:3\n");
    RuleStrategy strat{RuleSpec::loadFromFile(path)};

    EXPECT_TRUE(runSeries(strat, {10, 10, 10, 10, 10, 10}).empty());

    RuleStrategy strat2{RuleSpec::loadFromFile(path)};
    const auto signals = runSeries(strat2, {10, 10, 10, 10, 15});
    ASSERT_EQ(signals.size(), 1u);
    EXPECT_EQ(signals[0], SignalType::LONG);
}

TEST(RuleStrategyTest, MultipleEntryConditionsAreAnded) {
    // Both must hold on the same bar: price above SMA:2 AND below 100.
    const auto path = writeSpec(
        "version: 1\n"
        "entry: PRICE > SMA:2\n"
        "entry: PRICE < 100\n");
    RuleStrategy strat{RuleSpec::loadFromFile(path)};
    // Rising but ≥100: second condition blocks entry.
    EXPECT_TRUE(runSeries(strat, {100, 110, 120}).empty());

    RuleStrategy strat2{RuleSpec::loadFromFile(path)};
    const auto signals = runSeries(strat2, {50, 60, 70});
    ASSERT_EQ(signals.size(), 1u);
    EXPECT_EQ(signals[0], SignalType::LONG);
}

TEST(RuleStrategyTest, NoDuplicateSignalsWhileStateUnchanged) {
    const auto path = writeSpec(
        "version: 1\n"
        "entry: PRICE > SMA:2\n"
        "exit: PRICE < SMA:2\n");
    RuleStrategy strat{RuleSpec::loadFromFile(path)};
    // Repeatedly above SMA after entry — must not re-emit LONG.
    const auto signals = runSeries(strat, {10, 12, 14, 16, 18, 20});
    ASSERT_EQ(signals.size(), 1u);
    EXPECT_EQ(signals[0], SignalType::LONG);
}

TEST(RuleStrategyTest, NoSignalsBeforeWarmup) {
    const auto path = writeSpec("version: 1\nentry: SMA:5 > 0\n");
    RuleStrategy strat{RuleSpec::loadFromFile(path)};
    EXPECT_TRUE(runSeries(strat, {10, 10, 10, 10}).empty());  // 4 < warmup 5
}

// ---------------------------------------------------------------------------
// Version 2: direction (Phase 9.2, ADR-049)
// ---------------------------------------------------------------------------

TEST(RuleSpecTest, ParsesShortDirectionUnderVersion2) {
    const auto path = writeSpec(
        "version: 2\n"
        "direction: short\n"
        "entry: RSI:3 > 70\n"
        "exit: RSI:3 < 30\n");
    const RuleSpec spec = RuleSpec::loadFromFile(path);
    EXPECT_EQ(spec.direction, RuleDirection::SHORT);
}

TEST(RuleSpecTest, DirectionDefaultsToLong) {
    const auto v1 = RuleSpec::loadFromFile(writeSpec(
        "version: 1\nentry: PRICE > 0\n"));
    EXPECT_EQ(v1.direction, RuleDirection::LONG);

    const auto v2 = RuleSpec::loadFromFile(writeSpec(
        "version: 2\nentry: PRICE > 0\n"));
    EXPECT_EQ(v2.direction, RuleDirection::LONG);
}

TEST(RuleSpecTest, RejectsDirectionUnderVersion1) {
    EXPECT_THROW(RuleSpec::loadFromFile(writeSpec(
        "version: 1\ndirection: short\nentry: PRICE > 0\n")),
        std::invalid_argument);
}

TEST(RuleSpecTest, RejectsUnknownDirection) {
    EXPECT_THROW(RuleSpec::loadFromFile(writeSpec(
        "version: 2\ndirection: sideways\nentry: PRICE > 0\n")),
        std::invalid_argument);
}

TEST(RuleStrategyTest, ShortDirectionEmitsShortThenExit) {
    // Overbought entry (rising series), oversold exit (falling series).
    const auto path = writeSpec(
        "version: 2\n"
        "direction: short\n"
        "entry: RSI:3 > 70\n"
        "exit: RSI:3 < 30\n");
    RuleStrategy strat{RuleSpec::loadFromFile(path)};
    const auto signals =
        runSeries(strat, {50, 52, 54, 56, 58, 40, 30, 20, 10});
    ASSERT_EQ(signals.size(), 2u);
    EXPECT_EQ(signals[0], SignalType::SHORT);
    EXPECT_EQ(signals[1], SignalType::EXIT);
}

TEST(RuleStrategyTest, LongSpecStillEmitsLongUnderVersion2) {
    const auto path = writeSpec("version: 2\nentry: PRICE > 0\n");
    RuleStrategy strat{RuleSpec::loadFromFile(path)};
    const auto signals = runSeries(strat, {10, 11, 12});
    ASSERT_EQ(signals.size(), 1u);
    EXPECT_EQ(signals[0], SignalType::LONG);
}
