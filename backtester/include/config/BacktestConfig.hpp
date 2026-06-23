#pragma once

#include <fstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

// ---------------------------------------------------------------------------
// SymbolConfig — path/symbol pair for one instrument in the backtest
// ---------------------------------------------------------------------------
struct SymbolConfig {
    std::string symbol;
    std::string featureCsv;
};

// ---------------------------------------------------------------------------
// BacktestConfig — flat runtime configuration.
//
// Loaded from the YAML config file.  All fields have sensible defaults so
// the engine runs in tests without a config file.
//
// Multi-symbol YAML layout (backward-compatible):
//   symbol:          AAPL          ← first symbol (required)
//   feature_csv:     /f/AAPL.csv   ← first symbol CSV
//   symbol_1:        MSFT          ← additional symbols (optional)
//   feature_csv_1:   /f/MSFT.csv
//   symbol_2:        GOOGL
//   feature_csv_2:   /f/GOOGL.csv
//   ...up to symbol_19 / feature_csv_19
// ---------------------------------------------------------------------------
struct BacktestConfig {

    // ---- Symbols -----------------------------------------------------------
    std::vector<SymbolConfig> symbols;          ///< Populated from YAML
    std::string               modelPt;          ///< Path to transformer.pt
    std::string               featScalerCsv;    ///< Path to feature_scaler.csv
    std::string               targScalerCsv;    ///< Path to target_scaler.csv
    std::string               outputDir;        ///< Directory for CSV output

    // ---- Capital & sizing --------------------------------------------------
    double initialCash       = 100'000.0; ///< Starting portfolio capital ($)
    double riskFraction      = 0.10;      ///< Fraction of equity per LONG (10%)
    double maxSymbolExposure = 0.20;      ///< Max single-symbol weight (20%)
    double maxTotalExposure  = 0.80;      ///< Max total position weight (80%)
    int    maxPositionSize   = 10'000;    ///< Hard per-symbol share cap

    // ---- Execution realism -------------------------------------------------
    double halfSpread        = 0.0005;    ///< Half bid-ask spread per fill
    double slippageFraction  = 0.0005;    ///< Directional slippage per trade
    double marketImpact      = 0.0;       ///< Price impact per share ($)
    double commission        = 1.0;       ///< Fixed commission per fill ($)

    // ---- Risk / performance ------------------------------------------------
    double riskFreeRate       = 0.0;  ///< Annualised risk-free rate (Sharpe base)
    int    correlationWindow  = 60;   ///< Rolling days for correlation matrix
    double correlationThreshold = 0.7; ///< Correlation above this discounts size

    // ---- Signal thresholds -------------------------------------------------
    double buyThreshold  = 0.005; ///< Fractional predicted upside required for LONG
    double exitThreshold = 0.000; ///< Fractional predicted decline required for EXIT

    // ---- Logging -----------------------------------------------------------
    std::string logLevel = "info";           ///< trace|debug|info|warn|error|critical
    std::string logFile  = "output/backtest.log"; ///< Log file path (empty = console only)

    // ---- Validation --------------------------------------------------------
    /// Throws std::invalid_argument if any field violates its constraint.
    void validate() const {
        auto require = [](bool ok, const char* msg) {
            if (!ok) throw std::invalid_argument(std::string("BacktestConfig: ") + msg);
        };
        auto fileExists = [](const std::string& p) -> bool {
            if (p.empty()) return true;   // optional paths skip the check
            std::ifstream f(p);
            return f.good();
        };

        require(initialCash        >  0.0,  "initialCash must be > 0");
        require(riskFraction       >  0.0 && riskFraction       <= 1.0,
                "riskFraction must be in (0, 1]");
        require(maxSymbolExposure  >  0.0 && maxSymbolExposure  <= 1.0,
                "maxSymbolExposure must be in (0, 1]");
        require(maxTotalExposure   >  0.0 && maxTotalExposure   <= 1.0,
                "maxTotalExposure must be in (0, 1]");
        require(maxPositionSize    >  0,     "maxPositionSize must be > 0");
        require(halfSpread         >= 0.0,   "halfSpread must be >= 0");
        require(slippageFraction   >= 0.0,   "slippageFraction must be >= 0");
        require(marketImpact       >= 0.0,   "marketImpact must be >= 0");
        require(commission         >= 0.0,   "commission must be >= 0");
        require(correlationWindow  >  0,     "correlationWindow must be > 0");
        require(correlationThreshold >= 0.0 && correlationThreshold <= 1.0,
                "correlationThreshold must be in [0, 1]");
        require(buyThreshold  >= 0.0 && buyThreshold  <= 0.1,
                "buyThreshold must be in [0, 0.1]");
        require(exitThreshold >= 0.0 && exitThreshold <= 0.1,
                "exitThreshold must be in [0, 0.1]");

        require(fileExists(modelPt),       "modelPt path does not exist");
        require(fileExists(featScalerCsv), "featScalerCsv path does not exist");
        require(fileExists(targScalerCsv), "targScalerCsv path does not exist");
    }

    // ---- Factory -----------------------------------------------------------
    static BacktestConfig loadFromYAML(const std::string& path) {
        BacktestConfig cfg;
        std::ifstream file(path);
        if (!file.is_open()) return cfg;

        auto kv = parseKeyValues(file);

        auto setDouble = [&](const std::string& key, double& f) {
            if (kv.count(key)) f = std::stod(kv.at(key));
        };
        auto setInt = [&](const std::string& key, int& f) {
            if (kv.count(key)) f = std::stoi(kv.at(key));
        };
        auto setStr = [&](const std::string& key, std::string& f) {
            if (kv.count(key)) f = kv.at(key);
        };

        // Paths
        setStr("model_pt",          cfg.modelPt);
        setStr("feature_scaler_csv",cfg.featScalerCsv);
        setStr("target_scaler_csv", cfg.targScalerCsv);
        setStr("output_dir",        cfg.outputDir);

        // Capital
        setDouble("initial_cash",       cfg.initialCash);
        setDouble("risk_fraction",      cfg.riskFraction);
        setDouble("max_symbol_exposure",cfg.maxSymbolExposure);
        setDouble("max_total_exposure", cfg.maxTotalExposure);
        setInt   ("max_position_size",  cfg.maxPositionSize);

        // Execution
        setDouble("half_spread",        cfg.halfSpread);
        setDouble("slippage_fraction",  cfg.slippageFraction);
        setDouble("market_impact",      cfg.marketImpact);
        setDouble("commission",         cfg.commission);

        // Risk
        setDouble("risk_free_rate",        cfg.riskFreeRate);
        setInt   ("correlation_window",    cfg.correlationWindow);
        setDouble("correlation_threshold", cfg.correlationThreshold);

        // Signal thresholds
        setDouble("buy_threshold",  cfg.buyThreshold);
        setDouble("exit_threshold", cfg.exitThreshold);

        // Logging
        setStr("log_level", cfg.logLevel);
        setStr("log_file",  cfg.logFile);

        // Symbol list: primary symbol + symbol_1..symbol_19
        if (kv.count("symbol") && kv.count("feature_csv"))
            cfg.symbols.push_back({kv.at("symbol"), kv.at("feature_csv")});

        for (int i = 1; i <= 19; ++i) {
            const std::string sk = "symbol_"      + std::to_string(i);
            const std::string fk = "feature_csv_" + std::to_string(i);
            if (kv.count(sk) && kv.count(fk))
                cfg.symbols.push_back({kv.at(sk), kv.at(fk)});
        }

        cfg.validate();
        return cfg;
    }

private:
    static std::unordered_map<std::string, std::string>
    parseKeyValues(std::istream& stream) {
        std::unordered_map<std::string, std::string> result;
        std::string line;
        while (std::getline(stream, line)) {
            auto comment = line.find('#');
            if (comment != std::string::npos) line = line.substr(0, comment);
            auto colon = line.find(':');
            if (colon == std::string::npos) continue;
            std::string key   = trim(line.substr(0, colon));
            std::string value = trim(line.substr(colon + 1));
            if (!key.empty() && !value.empty()) result[key] = value;
        }
        return result;
    }

    static std::string trim(const std::string& s) {
        const auto start = s.find_first_not_of(" \t\r\n");
        const auto end   = s.find_last_not_of(" \t\r\n");
        return (start == std::string::npos) ? "" : s.substr(start, end - start + 1);
    }
};
