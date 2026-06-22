#include "market/FeatureCSVDataHandler.hpp"
#include "events/FeatureMarketEvent.hpp"

#include <ctime>
#include <iomanip>
#include <memory>
#include <spdlog/spdlog.h>
#include <sstream>
#include <stdexcept>

// ---------------------------------------------------------------------------
// Construction: parse header, map column names → indices
// ---------------------------------------------------------------------------

FeatureCSVDataHandler::FeatureCSVDataHandler(
    const std::string&              filename,
    const std::string&              symbol,
    const std::vector<std::string>& featureColumns,
    const std::string&              closeColumn,
    const std::string&              dateColumn)
    : symbol_(symbol)
{
    file_.open(filename);
    if (!file_.is_open())
        throw std::runtime_error("FeatureCSVDataHandler: cannot open " + filename);

    // Read and parse the header row
    std::string header;
    if (!std::getline(file_, header))
        throw std::runtime_error("FeatureCSVDataHandler: empty file " + filename);

    auto cols = splitCSVRow(header);

    // Build name → index map
    std::unordered_map<std::string, int> colMap;
    for (int i = 0; i < static_cast<int>(cols.size()); ++i)
        colMap[cols[i]] = i;

    // Resolve date column
    if (!colMap.count(dateColumn))
        throw std::runtime_error("FeatureCSVDataHandler: missing column '" + dateColumn + "'");
    dateColIndex_ = colMap[dateColumn];

    // Resolve close/price column
    if (!colMap.count(closeColumn))
        throw std::runtime_error("FeatureCSVDataHandler: missing column '" + closeColumn + "'");
    closeColIndex_ = colMap[closeColumn];

    // Resolve model feature columns (in the order the model expects them)
    featureColIndices_.reserve(featureColumns.size());
    for (const auto& name : featureColumns) {
        if (!colMap.count(name))
            throw std::runtime_error("FeatureCSVDataHandler: missing feature column '" + name + "'");
        featureColIndices_.push_back(colMap[name]);
    }

    spdlog::info("FeatureCSVDataHandler: opened {} ({} features)",
                 filename, featureColumns.size());
}

// ---------------------------------------------------------------------------
// Streaming
// ---------------------------------------------------------------------------

void FeatureCSVDataHandler::streamNext(EventQueue& queue) {
    std::string line;
    if (!std::getline(file_, line) || line.empty())
        return;

    auto fields = splitCSVRow(line);

    // Extract close price for MarketEvent.price
    double closePrice = std::stod(fields[closeColIndex_]);

    // Extract timestamp
    const std::string& timestamp = fields[dateColIndex_];

    // Gap detection: weekends are 2 days; anything >3 signals a missing bar
    if (!prevTimestamp_.empty()) {
        const int gap = daysBetween(prevTimestamp_, timestamp);
        if (gap > 3) {
            spdlog::warn("Date gap in {}: {}-day gap between {} and {}",
                         symbol_, gap, prevTimestamp_, timestamp);
            ++gapCount_;
        }
    }
    prevTimestamp_ = timestamp;

    // Extract features in model order
    std::vector<double> features;
    features.reserve(featureColIndices_.size());
    for (int idx : featureColIndices_) {
        const auto& val = fields[idx];
        features.push_back(val.empty() ? 0.0 : std::stod(val));
    }

    // Compute time marks from the date string
    auto timeMark = parseTimeMark(timestamp);

    queue.push(std::make_shared<FeatureMarketEvent>(
        symbol_, closePrice, timestamp,
        std::move(features), std::move(timeMark)));
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

std::vector<std::string> FeatureCSVDataHandler::splitCSVRow(
    const std::string& line) const
{
    std::vector<std::string> tokens;
    std::istringstream ss(line);
    std::string token;
    while (std::getline(ss, token, ','))
        tokens.push_back(token);
    return tokens;
}

int FeatureCSVDataHandler::daysBetween(const std::string& t1,
                                        const std::string& t2)
{
    auto parseDate = [](const std::string& s) -> std::time_t {
        std::tm tm = {};
        std::istringstream ss(s);
        ss >> std::get_time(&tm, "%Y-%m-%d");
        tm.tm_isdst = -1;
        return std::mktime(&tm);
    };
    const double diff = std::difftime(parseDate(t2), parseDate(t1));
    return static_cast<int>(diff / 86400.0 + 0.5);
}

std::vector<double> FeatureCSVDataHandler::parseTimeMark(
    const std::string& timestamp)
{
    // Parse "YYYY-MM-DD" (or "YYYY-MM-DD HH:MM:SS" — only date part used)
    std::tm tm = {};
    std::istringstream ss(timestamp);
    ss >> std::get_time(&tm, "%Y-%m-%d");
    std::mktime(&tm);  // fills tm_wday (0 = Sunday)

    double month   = static_cast<double>(tm.tm_mon + 1);      // 1-12
    double day     = static_cast<double>(tm.tm_mday);          // 1-31
    // Convert to Python weekday convention: 0 = Monday … 6 = Sunday
    double weekday = static_cast<double>((tm.tm_wday + 6) % 7);

    return {month, day, weekday};
}
