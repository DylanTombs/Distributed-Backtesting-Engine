#include "../../include/events/MarketEvent.hpp"
#include "../../include/events/SignalEvent.hpp"
#include "../../include/events/OrderEvent.hpp"
#include "../../include/events/FillEvent.hpp"
#include "../../include/engine/BacktestEngine.hpp"

#include <spdlog/spdlog.h>

BacktestEngine::BacktestEngine(Strategy&            strategy,
                               DataHandler&         dataHandler,
                               const BacktestConfig& config)
    : strategy(strategy)
    , dataHandler(dataHandler)
    , portfolio(config.initialCash,
                config.riskFraction,
                config.maxSymbolExposure,
                config.maxTotalExposure,
                config.correlationWindow,
                config.correlationThreshold)
    , riskManager(config.maxPositionSize)
    , execution(config.commission,
                config.halfSpread,
                config.slippageFraction,
                config.marketImpact)
{}

void BacktestEngine::run() {
    spdlog::info("Backtest engine started");
    int barCount = 0;

    while (true) {

        if (queue.empty())
            dataHandler.streamNext(queue);

        if (queue.empty()) {
            spdlog::info("Backtest complete — {} bars processed", barCount);
            break;
        }

        auto event = queue.pop();

        switch (event->getType()) {

        case EventType::MARKET: {
            auto marketEvent = std::static_pointer_cast<MarketEvent>(event);
            portfolio.updateMarket(*marketEvent);
            strategy.onMarketEvent(*marketEvent, queue);
            ++barCount;
            break;
        }

        case EventType::SIGNAL: {
            auto signal = std::static_pointer_cast<SignalEvent>(event);
            auto order  = portfolio.generateOrder(*signal);

            // Suppress HOLD orders — nothing to execute
            if (order.orderType != OrderType::HOLD)
                queue.push(std::make_shared<OrderEvent>(order));
            break;
        }

        case EventType::ORDER: {
            auto order = std::static_pointer_cast<OrderEvent>(event);
            if (riskManager.approveOrder(*order)) {
                auto fill = execution.executeOrder(*order);
                queue.push(std::make_shared<FillEvent>(fill));
            }
            break;
        }

        case EventType::FILL: {
            auto fill = std::static_pointer_cast<FillEvent>(event);
            portfolio.updateFill(*fill);
            break;
        }

        }
    }
}

Portfolio& BacktestEngine::getPortfolio() {
    return portfolio;
}
