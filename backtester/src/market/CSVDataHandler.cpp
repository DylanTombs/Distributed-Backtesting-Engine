#include "../../include/market/CSVDataHandler.hpp"
#include "../../include/events/MarketEvent.hpp"
#include <iostream>
#include <sstream>

CSVDataHandler::CSVDataHandler(const std::string& filename,
                               const std::string& symbol)
    : file(filename), symbol(symbol) {
    if (!file.is_open()) {
        std::cerr << "ERROR: Could not open file: " << filename << std::endl;
    }
    std::string header;
    std::getline(file, header);
}

void CSVDataHandler::streamNext(EventQueue& queue) {
    std::string line;
    // Advance until one valid row is emitted or the file is exhausted, so a
    // malformed row is skipped rather than silently ending the stream.
    while (std::getline(file, line)) {
        std::stringstream ss(line);
        std::string ts, open, high, low, close;

        std::getline(ss, ts, ',');
        std::getline(ss, open, ',');
        std::getline(ss, high, ',');
        std::getline(ss, low, ',');
        std::getline(ss, close, ',');

        try {
            const double p = std::stod(close);
            queue.push(std::make_shared<MarketEvent>(symbol, p, ts));
            return;
        } catch (const std::exception&) {
            std::cerr << "WARN: skipping malformed row: " << line << std::endl;
        }
    }
}
