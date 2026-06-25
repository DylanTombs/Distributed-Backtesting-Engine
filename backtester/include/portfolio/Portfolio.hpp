#pragma once

#include <deque>
#include <string>
#include <unordered_map>
#include <vector>

#include "../events/Events.hpp"
#include "../events/SignalEvent.hpp"
#include "../events/OrderEvent.hpp"
#include "../events/MarketEvent.hpp"
#include "../events/FillEvent.hpp"

// ---------------------------------------------------------------------------
// EquityPoint — one row of the equity curve.
// benchmarkEquity is the buy-and-hold value for the FIRST symbol seen.
// For multi-asset it represents equal-weight buy-and-hold of all symbols.
// ---------------------------------------------------------------------------
struct EquityPoint {
    std::string timestamp;
    double      equity          = 0.0;
    double      price           = 0.0;  ///< Close price of last-seen symbol
    double      benchmarkEquity = 0.0;
};

// ---------------------------------------------------------------------------
// Portfolio
//
// Extended for multi-asset simulation:
//   - Per-symbol buy-and-hold benchmark (equal-weight from first bars)
//   - Exposure caps: maxSymbolExposure and maxTotalExposure guard against
//     over-concentration before each LONG order is sized
//   - 60-day rolling Spearman correlation between each new signal's symbol
//     and all currently-held symbols; position size is discounted when
//     correlation exceeds the configured threshold (Task 4.5)
//
// Capital allocation (generateOrder):
//   baseQty     = floor(equity * riskFraction / price)
//   symbolCap   = floor(equity * (maxSymbolExposure - currentWeight) / price)
//   portfolioCap= floor(equity * (maxTotalExposure  - totalWeight)   / price)
//   qty         = max(1, min(baseQty, symbolCap, portfolioCap))
//   qty         = floor(qty * (1 - correlationDiscount))
// ---------------------------------------------------------------------------
class Portfolio {
public:
    explicit Portfolio(double initialCash,
                       double riskFraction      = 0.10,
                       double maxSymbolExposure = 0.20,
                       double maxTotalExposure  = 0.80,
                       int    correlationWindow = 60,
                       double correlationThreshold = 0.7);

    // ---- Trade record -------------------------------------------------------
    struct Trade {
        std::string timestamp;
        std::string symbol;
        double      price    = 0.0;
        int         quantity = 0;
        std::string direction;  ///< "BUY" or "SELL"
        bool        profit   = false;
        double      pnl      = 0.0; ///< Realised P&L for SELL trades; 0 for BUY
    };

    // ---- Core event handlers ------------------------------------------------
    void updateMarket(const MarketEvent& event);

    /**
     * Generates an order from a signal using risk-based sizing with
     * multi-asset exposure caps and correlation discounting.
     *
     * EXIT: sells the full current position for that symbol.
     * LONG/SHORT: sizes by risk fraction, clamps to exposure caps,
     *             then discounts for correlation with held positions.
     */
    OrderEvent generateOrder(const SignalEvent& signal);

    void updateFill(const FillEvent& fill);

    // ---- Accessors ----------------------------------------------------------
    double getCash()                              const;
    int    getPosition(const std::string& symbol) const;
    double getTotalEquity()                       const;

    const std::vector<EquityPoint>& getEquityCurve() const;
    const std::vector<Trade>&       getTrades()       const;

    // ---- Export -------------------------------------------------------------
    void exportEquityCurve(const std::string& filename) const;
    void exportTrades     (const std::string& filename) const;

    // ---- Correlation helpers (public for direct unit testing) ---------------

    /// Rank-transform v; tied values receive average rank (1-based).
    static std::vector<double> rankVector(const std::vector<double>& v);

    /// Spearman rank correlation of the last min(|x|,|y|) elements of x and y.
    static double spearmanCorr(const std::deque<double>& x,
                               const std::deque<double>& y);

private:
    double initialCash_;
    double cash_;
    double riskFraction_;
    double maxSymbolExposure_;
    double maxTotalExposure_;
    int    correlationWindow_;
    double correlationThreshold_;

    // Per-symbol state
    std::unordered_map<std::string, double>      latestPrices_;
    std::unordered_map<std::string, int>         positions_;
    std::unordered_map<std::string, std::string> latestTimestamps_;

    // Per-symbol benchmark (buy-and-hold initial price)
    std::unordered_map<std::string, double> benchmarkInitialPrice_;
    // Running sum of per-symbol benchmark equity (equal-weight across all symbols seen)
    std::unordered_map<std::string, double> benchmarkUnits_; ///< shares in benchmark per symbol

    // Rolling daily returns per symbol for correlation matrix
    std::unordered_map<std::string, std::deque<double>> returnHistory_;
    std::unordered_map<std::string, double>             prevPrice_;

    double lastBuyPrice_ = 0.0;

    std::vector<EquityPoint> equityCurve_;
    std::vector<Trade>       trades_;

    // ---- Private helpers ----------------------------------------------------
    double getTotalPositionValue() const;
    double getSymbolPositionValue(const std::string& symbol) const;

    /// Returns the correlation discount factor in [0.5, 1] to apply to qty.
    /// 1.0 = no discount; 0.5 = maximum 50% reduction at correlation 1.0.
    double correlationDiscount(const std::string& newSymbol) const;

    static double pearsonCorr(const std::deque<double>& x,
                              const std::deque<double>& y);
};
