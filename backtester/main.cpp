/**
 * backtest — transparent-strategy CLI (Phase 8.2, no LibTorch).
 *
 * Usage:
 *   ./backtest <ohlcv_csv> <symbol> <strategy_spec> [output_dir]
 *
 * Runs the RuleStrategy described by <strategy_spec> (see RuleStrategy.hpp
 * for the format) over the OHLCV CSV and writes ml_equity.csv and
 * ml_trades.csv into <output_dir> (default: current directory) — the same
 * output contract as ml_backtest, so the API runner treats both binaries
 * identically.
 *
 * Exit codes: 0 success, 1 usage, 2 invalid spec, 3 runtime failure.
 */

#include "engine/BacktestEngine.hpp"
#include "market/CSVDataHandler.hpp"
#include "strategy/RuleStrategy.hpp"

#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char* argv[]) {
    if (argc < 4) {
        std::cerr << "Usage: " << argv[0]
                  << " <ohlcv_csv> <symbol> <strategy_spec> [output_dir]\n";
        return 1;
    }

    const std::string csvPath  = argv[1];
    const std::string symbol   = argv[2];
    const std::string specPath = argv[3];
    const std::string outDir   = (argc > 4) ? argv[4] : ".";

    try {
        const RuleSpec spec = RuleSpec::loadFromFile(specPath);
        const bool isShort = spec.direction == RuleDirection::SHORT;
        std::cout << "Strategy: " << (spec.name.empty() ? "unnamed" : spec.name)
                  << (isShort ? "  [short]" : "")
                  << "  (warmup " << spec.warmupBars() << " bars)\n";

        RuleStrategy strategy(spec);
        CSVDataHandler data(csvPath, symbol);

        BacktestConfig config;
        // Short specs need the portfolio's short path enabled; the spec is
        // the single source of direction so the two can't disagree (ADR-049).
        config.allowShort = isShort;
        BacktestEngine engine(strategy, data, config);

        engine.run();

        const auto& equity = engine.getPortfolio().getEquityCurve();
        if (equity.empty()) {
            std::cerr << "ERROR: no market data streamed from " << csvPath << "\n";
            return 3;
        }

        engine.getPortfolio().exportEquityCurve(outDir + "/ml_equity.csv");
        engine.getPortfolio().exportTrades(outDir + "/ml_trades.csv");

        std::cout << "Bars: " << equity.size()
                  << "  Final equity: " << equity.back().equity << "\n";
        return 0;

    } catch (const std::invalid_argument& e) {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 2;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 3;
    }
}
