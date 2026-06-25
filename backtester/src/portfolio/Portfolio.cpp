#include "../../include/portfolio/Portfolio.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <numeric>
#include <spdlog/spdlog.h>

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

Portfolio::Portfolio(double initialCash,
                     double riskFraction,
                     double maxSymbolExposure,
                     double maxTotalExposure,
                     int    correlationWindow,
                     double correlationThreshold)
    : initialCash_(initialCash)
    , cash_(initialCash)
    , riskFraction_(riskFraction)
    , maxSymbolExposure_(maxSymbolExposure)
    , maxTotalExposure_(maxTotalExposure)
    , correlationWindow_(correlationWindow)
    , correlationThreshold_(correlationThreshold)
{}

// ---------------------------------------------------------------------------
// Market update
// ---------------------------------------------------------------------------

void Portfolio::updateMarket(const MarketEvent& event) {
    const std::string& sym = event.symbol;
    const double price     = event.price;

    // Update rolling return history for correlation
    if (prevPrice_.count(sym) && prevPrice_.at(sym) > 0.0) {
        double ret = (price - prevPrice_.at(sym)) / prevPrice_.at(sym);
        returnHistory_[sym].push_back(ret);
        if (static_cast<int>(returnHistory_[sym].size()) > correlationWindow_)
            returnHistory_[sym].pop_front();
    }
    prevPrice_[sym] = price;

    latestPrices_[sym]     = price;
    latestTimestamps_[sym] = event.timestamp;

    // Per-symbol benchmark: buy the symbol at its first-seen price,
    // allocate initialCash / numSymbols (unknown at t=0, so use a running
    // equal-weight: each new symbol gets allocated the current portfolio's
    // per-symbol share = initialCash / nSeen).
    if (!benchmarkInitialPrice_.count(sym)) {
        benchmarkInitialPrice_[sym] = price;
        // Equal-weight: allocate initialCash_ / numSymbols to each instrument.
        // Number of symbols grows as new ones are seen; rebalance benchmark weights.
        const double perSymbol = initialCash_ / benchmarkInitialPrice_.size();
        for (auto& [s, ip] : benchmarkInitialPrice_)
            benchmarkUnits_[s] = perSymbol / ip;
    }

    // Benchmark equity = sum over all symbols of (units * currentPrice)
    double benchmarkEquity = 0.0;
    for (const auto& [s, units] : benchmarkUnits_) {
        if (latestPrices_.count(s))
            benchmarkEquity += units * latestPrices_.at(s);
    }

    equityCurve_.push_back({
        event.timestamp,
        getTotalEquity(),
        price,
        benchmarkEquity
    });
}

// ---------------------------------------------------------------------------
// Order generation — risk-based sizing with exposure + correlation caps
// ---------------------------------------------------------------------------

OrderEvent Portfolio::generateOrder(const SignalEvent& signal) {
    const std::string& sym = signal.symbol;

    const double price = latestPrices_.count(sym)
                         ? latestPrices_.at(sym) : 0.0;

    if (price <= 0.0) {
        spdlog::warn("Portfolio: no price for {} — order suppressed", sym);
        return OrderEvent(sym, OrderType::HOLD, 0, 0.0);
    }

    // ---- EXIT — liquidate the full position --------------------------------
    if (signal.signalType == SignalType::EXIT) {
        const int held = getPosition(sym);
        if (held <= 0)
            return OrderEvent(sym, OrderType::HOLD, 0, price);
        return OrderEvent(sym, OrderType::SELL, held, price);
    }

    // ---- LONG / BUY --------------------------------------------------------
    if (signal.signalType == SignalType::LONG ||
        signal.signalType == SignalType::BUY) {

        const double equity    = getTotalEquity();
        const double symValue  = getSymbolPositionValue(sym);
        const double totalPos  = getTotalPositionValue();

        // Fractional weights
        const double currentSymWeight   = symValue  / equity;
        const double currentTotalWeight = totalPos  / equity;

        // Check exposure caps — suppress order if already at limit
        if (currentSymWeight   >= maxSymbolExposure_) {
            spdlog::warn("Portfolio: {} symbol exposure cap reached ({:.1f}%) — order suppressed",
                         sym, currentSymWeight * 100.0);
            return OrderEvent(sym, OrderType::HOLD, 0, price);
        }
        if (currentTotalWeight >= maxTotalExposure_) {
            spdlog::warn("Portfolio: total exposure cap reached ({:.1f}%) — order suppressed",
                         currentTotalWeight * 100.0);
            return OrderEvent(sym, OrderType::HOLD, 0, price);
        }

        // Base quantity: risk fraction of equity
        const int baseQty = static_cast<int>(
            std::floor((equity * riskFraction_) / price));

        // Cap by remaining symbol headroom
        const int symCap = static_cast<int>(
            std::floor(equity * (maxSymbolExposure_ - currentSymWeight) / price));

        // Cap by remaining total headroom
        const int totalCap = static_cast<int>(
            std::floor(equity * (maxTotalExposure_ - currentTotalWeight) / price));

        int qty = std::max(1, std::min({baseQty, symCap, totalCap}));

        // Correlation discount: scale down when new symbol is highly correlated
        // with existing positions
        const double discount = correlationDiscount(sym);
        qty = std::max(1, static_cast<int>(std::floor(qty * discount)));

        if (discount < 1.0) {
            spdlog::warn("Portfolio: {} correlation discount {:.0f}% applied — final qty={}",
                         sym, (1.0 - discount) * 100.0, qty);
        }

        return OrderEvent(sym, OrderType::BUY, qty, price);
    }

    // ---- SHORT / SELL -------------------------------------------------------
    if (signal.signalType == SignalType::SHORT ||
        signal.signalType == SignalType::SELL) {
        const double equity = getTotalEquity();
        const int    qty    = std::max(1, static_cast<int>(
            std::floor((equity * riskFraction_) / price)));
        return OrderEvent(sym, OrderType::SELL, qty, price);
    }

    return OrderEvent(sym, OrderType::HOLD, 0, price);
}

// ---------------------------------------------------------------------------
// Fill update
// ---------------------------------------------------------------------------

void Portfolio::updateFill(const FillEvent& fill) {
    positions_[fill.symbol] += fill.quantity;
    cash_ -= fill.quantity * fill.price;
    cash_ -= fill.commission;

    const std::string ts = latestTimestamps_.count(fill.symbol)
                           ? latestTimestamps_.at(fill.symbol) : "";

    if (fill.quantity > 0) {
        lastBuyPrice_ = fill.price;
        trades_.push_back({ts, fill.symbol, fill.price, fill.quantity, "BUY", true, 0.0});
    } else {
        const int    qty    = -fill.quantity;
        const double pnl    = (fill.price - lastBuyPrice_) * qty - fill.commission;
        const bool   profit = pnl > 0.0;
        trades_.push_back({ts, fill.symbol, fill.price, qty, "SELL", profit, pnl});
    }
}

// ---------------------------------------------------------------------------
// Accessors
// ---------------------------------------------------------------------------

double Portfolio::getCash() const { return cash_; }

int Portfolio::getPosition(const std::string& symbol) const {
    const auto it = positions_.find(symbol);
    return (it != positions_.end()) ? it->second : 0;
}

double Portfolio::getTotalEquity() const {
    return cash_ + getTotalPositionValue();
}

const std::vector<EquityPoint>& Portfolio::getEquityCurve() const {
    return equityCurve_;
}

const std::vector<Portfolio::Trade>& Portfolio::getTrades() const {
    return trades_;
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

double Portfolio::getTotalPositionValue() const {
    double total = 0.0;
    for (const auto& [sym, qty] : positions_)
        if (latestPrices_.count(sym))
            total += qty * latestPrices_.at(sym);
    return total;
}

double Portfolio::getSymbolPositionValue(const std::string& symbol) const {
    const int qty = getPosition(symbol);
    if (qty == 0) return 0.0;
    if (!latestPrices_.count(symbol)) return 0.0;
    return qty * latestPrices_.at(symbol);
}

double Portfolio::correlationDiscount(const std::string& newSym) const {
    if (!returnHistory_.count(newSym)) return 1.0;
    const auto& newReturns = returnHistory_.at(newSym);
    if (newReturns.size() < 10) return 1.0;  // insufficient history

    double maxAbsCorr = 0.0;

    for (const auto& [sym, qty] : positions_) {
        if (qty <= 0) continue;           // no open position
        if (sym == newSym) continue;      // same symbol — not a correlation issue
        if (!returnHistory_.count(sym)) continue;

        const double corr = spearmanCorr(newReturns, returnHistory_.at(sym));
        maxAbsCorr = std::max(maxAbsCorr, std::abs(corr));
    }

    if (maxAbsCorr <= correlationThreshold_) return 1.0;

    // Linear discount from threshold to 1: at threshold → no discount,
    // at correlation 1.0 → 50% size reduction.
    const double excess   = maxAbsCorr - correlationThreshold_;
    const double maxRange = 1.0 - correlationThreshold_;
    const double reduction = 0.5 * (excess / maxRange);  // up to 50% reduction
    return 1.0 - reduction;
}

std::vector<double> Portfolio::rankVector(const std::vector<double>& v) {
    const int n = static_cast<int>(v.size());
    std::vector<int> idx(n);
    std::iota(idx.begin(), idx.end(), 0);
    std::sort(idx.begin(), idx.end(),
              [&v](int a, int b) { return v[a] < v[b]; });

    std::vector<double> ranks(n);
    int i = 0;
    while (i < n) {
        int j = i;
        while (j < n && v[idx[j]] == v[idx[i]]) ++j;
        const double avgRank = (i + 1.0 + j) / 2.0;
        for (int k = i; k < j; ++k)
            ranks[idx[k]] = avgRank;
        i = j;
    }
    return ranks;
}

double Portfolio::spearmanCorr(const std::deque<double>& x,
                               const std::deque<double>& y) {
    const int n = static_cast<int>(std::min(x.size(), y.size()));
    if (n < 2) return 0.0;

    const int xOff = static_cast<int>(x.size()) - n;
    const int yOff = static_cast<int>(y.size()) - n;

    const std::vector<double> xv(x.begin() + xOff, x.end());
    const std::vector<double> yv(y.begin() + yOff, y.end());

    const auto rx = rankVector(xv);
    const auto ry = rankVector(yv);

    const std::deque<double> rxd(rx.begin(), rx.end());
    const std::deque<double> ryd(ry.begin(), ry.end());
    return pearsonCorr(rxd, ryd);
}

double Portfolio::pearsonCorr(const std::deque<double>& x,
                              const std::deque<double>& y) {
    const int n = static_cast<int>(std::min(x.size(), y.size()));
    if (n < 2) return 0.0;

    // Use the last n elements from both
    const int xOff = static_cast<int>(x.size()) - n;
    const int yOff = static_cast<int>(y.size()) - n;

    double sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0, sumY2 = 0;
    for (int i = 0; i < n; ++i) {
        const double xi = x[xOff + i];
        const double yi = y[yOff + i];
        sumX  += xi;       sumY  += yi;
        sumXY += xi * yi;  sumX2 += xi * xi;  sumY2 += yi * yi;
    }
    const double denom = std::sqrt(
        (n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
    if (denom < 1e-12) return 0.0;
    return (n * sumXY - sumX * sumY) / denom;
}

// ---------------------------------------------------------------------------
// CSV export
// ---------------------------------------------------------------------------

void Portfolio::exportEquityCurve(const std::string& filename) const {
    std::ofstream file(filename);
    file << "timestamp,equity,price,benchmark_equity\n";
    for (const auto& pt : equityCurve_)
        file << pt.timestamp       << ","
             << pt.equity          << ","
             << pt.price           << ","
             << pt.benchmarkEquity << "\n";
}

void Portfolio::exportTrades(const std::string& filename) const {
    std::ofstream file(filename);
    file << "timestamp,symbol,price,quantity,direction,profit,pnl\n";
    for (const auto& t : trades_)
        file << t.timestamp << ","
             << t.symbol    << ","
             << t.price     << ","
             << t.quantity  << ","
             << t.direction << ","
             << t.profit    << ","
             << t.pnl       << "\n";
}
