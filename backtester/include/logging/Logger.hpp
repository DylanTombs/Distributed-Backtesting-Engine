#pragma once

#include <spdlog/spdlog.h>
#include <spdlog/sinks/basic_file_sink.h>
#include <spdlog/sinks/stdout_color_sinks.h>

#include <memory>
#include <string>

/// Initialise the spdlog default logger with a console + file sink.
/// Call once at process startup (ml_main.cpp) before any log calls.
/// If logFile is empty, only the console sink is attached.
inline void initLogger(const std::string& logFile,
                       spdlog::level::level_enum level = spdlog::level::info)
{
    std::vector<spdlog::sink_ptr> sinks;
    sinks.push_back(std::make_shared<spdlog::sinks::stdout_color_sink_mt>());

    if (!logFile.empty()) {
        sinks.push_back(
            std::make_shared<spdlog::sinks::basic_file_sink_mt>(logFile, /*truncate=*/true));
    }

    auto logger = std::make_shared<spdlog::logger>(
        "backtester", sinks.begin(), sinks.end());
    logger->set_level(level);
    logger->set_pattern("[%Y-%m-%d %H:%M:%S.%e] [%^%l%$] [%n] %v");
    spdlog::set_default_logger(logger);
}

/// Parse a YAML log-level string ("trace","debug","info","warn","error","critical")
/// into the spdlog enum.  Unknown strings default to info.
inline spdlog::level::level_enum parseLogLevel(const std::string& s)
{
    if (s == "trace")    return spdlog::level::trace;
    if (s == "debug")    return spdlog::level::debug;
    if (s == "warn")     return spdlog::level::warn;
    if (s == "error")    return spdlog::level::err;
    if (s == "critical") return spdlog::level::critical;
    return spdlog::level::info;
}
