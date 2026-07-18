#include "../../include/strategy/RuleStrategy.hpp"
#include "../../include/strategy/Indicators.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <sstream>
#include <stdexcept>

// ---------------------------------------------------------------------------
// Spec parsing
// ---------------------------------------------------------------------------

namespace {

std::string trim(const std::string& s) {
    const auto start = s.find_first_not_of(" \t\r\n");
    const auto end   = s.find_last_not_of(" \t\r\n");
    return (start == std::string::npos) ? "" : s.substr(start, end - start + 1);
}

void fail(const std::string& msg) {
    throw std::invalid_argument("RuleSpec: " + msg);
}

RuleIndicator parseIndicator(const std::string& name) {
    if (name == "PRICE")  return RuleIndicator::PRICE;
    if (name == "SMA")    return RuleIndicator::SMA;
    if (name == "EMA")    return RuleIndicator::EMA;
    if (name == "RSI")    return RuleIndicator::RSI;
    if (name == "HIGH_N") return RuleIndicator::HIGH_N;
    if (name == "LOW_N")  return RuleIndicator::LOW_N;
    fail("unknown indicator '" + name + "'");
    return RuleIndicator::PRICE;  // unreachable
}

RuleOperand parseOperand(const std::string& token) {
    RuleOperand op;
    const auto colon = token.find(':');
    if (colon == std::string::npos) {
        op.indicator = parseIndicator(token);
        if (op.indicator != RuleIndicator::PRICE)
            fail("indicator '" + token + "' requires a period, e.g. " + token + ":14");
        return op;
    }
    op.indicator = parseIndicator(token.substr(0, colon));
    if (op.indicator == RuleIndicator::PRICE)
        fail("PRICE takes no period");
    try {
        op.period = std::stoi(token.substr(colon + 1));
    } catch (const std::exception&) {
        fail("invalid period in '" + token + "'");
    }
    if (op.period < RuleSpec::MIN_PERIOD || op.period > RuleSpec::MAX_PERIOD)
        fail("period in '" + token + "' outside [" +
             std::to_string(RuleSpec::MIN_PERIOD) + ", " +
             std::to_string(RuleSpec::MAX_PERIOD) + "]");
    return op;
}

RuleOp parseOp(const std::string& token) {
    if (token == "<")             return RuleOp::LT;
    if (token == ">")             return RuleOp::GT;
    if (token == "crosses_above") return RuleOp::CROSS_ABOVE;
    if (token == "crosses_below") return RuleOp::CROSS_BELOW;
    fail("unknown operator '" + token + "'");
    return RuleOp::GT;  // unreachable
}

RuleCondition parseCondition(const std::string& text) {
    std::istringstream ss(text);
    std::string lhsTok, opTok, rhsTok, extra;
    if (!(ss >> lhsTok >> opTok >> rhsTok))
        fail("condition '" + text + "' must be '<lhs> <op> <rhs>'");
    if (ss >> extra)
        fail("unexpected token '" + extra + "' in condition '" + text + "'");

    RuleCondition cond;
    cond.lhs = parseOperand(lhsTok);
    cond.op  = parseOp(opTok);

    // RHS: numeric literal or operand token
    if (!rhsTok.empty() &&
        (std::isdigit(static_cast<unsigned char>(rhsTok[0])) ||
         rhsTok[0] == '-' || rhsTok[0] == '.')) {
        try {
            cond.value = std::stod(rhsTok);
            cond.rhsIsValue = true;
        } catch (const std::exception&) {
            fail("invalid numeric value '" + rhsTok + "'");
        }
    } else {
        cond.rhsIsValue = false;
        cond.rhs = parseOperand(rhsTok);
    }

    // Cross conditions need two evolving series; against a constant they
    // degenerate to </> and hide user intent — reject explicitly.
    if ((cond.op == RuleOp::CROSS_ABOVE || cond.op == RuleOp::CROSS_BELOW) &&
        cond.rhsIsValue)
        fail("cross operators require an indicator on the right-hand side");

    return cond;
}

int operandWarmup(const RuleOperand& op) {
    switch (op.indicator) {
        case RuleIndicator::PRICE:  return 1;
        case RuleIndicator::RSI:    return op.period + 1;  // needs p diffs
        case RuleIndicator::HIGH_N:
        case RuleIndicator::LOW_N:  return op.period + 1;  // excludes current bar
        default:                    return op.period;
    }
}

std::string operandKey(const RuleOperand& op) {
    switch (op.indicator) {
        case RuleIndicator::PRICE:  return "PRICE";
        case RuleIndicator::SMA:    return "SMA:"    + std::to_string(op.period);
        case RuleIndicator::EMA:    return "EMA:"    + std::to_string(op.period);
        case RuleIndicator::RSI:    return "RSI:"    + std::to_string(op.period);
        case RuleIndicator::HIGH_N: return "HIGH_N:" + std::to_string(op.period);
        case RuleIndicator::LOW_N:  return "LOW_N:"  + std::to_string(op.period);
    }
    return "PRICE";
}

}  // namespace

RuleSpec RuleSpec::loadFromFile(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open())
        fail("cannot open spec file: " + path);

    RuleSpec spec;
    bool sawVersion = false;
    std::string line;
    while (std::getline(file, line)) {
        const auto comment = line.find('#');
        if (comment != std::string::npos) line = line.substr(0, comment);
        line = trim(line);
        if (line.empty()) continue;

        const auto colon = line.find(':');
        if (colon == std::string::npos)
            fail("malformed line '" + line + "'");
        const std::string key   = trim(line.substr(0, colon));
        const std::string value = trim(line.substr(colon + 1));

        if (key == "version") {
            spec.version = std::stoi(value);
            if (spec.version != 1 && spec.version != 2)
                fail("unsupported spec version " + value);
            sawVersion = true;
        } else if (key == "name") {
            spec.name = value.substr(0, 64);
        } else if (key == "direction") {
            // Version-gated so a v1-only consumer can never mis-run a short
            // spec as long (ADR-049).
            if (spec.version != 2)
                fail("'direction' requires version: 2 (declared before it)");
            if (value == "long")       spec.direction = RuleDirection::LONG;
            else if (value == "short") spec.direction = RuleDirection::SHORT;
            else fail("direction must be 'long' or 'short', got '" + value + "'");
        } else if (key == "entry") {
            spec.entry.push_back(parseCondition(value));
        } else if (key == "exit") {
            spec.exit.push_back(parseCondition(value));
        } else {
            fail("unknown key '" + key + "'");
        }
    }

    if (!sawVersion) fail("missing 'version' line");
    if (spec.entry.empty()) fail("at least one entry condition is required");
    if (spec.entry.size() > MAX_CONDITIONS_PER_SIDE ||
        spec.exit.size()  > MAX_CONDITIONS_PER_SIDE)
        fail("at most " + std::to_string(MAX_CONDITIONS_PER_SIDE) +
             " conditions per side");
    return spec;
}

int RuleSpec::warmupBars() const {
    int w = 1;
    auto consider = [&w](const RuleCondition& c) {
        w = std::max(w, operandWarmup(c.lhs));
        if (!c.rhsIsValue) w = std::max(w, operandWarmup(c.rhs));
    };
    for (const auto& c : entry) consider(c);
    for (const auto& c : exit)  consider(c);
    return w;
}

// ---------------------------------------------------------------------------
// Strategy
// ---------------------------------------------------------------------------

RuleStrategy::RuleStrategy(RuleSpec spec)
    : spec_(std::move(spec)), warmup_(spec_.warmupBars()) {}

double RuleStrategy::indicatorValue(const RuleOperand& op) const {
    switch (op.indicator) {
        case RuleIndicator::PRICE:  return closes_.back();
        case RuleIndicator::SMA:    return indicators::sma(closes_, op.period);
        case RuleIndicator::EMA:    return indicators::emaSeeded(closes_, op.period);
        case RuleIndicator::RSI:    return indicators::rsi(closes_, op.period);
        case RuleIndicator::HIGH_N: return indicators::highestBefore(closes_, op.period);
        case RuleIndicator::LOW_N:  return indicators::lowestBefore(closes_, op.period);
    }
    return closes_.back();
}

std::map<std::string, double> RuleStrategy::computeAll() const {
    std::map<std::string, double> values;
    auto add = [this, &values](const RuleOperand& op) {
        values.emplace(operandKey(op), indicatorValue(op));
    };
    for (const auto& c : spec_.entry) { add(c.lhs); if (!c.rhsIsValue) add(c.rhs); }
    for (const auto& c : spec_.exit)  { add(c.lhs); if (!c.rhsIsValue) add(c.rhs); }
    return values;
}

bool RuleStrategy::conditionsMet(
        const std::vector<RuleCondition>& conds,
        const std::map<std::string, double>& current) const {
    for (const auto& c : conds) {
        const double lhs = current.at(operandKey(c.lhs));
        const double rhs = c.rhsIsValue ? c.value : current.at(operandKey(c.rhs));

        switch (c.op) {
            case RuleOp::LT:
                if (!(lhs < rhs)) return false;
                break;
            case RuleOp::GT:
                if (!(lhs > rhs)) return false;
                break;
            case RuleOp::CROSS_ABOVE: {
                if (!hasPrev_) return false;
                const double pl = prevValues_.at(operandKey(c.lhs));
                const double pr = prevValues_.at(operandKey(c.rhs));
                if (!(pl <= pr && lhs > rhs)) return false;
                break;
            }
            case RuleOp::CROSS_BELOW: {
                if (!hasPrev_) return false;
                const double pl = prevValues_.at(operandKey(c.lhs));
                const double pr = prevValues_.at(operandKey(c.rhs));
                if (!(pl >= pr && lhs < rhs)) return false;
                break;
            }
        }
    }
    return true;
}

void RuleStrategy::onMarketEvent(const MarketEvent& event, EventQueue& queue) {
    closes_.push_back(event.price);
    if (static_cast<int>(closes_.size()) > warmup_ + 1)
        closes_.pop_front();

    if (static_cast<int>(closes_.size()) < warmup_)
        return;

    const auto current = computeAll();

    const SignalType entrySignal = (spec_.direction == RuleDirection::SHORT)
        ? SignalType::SHORT : SignalType::LONG;

    if (!inPosition_ && conditionsMet(spec_.entry, current)) {
        inPosition_ = true;
        queue.push(std::make_shared<SignalEvent>(event.symbol, entrySignal));
    } else if (inPosition_ && !spec_.exit.empty() &&
               conditionsMet(spec_.exit, current)) {
        inPosition_ = false;
        queue.push(std::make_shared<SignalEvent>(event.symbol, SignalType::EXIT));
    }

    prevValues_ = current;
    hasPrev_ = true;
}
