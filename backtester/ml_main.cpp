/**
 * ml_main.cpp — ML backtester entry point.
 *
 * Wires together:
 *   BacktestConfig              (YAML-driven configuration)
 *   MultiAssetDataHandler       (synchronises N FeatureCSVDataHandlers by date)
 *   BacktestEngine              (MARKET→SIGNAL→ORDER→FILL event loop)
 *   MLStrategy (per symbol)     (transformer inference via LibTorch)
 *   PerformanceMetrics          (Sharpe, IR, drawdown, alpha)
 *
 * Usage:
 *   ./ml_backtest <backtest_config.yaml>
 *
 * The config YAML specifies all symbols, paths, and execution parameters.
 * See backtest_config.yaml for the full schema.
 */

#include "config/BacktestConfig.hpp"
#include "engine/BacktestEngine.hpp"
#include "logging/Logger.hpp"
#include "market/FeatureCSVDataHandler.hpp"
#include "market/MultiAssetDataHandler.hpp"
#include "portfolio/PerformanceMetrics.hpp"
#include "strategy/MLStrategy.hpp"
#include "strategy/Strategy.hpp"

#include <iomanip>
#include <iostream>
#include <memory>
#include <spdlog/spdlog.h>
#include <string>
#include <unordered_map>
#include <vector>

// ---------------------------------------------------------------------------
// Feature column list — must match load_args() in research/exportModel.py
// Order: auxilFeatures (index 0..N-1) then target ('close')
// ---------------------------------------------------------------------------
static const std::vector<std::string> MODEL_FEATURE_COLUMNS = {
    "high", "low", "volume", "adj close",
    "P", "R1", "R2", "R3", "S1", "S2", "S3",
    "obv", "volume_zscore",
    "rsi", "macd", "macds", "macdh",
    "sma", "lma", "sema", "lema",
    "overnight_gap",
    "return_lag_1", "return_lag_3", "return_lag_5",
    "volatility",
    "SR_K", "SR_D", "SR_RSI_K", "SR_RSI_D",
    "ATR", "HL_PCT", "PCT_CHG",
    "close"   // target — always last
};

// ---------------------------------------------------------------------------
// MultiSymbolStrategy
//
// Routes each MarketEvent to the per-symbol MLStrategy instance.
// This lets a single Strategy& reference satisfy the BacktestEngine API
// while each symbol maintains its own feature buffer and position flag.
// ---------------------------------------------------------------------------
class MultiSymbolStrategy : public Strategy {
public:
    void addSymbol(const std::string&       symbol,
                   std::unique_ptr<MLStrategy> strategy) {
        strategies_[symbol] = std::move(strategy);
    }

    void onMarketEvent(const MarketEvent& event, EventQueue& queue) override {
        const auto it = strategies_.find(event.symbol);
        if (it != strategies_.end())
            it->second->onMarketEvent(event, queue);
    }

private:
    std::unordered_map<std::string, std::unique_ptr<MLStrategy>> strategies_;
};

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
int main(int argc, char* argv[]) {
    if (argc < 2) {
        // Logger not yet initialised — use stderr directly for CLI usage error
        std::cerr << "Usage: " << argv[0] << " <backtest_config.yaml>\n";
        return 1;
    }

    const std::string configPath = argv[1];
    const BacktestConfig config  = BacktestConfig::loadFromYAML(configPath);

    // Initialise logger before any other log calls
    initLogger(config.logFile, parseLogLevel(config.logLevel));

    if (config.symbols.empty()) {
        spdlog::error("No symbols defined in {} (expected 'symbol' + 'feature_csv' keys)",
                      configPath);
        return 1;
    }

    // ---- Log config summary ------------------------------------------------
    spdlog::info("=== ML Backtest ===");
    spdlog::info("  Config:           {}", configPath);
    spdlog::info("  Symbols:          {}", config.symbols.size());
    spdlog::info("  Model:            {}", config.modelPt);
    spdlog::info("  Initial cash:    ${:.2f}", config.initialCash);
    spdlog::info("  Risk fraction:    {:.1f}%", config.riskFraction * 100.0);
    spdlog::info("  Half spread:      {:.4f}%", config.halfSpread * 100.0);
    spdlog::info("  Slippage:         {:.4f}%", config.slippageFraction * 100.0);
    spdlog::info("  Commission:      ${:.2f}/trade", config.commission);
    spdlog::info("  Buy threshold:    {:.3f}%", config.buyThreshold * 100.0);
    spdlog::info("  Exit threshold:   {:.3f}%", config.exitThreshold * 100.0);

    for (const auto& sym : config.symbols)
        spdlog::info("  Symbol: {}  csv={}", sym.symbol, sym.featureCsv);

    try {
        // ---- Build multi-asset data handler --------------------------------
        auto multi = std::make_unique<MultiAssetDataHandler>();

        for (const auto& sym : config.symbols) {
            auto handler = std::make_unique<FeatureCSVDataHandler>(
                sym.featureCsv,
                sym.symbol,
                MODEL_FEATURE_COLUMNS,
                /*closeColumn=*/ "close",
                /*dateColumn=*/  "timestamp");
            multi->addHandler(std::move(handler));
        }

        // ---- Build per-symbol strategies -----------------------------------
        MultiSymbolStrategy compositeStrategy;

        const int nFeatures = static_cast<int>(MODEL_FEATURE_COLUMNS.size());
        for (const auto& sym : config.symbols) {
            auto strat = std::make_unique<MLStrategy>(
                config.modelPt,
                config.featScalerCsv,
                config.targScalerCsv,
                /*seqLen=*/        30,
                /*nFeatures=*/     nFeatures,
                /*buyThreshold=*/  0.005,
                /*exitThreshold=*/ 0.0);
            compositeStrategy.addSymbol(sym.symbol, std::move(strat));
        }

        // ---- Run backtest --------------------------------------------------
        BacktestEngine engine(compositeStrategy, *multi, config);
        engine.run();

        // ---- Results -------------------------------------------------------
        auto& portfolio = engine.getPortfolio();
        auto& curve     = portfolio.getEquityCurve();
        auto& trades    = portfolio.getTrades();

        if (curve.empty()) {
            spdlog::warn("No market data processed — check feature CSV paths");
            return 0;
        }

        const PerformanceMetrics metrics =
            PerformanceMetrics::compute(curve, config.riskFreeRate);
        metrics.print(std::cout);

        int wins = 0;
        for (const auto& t : trades)
            if (t.profit) ++wins;

        spdlog::info("Total trades: {}", trades.size());
        if (!trades.empty())
            spdlog::info("Win rate: {:.1f}%",
                         100.0 * wins / static_cast<double>(trades.size()));

        // ---- Export --------------------------------------------------------
        const std::string outDir = config.outputDir.empty() ? "." : config.outputDir;
        const std::string equityOut = outDir + "/ml_equity.csv";
        const std::string tradesOut = outDir + "/ml_trades.csv";
        const std::string metricsOut= outDir + "/ml_metrics.csv";

        portfolio.exportEquityCurve(equityOut);
        portfolio.exportTrades(tradesOut);
        metrics.exportCSV(metricsOut);

        spdlog::info("Saved: {}  {}  {}", equityOut, tradesOut, metricsOut);

    } catch (const std::exception& e) {
        spdlog::error("Fatal: {}", e.what());
        return 1;
    }

    return 0;
}
