#pragma once

#include "DataHandler.hpp"
#include <fstream>
#include <string>

/**
 * CSVDataHandler — streams close prices from an OHLCV CSV
 * (columns: date/timestamp, open, high, low, close, ...).
 *
 * Emits one MarketEvent per row for `symbol`. Malformed rows are skipped
 * so a single bad line cannot abort a backtest.
 */
class CSVDataHandler : public DataHandler {
private:
    std::ifstream file;
    std::string symbol;

public:
    explicit CSVDataHandler(const std::string& filename,
                            const std::string& symbol = "AAPL");

    void streamNext(EventQueue& queue) override;
};
