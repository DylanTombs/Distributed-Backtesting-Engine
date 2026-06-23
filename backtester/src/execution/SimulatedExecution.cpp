#include "../../include/execution/SimulatedExecution.hpp"

#include <cmath>
#include <spdlog/spdlog.h>

SimulatedExecution::SimulatedExecution(double commission,
                                       double halfSpread,
                                       double slippageFraction,
                                       double marketImpact)
    : commission_(commission)
    , halfSpread_(halfSpread)
    , slippageFraction_(slippageFraction)
    , marketImpact_(marketImpact)
{}

FillEvent SimulatedExecution::executeOrder(const OrderEvent& order) {
    const double rawPrice = order.price;
    double       fillPrice = rawPrice;
    int          quantity  = order.quantity;

    if (order.orderType == OrderType::BUY) {
        // Buys are filled above mid: pay spread + slippage + market impact
        fillPrice = rawPrice * (1.0 + halfSpread_ + slippageFraction_)
                    + marketImpact_ * quantity;

    } else if (order.orderType == OrderType::SELL) {
        // Sells are filled below mid: receive less due to spread + slippage
        fillPrice = rawPrice * (1.0 - halfSpread_ - slippageFraction_)
                    - marketImpact_ * quantity;
        quantity  = -quantity; // negative → Portfolio reduces position
    }
    // HOLD orders should never reach execution; pass through as no-op
    // (quantity stays 0, fill has no effect)

    const double frictionCost =
        std::abs((fillPrice - rawPrice) * std::abs(quantity));

    spdlog::debug("Execution {} {}  symbol={}  qty={}  raw={:.4f}  fill={:.4f}"
                  "  friction={:.4f}  commission={:.2f}",
                  (order.orderType == OrderType::BUY ? "BUY " : "SELL"),
                  order.symbol, std::abs(quantity),
                  rawPrice, fillPrice, frictionCost, commission_);

    return FillEvent(order.symbol, quantity, fillPrice, commission_);
}
