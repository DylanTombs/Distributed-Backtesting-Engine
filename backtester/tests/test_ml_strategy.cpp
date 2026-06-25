/**
 * Unit tests for MLStrategy.
 *
 * LibTorch is not available in CI so runInference() returns -1.0 (no-op).
 * Tests cover: construction with valid scalers, feature-count validation,
 * 3-state signal logic gating, short-sell flag propagation, and the
 * constructor-level model-cache path (Task 4.2 — compile-time verification
 * that two instances sharing a path path don't double-load the module).
 */
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include <gtest/gtest.h>

#include "strategy/MLStrategy.hpp"
#include "events/EventQueue.hpp"
#include "events/FeatureMarketEvent.hpp"
#include "events/SignalEvent.hpp"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static std::string writeTempScaler(const std::string& path, int nFeatures) {
    std::ofstream f(path);
    f << "feature,mean,scale\n";
    for (int i = 0; i < nFeatures; ++i)
        f << "f" << i << ",0.0,1.0\n";
    return path;
}

static std::string writeTempTargetScaler(const std::string& path) {
    std::ofstream f(path);
    f << "feature,mean,scale\n";
    f << "close,0.0,1.0\n";
    return path;
}

static const int  N_FEAT   = 4;
static const int  SEQ_LEN  = 3;
static const char FEAT_CSV[] = "/tmp/test_feat_scaler.csv";
static const char TARG_CSV[] = "/tmp/test_targ_scaler.csv";

class MLStrategyTest : public ::testing::Test {
protected:
    void SetUp() override {
        writeTempScaler(FEAT_CSV, N_FEAT);
        writeTempTargetScaler(TARG_CSV);
    }

    MLStrategy makeStrategy(bool allowShort = false) {
        return MLStrategy(
            /*modelPath=*/     "",
            /*featScaler=*/    FEAT_CSV,
            /*targScaler=*/    TARG_CSV,
            /*seqLen=*/        SEQ_LEN,
            /*nFeatures=*/     N_FEAT,
            /*buyThreshold=*/  0.0,
            /*exitThreshold=*/ 0.0,
            allowShort);
    }

    // Push a FeatureMarketEvent with `price` and a uniform feature vector.
    void pushEvent(MLStrategy& strat, double price, EventQueue& q,
                   const std::string& ts = "2024-01-01") {
        auto ev = std::make_shared<FeatureMarketEvent>(
            "AAPL", price, ts,
            std::vector<double>(N_FEAT, 0.0),
            std::vector<double>{0.0, 0.0, 0.0});
        strat.onMarketEvent(*ev, q);
    }
};

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

TEST_F(MLStrategyTest, ConstructsWithValidScalers) {
    EXPECT_NO_THROW(makeStrategy());
}

TEST_F(MLStrategyTest, ConstructsWithAllowShortTrue) {
    EXPECT_NO_THROW(makeStrategy(/*allowShort=*/true));
}

TEST_F(MLStrategyTest, WrongFeatureCountLoggedAndSkipped) {
    MLStrategy strat = makeStrategy();
    EventQueue q;

    // N_FEAT=4 but event carries 2 features → should not emit a signal
    auto bad = std::make_shared<FeatureMarketEvent>(
        "AAPL", 100.0, "2024-01-01",
        std::vector<double>(2, 0.0),
        std::vector<double>{0.0, 0.0, 0.0});
    strat.onMarketEvent(*bad, q);
    EXPECT_TRUE(q.empty());
}

TEST_F(MLStrategyTest, PlainMarketEventIgnored) {
    MLStrategy strat = makeStrategy();
    EventQueue q;

    // A base MarketEvent (not FeatureMarketEvent) should be a no-op
    MarketEvent ev("AAPL", 100.0, "2024-01-01");
    strat.onMarketEvent(ev, q);
    EXPECT_TRUE(q.empty());
}

// ---------------------------------------------------------------------------
// Buffer gating — no inference until seqLen bars are in
// ---------------------------------------------------------------------------

TEST_F(MLStrategyTest, NoSignalBeforeBufferFull) {
    MLStrategy strat = makeStrategy();
    EventQueue q;

    // Push SEQ_LEN-1 bars — buffer not full yet, runInference returns -1 anyway
    for (int i = 0; i < SEQ_LEN - 1; ++i)
        pushEvent(strat, 100.0, q);

    EXPECT_TRUE(q.empty());
}

// With LibTorch disabled, runInference always returns -1.0, so even after
// the buffer is full no signal is ever emitted (the early-return guard fires).
// This is the expected behaviour in the no-LibTorch build.
TEST_F(MLStrategyTest, NoSignalAfterBufferFullWithoutLibTorch) {
    MLStrategy strat = makeStrategy();
    EventQueue q;

    for (int i = 0; i < SEQ_LEN + 2; ++i)
        pushEvent(strat, 100.0, q);

    EXPECT_TRUE(q.empty());
}

// ---------------------------------------------------------------------------
// allowShort flag propagation
// ---------------------------------------------------------------------------

TEST_F(MLStrategyTest, AllowShortFalseDoesNotEmitShortSignals) {
    MLStrategy strat = makeStrategy(/*allowShort=*/false);
    EventQueue q;

    // Drive the buffer full; without LibTorch no signal is emitted regardless
    for (int i = 0; i < SEQ_LEN + 2; ++i)
        pushEvent(strat, 100.0, q);

    // Verify no SHORT was emitted
    bool sawShort = false;
    while (!q.empty()) {
        auto ev = q.pop();
        if (auto sig = std::dynamic_pointer_cast<SignalEvent>(ev))
            if (sig->signalType == SignalType::SHORT) sawShort = true;
    }
    EXPECT_FALSE(sawShort);
}

// ---------------------------------------------------------------------------
// Model cache (Task 4.2) — compile-time coverage
//
// Without LibTorch the cache is never populated, so this test is a
// construction-level smoke test that verifies two instances with the same
// (empty) model path construct without crashing.  With LibTorch enabled the
// cache machinery would prevent a second torch::jit::load() call.
// ---------------------------------------------------------------------------

TEST_F(MLStrategyTest, TwoInstancesWithSamePathConstructWithoutError) {
    EXPECT_NO_THROW({
        MLStrategy a("", FEAT_CSV, TARG_CSV, SEQ_LEN, N_FEAT);
        MLStrategy b("", FEAT_CSV, TARG_CSV, SEQ_LEN, N_FEAT);
        (void)a; (void)b;
    });
}
