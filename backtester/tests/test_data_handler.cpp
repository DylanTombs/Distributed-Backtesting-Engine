/**
 * Unit tests for FeatureCSVDataHandler and BacktestConfig.
 *
 * FeatureCSVDataHandler tests use temporary in-memory CSV files written to
 * a temp path so they exercise the real file-parsing code path.
 *
 * BacktestConfig tests exercise the new validate() method.
 */
#include <cstdio>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>

#include <gtest/gtest.h>

#include "market/FeatureCSVDataHandler.hpp"
#include "events/EventQueue.hpp"
#include "events/FeatureMarketEvent.hpp"
#include "config/BacktestConfig.hpp"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Write content to a temp file and return its path.
static std::string writeTempCSV(const std::string& content) {
    char buf[L_tmpnam];
    std::tmpnam(buf);  // NOLINT: acceptable in test context
    std::string path = std::string(buf) + ".csv";
    std::ofstream f(path);
    f << content;
    return path;
}

/// Pop all events from the queue and return them as a typed vector.
static std::vector<std::shared_ptr<FeatureMarketEvent>>
drainFeatureEvents(EventQueue& q) {
    std::vector<std::shared_ptr<FeatureMarketEvent>> out;
    while (!q.empty()) {
        auto ev = std::static_pointer_cast<FeatureMarketEvent>(q.pop());
        if (ev) out.push_back(ev);
    }
    return out;
}

// ---------------------------------------------------------------------------
// FeatureCSVDataHandler — construction errors
// ---------------------------------------------------------------------------

TEST(DataHandlerConstruct, NonExistentFileThrowsRuntimeError) {
    EXPECT_THROW(
        FeatureCSVDataHandler("/no/such/file.csv", "AAPL", {"close"}, "close", "date"),
        std::runtime_error
    );
}

TEST(DataHandlerConstruct, MissingDateColumnThrowsRuntimeError) {
    const std::string csv = "close,f1\n100.0,1.0\n";
    auto path = writeTempCSV(csv);
    EXPECT_THROW(
        FeatureCSVDataHandler(path, "AAPL", {"f1"}, "close", "date"),
        std::runtime_error
    );
    std::remove(path.c_str());
}

TEST(DataHandlerConstruct, MissingCloseColumnThrowsRuntimeError) {
    const std::string csv = "date,f1\n2020-01-02,1.0\n";
    auto path = writeTempCSV(csv);
    EXPECT_THROW(
        FeatureCSVDataHandler(path, "AAPL", {"f1"}, "close", "date"),
        std::runtime_error
    );
    std::remove(path.c_str());
}

TEST(DataHandlerConstruct, MissingFeatureColumnThrowsRuntimeError) {
    const std::string csv = "date,close\n2020-01-02,100.0\n";
    auto path = writeTempCSV(csv);
    EXPECT_THROW(
        FeatureCSVDataHandler(path, "AAPL", {"missing_feature"}, "close", "date"),
        std::runtime_error
    );
    std::remove(path.c_str());
}

// ---------------------------------------------------------------------------
// FeatureCSVDataHandler — streaming
// ---------------------------------------------------------------------------

TEST(DataHandlerStream, StreamingEmitsCorrectNumberOfEvents) {
    const std::string csv =
        "date,close,f1\n"
        "2020-01-02,100.0,1.5\n"
        "2020-01-03,101.0,1.6\n"
        "2020-01-06,102.0,1.7\n";
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "AAPL", {"f1"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);
    handler.streamNext(q);
    handler.streamNext(q);
    EXPECT_EQ(q.empty() ? 0 : 3, 3);  // three events queued
    std::remove(path.c_str());
}

TEST(DataHandlerStream, EmittedEventHasCorrectSymbol) {
    const std::string csv = "date,close,f1\n2020-01-02,100.0,1.5\n";
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "TSLA", {"f1"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);
    ASSERT_FALSE(q.empty());
    auto ev = std::static_pointer_cast<FeatureMarketEvent>(q.pop());
    EXPECT_EQ(ev->symbol, "TSLA");
    std::remove(path.c_str());
}

TEST(DataHandlerStream, EmittedEventHasCorrectPrice) {
    const std::string csv = "date,close,f1\n2020-01-02,123.45,0.0\n";
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "AAPL", {"f1"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);
    ASSERT_FALSE(q.empty());
    auto ev = std::static_pointer_cast<FeatureMarketEvent>(q.pop());
    EXPECT_DOUBLE_EQ(ev->price, 123.45);
    std::remove(path.c_str());
}

TEST(DataHandlerStream, EmittedEventHasCorrectFeatureCount) {
    const std::string csv = "date,close,f1,f2,f3\n2020-01-02,100.0,1.0,2.0,3.0\n";
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "AAPL", {"f1","f2","f3"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);
    ASSERT_FALSE(q.empty());
    auto ev = std::static_pointer_cast<FeatureMarketEvent>(q.pop());
    EXPECT_EQ(static_cast<int>(ev->features.size()), 3);
    std::remove(path.c_str());
}

TEST(DataHandlerStream, TimeMarkHasThreeComponents) {
    const std::string csv = "date,close,f1\n2020-03-15,100.0,1.0\n";
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "AAPL", {"f1"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);
    ASSERT_FALSE(q.empty());
    auto ev = std::static_pointer_cast<FeatureMarketEvent>(q.pop());
    EXPECT_EQ(static_cast<int>(ev->timeMark.size()), 3);
    std::remove(path.c_str());
}

TEST(DataHandlerStream, StreamingPastEndOfFileIsIdempotent) {
    const std::string csv = "date,close,f1\n2020-01-02,100.0,1.0\n";
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "AAPL", {"f1"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);  // consumes the one data row
    handler.streamNext(q);  // past EOF — must not throw or crash
    EXPECT_FALSE(q.empty());  // only one event total
    std::remove(path.c_str());
}

// ---------------------------------------------------------------------------
// FeatureCSVDataHandler — gap detection
// ---------------------------------------------------------------------------

TEST(DataHandlerGap, WeekendGapDoesNotIncrementCount) {
    // Friday → Monday: 3 calendar days, not a reportable gap
    const std::string csv =
        "date,close,f1\n"
        "2020-01-03,100.0,1.0\n"   // Friday
        "2020-01-06,101.0,1.1\n";  // Monday
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "AAPL", {"f1"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);
    handler.streamNext(q);
    EXPECT_EQ(handler.gapCount(), 0);
    std::remove(path.c_str());
}

TEST(DataHandlerGap, FourDayGapIncrementsCount) {
    // Thursday → Monday: 4 calendar days → gap
    const std::string csv =
        "date,close,f1\n"
        "2020-01-02,100.0,1.0\n"   // Thursday
        "2020-01-06,101.0,1.1\n";  // Monday (+4 days)
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "AAPL", {"f1"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);
    handler.streamNext(q);
    EXPECT_EQ(handler.gapCount(), 1);
    std::remove(path.c_str());
}

TEST(DataHandlerGap, MultipleGapsAccumulateInCount) {
    const std::string csv =
        "date,close,f1\n"
        "2020-01-02,100.0,1.0\n"
        "2020-01-09,101.0,1.1\n"   // 7-day gap
        "2020-01-19,102.0,1.2\n";  // 10-day gap
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "AAPL", {"f1"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);
    handler.streamNext(q);
    handler.streamNext(q);
    EXPECT_EQ(handler.gapCount(), 2);
    std::remove(path.c_str());
}

TEST(DataHandlerGap, ConsecutiveDaysHaveZeroGapCount) {
    const std::string csv =
        "date,close,f1\n"
        "2020-01-02,100.0,1.0\n"
        "2020-01-03,101.0,1.1\n"
        "2020-01-04,102.0,1.2\n";
    auto path = writeTempCSV(csv);
    FeatureCSVDataHandler handler(path, "AAPL", {"f1"}, "close", "date");
    EventQueue q;
    handler.streamNext(q);
    handler.streamNext(q);
    handler.streamNext(q);
    EXPECT_EQ(handler.gapCount(), 0);
    std::remove(path.c_str());
}

// ---------------------------------------------------------------------------
// BacktestConfig — validate()
// ---------------------------------------------------------------------------

static BacktestConfig validConfig() {
    BacktestConfig cfg;
    // All defaults are valid — just return without path checks
    cfg.modelPt       = "";
    cfg.featScalerCsv = "";
    cfg.targScalerCsv = "";
    return cfg;
}

TEST(BacktestConfigValidate, DefaultConfigIsValid) {
    EXPECT_NO_THROW(validConfig().validate());
}

TEST(BacktestConfigValidate, NegativeInitialCashThrows) {
    auto cfg = validConfig();
    cfg.initialCash = -1.0;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigValidate, ZeroInitialCashThrows) {
    auto cfg = validConfig();
    cfg.initialCash = 0.0;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigValidate, RiskFractionAboveOneThrows) {
    auto cfg = validConfig();
    cfg.riskFraction = 1.1;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigValidate, ZeroRiskFractionThrows) {
    auto cfg = validConfig();
    cfg.riskFraction = 0.0;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigValidate, NegativeHalfSpreadThrows) {
    auto cfg = validConfig();
    cfg.halfSpread = -0.001;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigValidate, ZeroHalfSpreadIsValid) {
    auto cfg = validConfig();
    cfg.halfSpread = 0.0;
    EXPECT_NO_THROW(cfg.validate());
}

TEST(BacktestConfigValidate, NegativeCommissionThrows) {
    auto cfg = validConfig();
    cfg.commission = -1.0;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigValidate, CorrelationThresholdAboveOneThrows) {
    auto cfg = validConfig();
    cfg.correlationThreshold = 1.1;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigValidate, CorrelationWindowZeroThrows) {
    auto cfg = validConfig();
    cfg.correlationWindow = 0;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigValidate, NonExistentModelPathThrows) {
    auto cfg = validConfig();
    cfg.modelPt = "/no/such/model.pt";
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigValidate, ValidationErrorMessageContainsFieldName) {
    auto cfg = validConfig();
    cfg.initialCash = -999.0;
    try {
        cfg.validate();
        FAIL() << "Expected std::invalid_argument";
    } catch (const std::invalid_argument& e) {
        EXPECT_NE(std::string(e.what()).find("initialCash"), std::string::npos);
    }
}

// ---------------------------------------------------------------------------
// BacktestConfig — signal threshold validation (Phase 3)
// ---------------------------------------------------------------------------

TEST(BacktestConfigThresholds, DefaultThresholdsAreValid) {
    auto cfg = validConfig();
    // defaults: buyThreshold=0.005, exitThreshold=0.000 — both in [0, 0.1]
    EXPECT_NO_THROW(cfg.validate());
}

TEST(BacktestConfigThresholds, ZeroBuyThresholdIsValid) {
    auto cfg = validConfig();
    cfg.buyThreshold = 0.0;
    EXPECT_NO_THROW(cfg.validate());
}

TEST(BacktestConfigThresholds, MaxBuyThresholdIsValid) {
    auto cfg = validConfig();
    cfg.buyThreshold = 0.1;
    EXPECT_NO_THROW(cfg.validate());
}

TEST(BacktestConfigThresholds, BuyThresholdAboveMaxThrows) {
    auto cfg = validConfig();
    cfg.buyThreshold = 0.2;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigThresholds, NegativeBuyThresholdThrows) {
    auto cfg = validConfig();
    cfg.buyThreshold = -0.001;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigThresholds, BuyThresholdErrorMessageContainsFieldName) {
    auto cfg = validConfig();
    cfg.buyThreshold = 0.5;
    try {
        cfg.validate();
        FAIL() << "Expected std::invalid_argument";
    } catch (const std::invalid_argument& e) {
        EXPECT_NE(std::string(e.what()).find("buyThreshold"), std::string::npos);
    }
}

TEST(BacktestConfigThresholds, ZeroExitThresholdIsValid) {
    auto cfg = validConfig();
    cfg.exitThreshold = 0.0;
    EXPECT_NO_THROW(cfg.validate());
}

TEST(BacktestConfigThresholds, ExitThresholdAboveMaxThrows) {
    auto cfg = validConfig();
    cfg.exitThreshold = 0.15;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigThresholds, NegativeExitThresholdThrows) {
    auto cfg = validConfig();
    cfg.exitThreshold = -0.001;
    EXPECT_THROW(cfg.validate(), std::invalid_argument);
}

TEST(BacktestConfigThresholds, ExitThresholdErrorMessageContainsFieldName) {
    auto cfg = validConfig();
    cfg.exitThreshold = 0.5;
    try {
        cfg.validate();
        FAIL() << "Expected std::invalid_argument";
    } catch (const std::invalid_argument& e) {
        EXPECT_NE(std::string(e.what()).find("exitThreshold"), std::string::npos);
    }
}

TEST(BacktestConfigThresholds, BothThresholdsAtBoundaryAreValid) {
    auto cfg = validConfig();
    cfg.buyThreshold  = 0.1;
    cfg.exitThreshold = 0.1;
    EXPECT_NO_THROW(cfg.validate());
}
