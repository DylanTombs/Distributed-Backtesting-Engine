/**
 * Unit tests for Portfolio.
 *
 * BacktestConfig defaults (zero slippage) are used throughout so that
 * Portfolio tests are independent of SimulatedExecution.
 */
#include <cmath>
#include <fstream>
#include <sstream>
#include <string>

#include <gtest/gtest.h>

#include "portfolio/Portfolio.hpp"
#include "events/MarketEvent.hpp"
#include "events/SignalEvent.hpp"
#include "events/FillEvent.hpp"

class PortfolioTest : public ::testing::Test {
protected:
    static constexpr double INITIAL_CASH    = 10'000.0;
    static constexpr double RISK_FRACTION   = 0.10;
    Portfolio portfolio{INITIAL_CASH, RISK_FRACTION};
};


// ---- Construction -----------------------------------------------------------

TEST_F(PortfolioTest, InitialCashMatchesConstructorArgument) {
    EXPECT_DOUBLE_EQ(portfolio.getCash(), INITIAL_CASH);
}

TEST_F(PortfolioTest, InitialPositionForUnknownSymbolIsZero) {
    EXPECT_EQ(portfolio.getPosition("AAPL"), 0);
}

TEST_F(PortfolioTest, InitialEquityCurveIsEmpty) {
    EXPECT_TRUE(portfolio.getEquityCurve().empty());
}

TEST_F(PortfolioTest, InitialTotalEquityEqualsCash) {
    EXPECT_DOUBLE_EQ(portfolio.getTotalEquity(), INITIAL_CASH);
}

// ---- Fill accounting --------------------------------------------------------

TEST_F(PortfolioTest, BuyFillDecreasesCorrectCashAmount) {
    FillEvent fill("AAPL", 5, 100.0, 0.5);
    portfolio.updateFill(fill);
    EXPECT_DOUBLE_EQ(portfolio.getCash(), 9'499.5);
}

TEST_F(PortfolioTest, BuyFillIncreasesPositionByQuantity) {
    FillEvent fill("AAPL", 5, 100.0, 0.5);
    portfolio.updateFill(fill);
    EXPECT_EQ(portfolio.getPosition("AAPL"), 5);
}

TEST_F(PortfolioTest, BuyFillRecordsTradeWithBuyDirection) {
    FillEvent fill("AAPL", 3, 100.0, 0.0);
    portfolio.updateFill(fill);
    ASSERT_EQ(portfolio.getTrades().size(), 1u);
    EXPECT_EQ(portfolio.getTrades()[0].direction, "BUY");
    EXPECT_EQ(portfolio.getTrades()[0].quantity,  3);
}

TEST_F(PortfolioTest, SellFillIncreasesPortfolioCash) {
    portfolio.updateFill(FillEvent("AAPL",  5, 100.0, 0.5));
    portfolio.updateFill(FillEvent("AAPL", -5, 110.0, 0.5));
    EXPECT_DOUBLE_EQ(portfolio.getCash(), 10'049.0);
}

TEST_F(PortfolioTest, SellFillReducesPosition) {
    portfolio.updateFill(FillEvent("AAPL",  5, 100.0, 0.0));
    portfolio.updateFill(FillEvent("AAPL", -5, 110.0, 0.0));
    EXPECT_EQ(portfolio.getPosition("AAPL"), 0);
}

TEST_F(PortfolioTest, ProfitableSellRecordedWithProfitTrue) {
    portfolio.updateFill(FillEvent("AAPL",  5, 100.0, 0.0));
    portfolio.updateFill(FillEvent("AAPL", -5, 110.0, 0.0));
    EXPECT_TRUE(portfolio.getTrades().back().profit);
}

TEST_F(PortfolioTest, LosingTradeRecordedWithProfitFalse) {
    portfolio.updateFill(FillEvent("AAPL",  5, 100.0, 0.0));
    portfolio.updateFill(FillEvent("AAPL", -5,  90.0, 0.0));
    EXPECT_FALSE(portfolio.getTrades().back().profit);
}

TEST_F(PortfolioTest, CommissionDeductedSeparatelyFromTradeValue) {
    FillEvent fill("AAPL", 5, 100.0, 2.0);
    portfolio.updateFill(fill);
    EXPECT_DOUBLE_EQ(portfolio.getCash(), 9'498.0);
}

// ---- Market update & equity curve ------------------------------------------

TEST_F(PortfolioTest, UpdateMarketAppendsOneEquityCurveEntry) {
    portfolio.updateMarket(MarketEvent("AAPL", 150.0, "2024-01-01"));
    EXPECT_EQ(portfolio.getEquityCurve().size(), 1u);
}

TEST_F(PortfolioTest, UpdateMarketEquityEqualsNetLiquidationValue) {
    portfolio.updateMarket(MarketEvent("AAPL", 150.0, "2024-01-01"));
    const auto& pt = portfolio.getEquityCurve().back();
    EXPECT_DOUBLE_EQ(pt.equity, INITIAL_CASH);
    EXPECT_DOUBLE_EQ(pt.price,  150.0);
}

TEST_F(PortfolioTest, UpdateMarketReflectsOpenPositionValue) {
    portfolio.updateFill(FillEvent("AAPL", 10, 100.0, 0.0));
    portfolio.updateMarket(MarketEvent("AAPL", 120.0, "2024-01-02"));
    const auto& pt = portfolio.getEquityCurve().back();
    EXPECT_DOUBLE_EQ(pt.equity, 10'200.0);
}

// ---- Benchmark (buy-and-hold) -----------------------------------------------

TEST_F(PortfolioTest, BenchmarkEquityEqualsInitialCashAtFirstBar) {
    portfolio.updateMarket(MarketEvent("AAPL", 100.0, "2024-01-01"));
    const auto& pt = portfolio.getEquityCurve().back();
    EXPECT_DOUBLE_EQ(pt.benchmarkEquity, INITIAL_CASH);
}

TEST_F(PortfolioTest, BenchmarkEquityScalesWithPriceChange) {
    portfolio.updateMarket(MarketEvent("AAPL", 100.0, "2024-01-01"));
    portfolio.updateMarket(MarketEvent("AAPL", 110.0, "2024-01-02"));
    const auto& pt = portfolio.getEquityCurve().back();
    // Buy-and-hold: 10000 * (110/100) = 11000
    EXPECT_DOUBLE_EQ(pt.benchmarkEquity, 11'000.0);
}

// ---- Order generation — LONG (risk-based sizing) ----------------------------

TEST_F(PortfolioTest, LongSignalGeneratesBuyOrder) {
    portfolio.updateMarket(MarketEvent("AAPL", 150.0, "2024-01-01"));
    SignalEvent  signal("AAPL", SignalType::LONG);
    OrderEvent   order = portfolio.generateOrder(signal);
    EXPECT_EQ(order.orderType, OrderType::BUY);
    EXPECT_DOUBLE_EQ(order.price, 150.0);
}

TEST_F(PortfolioTest, LongSignalQuantityIsRiskBased) {
    // equity=$10000, riskFraction=10%, price=$150
    // qty = floor(10000 * 0.10 / 150) = floor(6.67) = 6
    portfolio.updateMarket(MarketEvent("AAPL", 150.0, "2024-01-01"));
    SignalEvent signal("AAPL", SignalType::LONG);
    OrderEvent  order = portfolio.generateOrder(signal);
    EXPECT_EQ(order.quantity, 6);
}

TEST_F(PortfolioTest, LongSignalMinimumQuantityIsOne) {
    // Very high price: floor(10000 * 0.10 / 200000) = 0, clamped to 1
    portfolio.updateMarket(MarketEvent("AAPL", 200'000.0, "2024-01-01"));
    SignalEvent signal("AAPL", SignalType::LONG);
    OrderEvent  order = portfolio.generateOrder(signal);
    EXPECT_EQ(order.quantity, 1);
}

// ---- Order generation — EXIT (full position close) --------------------------

TEST_F(PortfolioTest, ExitSignalWithNoPositionGeneratesHoldOrder) {
    portfolio.updateMarket(MarketEvent("AAPL", 150.0, "2024-01-01"));
    SignalEvent signal("AAPL", SignalType::EXIT);
    OrderEvent  order = portfolio.generateOrder(signal);
    EXPECT_EQ(order.orderType, OrderType::HOLD);
    EXPECT_EQ(order.quantity,  0);
}

TEST_F(PortfolioTest, ExitSignalFullyClosesOpenPosition) {
    // Open a 7-share position, then EXIT — should sell all 7
    portfolio.updateMarket(MarketEvent("AAPL", 150.0, "2024-01-01"));
    portfolio.updateFill(FillEvent("AAPL", 7, 150.0, 0.0));

    SignalEvent signal("AAPL", SignalType::EXIT);
    OrderEvent  order = portfolio.generateOrder(signal);

    EXPECT_EQ(order.orderType, OrderType::SELL);
    EXPECT_EQ(order.quantity,  7);
    EXPECT_DOUBLE_EQ(order.price, 150.0);
}

TEST_F(PortfolioTest, ShortSignalGeneratesSellOrder) {
    portfolio.updateMarket(MarketEvent("AAPL", 150.0, "2024-01-01"));
    SignalEvent signal("AAPL", SignalType::SHORT);
    OrderEvent  order = portfolio.generateOrder(signal);
    EXPECT_EQ(order.orderType, OrderType::SELL);
}

// ---- CSV export -------------------------------------------------------------

TEST_F(PortfolioTest, ExportEquityCurveWritesValidCsvWithBenchmarkColumn) {
    portfolio.updateMarket(MarketEvent("AAPL", 100.0, "2024-01-01"));
    portfolio.updateMarket(MarketEvent("AAPL", 110.0, "2024-01-02"));

    const std::string path = "/tmp/test_equity_curve.csv";
    portfolio.exportEquityCurve(path);

    std::ifstream file(path);
    ASSERT_TRUE(file.is_open());

    std::string header;
    std::getline(file, header);
    EXPECT_EQ(header, "timestamp,equity,price,benchmark_equity");

    int line_count = 0;
    std::string line;
    while (std::getline(file, line)) {
        EXPECT_FALSE(line.empty());
        ++line_count;
    }
    EXPECT_EQ(line_count, 2);
}

TEST_F(PortfolioTest, ExportTradesWritesQuantityColumn) {
    portfolio.updateFill(FillEvent("AAPL", 5, 100.0, 0.0));

    const std::string path = "/tmp/test_trades.csv";
    portfolio.exportTrades(path);

    std::ifstream file(path);
    ASSERT_TRUE(file.is_open());

    std::string header;
    std::getline(file, header);
    EXPECT_EQ(header, "timestamp,symbol,price,quantity,direction,profit,pnl");
}

// ---------------------------------------------------------------------------
// rankVector — Task 4.5
// ---------------------------------------------------------------------------

TEST(RankVector, DistinctValuesRankedCorrectly) {
    const auto r = Portfolio::rankVector({3.0, 1.0, 2.0});
    EXPECT_DOUBLE_EQ(r[0], 3.0);
    EXPECT_DOUBLE_EQ(r[1], 1.0);
    EXPECT_DOUBLE_EQ(r[2], 2.0);
}

TEST(RankVector, TiedValuesReceiveAverageRank) {
    const auto r = Portfolio::rankVector({1.0, 1.0, 3.0});
    EXPECT_DOUBLE_EQ(r[0], 1.5);
    EXPECT_DOUBLE_EQ(r[1], 1.5);
    EXPECT_DOUBLE_EQ(r[2], 3.0);
}

TEST(RankVector, AllTiedValuesReceiveMiddleRank) {
    const auto r = Portfolio::rankVector({5.0, 5.0, 5.0});
    EXPECT_DOUBLE_EQ(r[0], 2.0);
    EXPECT_DOUBLE_EQ(r[1], 2.0);
    EXPECT_DOUBLE_EQ(r[2], 2.0);
}

TEST(RankVector, SingleElementRankIsOne) {
    const auto r = Portfolio::rankVector({42.0});
    ASSERT_EQ(r.size(), 1u);
    EXPECT_DOUBLE_EQ(r[0], 1.0);
}

TEST(RankVector, AlreadySortedAscendingRanksCorrectly) {
    const auto r = Portfolio::rankVector({10.0, 20.0, 30.0});
    EXPECT_DOUBLE_EQ(r[0], 1.0);
    EXPECT_DOUBLE_EQ(r[1], 2.0);
    EXPECT_DOUBLE_EQ(r[2], 3.0);
}

// ---------------------------------------------------------------------------
// spearmanCorr — Task 4.5
// ---------------------------------------------------------------------------

TEST(SpearmanCorr, IdenticalSeriesReturnsOne) {
    const std::deque<double> x = {1.0, 2.0, 3.0, 4.0, 5.0};
    EXPECT_DOUBLE_EQ(Portfolio::spearmanCorr(x, x), 1.0);
}

TEST(SpearmanCorr, PerfectlyInverseSeriesReturnsMinusOne) {
    const std::deque<double> x = {1.0, 2.0, 3.0, 4.0, 5.0};
    const std::deque<double> y = {5.0, 4.0, 3.0, 2.0, 1.0};
    EXPECT_DOUBLE_EQ(Portfolio::spearmanCorr(x, y), -1.0);
}

TEST(SpearmanCorr, OutlierDoesNotDistortMonotonicRelationship) {
    // x has a large outlier; monotonic rank order between x and y is preserved
    const std::deque<double> x = {1.0, 2.0, 3.0, 4.0, 100.0};
    const std::deque<double> y = {2.0, 4.0, 6.0, 8.0, 10.0};
    // Ranks of x: {1, 2, 3, 4, 5}; ranks of y: {1, 2, 3, 4, 5} → Spearman = 1.0
    EXPECT_DOUBLE_EQ(Portfolio::spearmanCorr(x, y), 1.0);
}

TEST(SpearmanCorr, ResultIsAlwaysInBounds) {
    const std::deque<double> x = {3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0};
    const std::deque<double> y = {7.0, 2.0, 8.0, 1.0, 8.0, 2.0, 8.0, 4.0};
    const double r = Portfolio::spearmanCorr(x, y);
    EXPECT_GE(r, -1.0);
    EXPECT_LE(r,  1.0);
}

TEST(SpearmanCorr, SingleElementSeriesReturnsZero) {
    const std::deque<double> x = {1.0};
    const std::deque<double> y = {2.0};
    EXPECT_DOUBLE_EQ(Portfolio::spearmanCorr(x, y), 0.0);
}

TEST(SpearmanCorr, UnequalLengthsUsesShortestSuffix) {
    // x is longer; should use last 3 elements of x against all 3 of y
    const std::deque<double> x = {99.0, 1.0, 2.0, 3.0};
    const std::deque<double> y = {1.0,  2.0, 3.0};
    EXPECT_DOUBLE_EQ(Portfolio::spearmanCorr(x, y), 1.0);
}
