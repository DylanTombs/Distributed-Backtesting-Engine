#include "strategy/MLStrategy.hpp"
#include "events/FeatureMarketEvent.hpp"
#include "events/SignalEvent.hpp"

#include <memory>
#include <spdlog/spdlog.h>
#include <stdexcept>

#ifdef ML_STRATEGY_ENABLED
std::unordered_map<std::string,
                   std::shared_ptr<torch::jit::script::Module>>
    MLStrategy::modelCache_;
#endif

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

MLStrategy::MLStrategy(const std::string& modelPath,
                       const std::string& featureScalerPath,
                       const std::string& targetScalerPath,
                       int    seqLen,
                       int    nFeatures,
                       double buyThreshold,
                       double exitThreshold,
                       bool   allowShort)
    : seqLen_(seqLen)
    , nFeatures_(nFeatures)
    , buyThreshold_(buyThreshold)
    , exitThreshold_(exitThreshold)
    , allowShort_(allowShort)
{
    featureScaler_ = ScalerParams::loadFromCSV(featureScalerPath);
    targetScaler_  = ScalerParams::loadFromCSV(targetScalerPath);

    if (static_cast<int>(featureScaler_.mean.size()) != nFeatures_) {
        throw std::runtime_error(
            "MLStrategy: feature scaler has " +
            std::to_string(featureScaler_.mean.size()) +
            " entries but nFeatures=" + std::to_string(nFeatures_));
    }

#ifdef ML_STRATEGY_ENABLED
    try {
        auto it = modelCache_.find(modelPath);
        if (it != modelCache_.end()) {
            model_       = it->second;
            modelLoaded_ = true;
            spdlog::debug("MLStrategy: reusing cached model from {}", modelPath);
        } else {
            auto m = std::make_shared<torch::jit::script::Module>(
                torch::jit::load(modelPath));
            m->eval();
            modelCache_[modelPath] = m;
            model_       = m;
            modelLoaded_ = true;
            spdlog::info("MLStrategy: loaded model from {}", modelPath);
        }
    } catch (const c10::Error& e) {
        spdlog::error("MLStrategy: failed to load model: {}", e.what());
        modelLoaded_ = false;
    }
#else
    (void)modelPath;  // suppress unused-parameter warning
    spdlog::warn("MLStrategy: compiled without LibTorch — inference disabled");
#endif
}

// ---------------------------------------------------------------------------
// Event handler
// ---------------------------------------------------------------------------

void MLStrategy::onMarketEvent(const MarketEvent& event, EventQueue& queue) {
    // Only process events that carry a full feature vector
    const auto* featEvent = dynamic_cast<const FeatureMarketEvent*>(&event);
    if (!featEvent) return;

    if (static_cast<int>(featEvent->features.size()) != nFeatures_) {
        spdlog::error("MLStrategy: expected {} features, got {} — skipping bar",
                      nFeatures_, featEvent->features.size());
        return;
    }

    // Scale raw features and push to rolling buffer
    auto scaledFeatures = featureScaler_.transform(featEvent->features);
    featureBuffer_.push_back(std::move(scaledFeatures));
    timeMarkBuffer_.push_back(featEvent->timeMark);

    if (static_cast<int>(featureBuffer_.size()) > seqLen_) {
        featureBuffer_.pop_front();
        timeMarkBuffer_.pop_front();
    }

    if (!bufferFull()) return;

    double predictedClose = runInference();
    if (predictedClose < 0.0) return;   // inference not available

    const double currentClose = event.price;
    const bool bullish = predictedClose > currentClose * (1.0 + buyThreshold_);
    const bool bearish = predictedClose < currentClose * (1.0 - exitThreshold_);

    switch (positionDir_) {
        case PositionDirection::FLAT:
            if (bullish) {
                queue.push(std::make_shared<SignalEvent>(event.symbol, SignalType::LONG));
                positionDir_ = PositionDirection::LONG;
                spdlog::debug("MLStrategy LONG  {} @ {}  price={:.4f}  pred={:.4f}",
                              event.symbol, event.timestamp, currentClose, predictedClose);
            } else if (bearish && allowShort_) {
                queue.push(std::make_shared<SignalEvent>(event.symbol, SignalType::SHORT));
                positionDir_ = PositionDirection::SHORT;
                spdlog::debug("MLStrategy SHORT {} @ {}  price={:.4f}  pred={:.4f}",
                              event.symbol, event.timestamp, currentClose, predictedClose);
            }
            break;

        case PositionDirection::LONG:
            if (bearish) {
                queue.push(std::make_shared<SignalEvent>(event.symbol, SignalType::EXIT));
                positionDir_ = PositionDirection::FLAT;
                spdlog::debug("MLStrategy EXIT  {} @ {}  price={:.4f}  pred={:.4f}",
                              event.symbol, event.timestamp, currentClose, predictedClose);
            }
            break;

        case PositionDirection::SHORT:
            if (bullish) {
                queue.push(std::make_shared<SignalEvent>(event.symbol, SignalType::EXIT));
                positionDir_ = PositionDirection::FLAT;
                spdlog::debug("MLStrategy COVER {} @ {}  price={:.4f}  pred={:.4f}",
                              event.symbol, event.timestamp, currentClose, predictedClose);
            }
            break;
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

bool MLStrategy::bufferFull() const {
    return static_cast<int>(featureBuffer_.size()) == seqLen_;
}

double MLStrategy::runInference() const {
#ifdef ML_STRATEGY_ENABLED
    if (!modelLoaded_ || !bufferFull())
        return -1.0;

    torch::NoGradGuard no_grad;

    // Build xEnc: (1, seqLen, nFeatures)
    auto opts  = torch::TensorOptions().dtype(torch::kFloat32);
    auto xEnc  = torch::zeros({1, seqLen_, nFeatures_}, opts);
    auto xMark = torch::zeros({1, seqLen_, 3},           opts);

    for (int t = 0; t < seqLen_; ++t) {
        const auto& feat = featureBuffer_[t];
        for (int f = 0; f < nFeatures_; ++f)
            xEnc[0][t][f] = static_cast<float>(feat[f]);

        const auto& mark = timeMarkBuffer_[t];
        for (int m = 0; m < static_cast<int>(mark.size()); ++m)
            xMark[0][t][m] = static_cast<float>(mark[m]);
    }

    // TransformerInferenceWrapper.forward(xEnc, xMarkEnc) → (1, predLen, 1)
    std::vector<torch::jit::IValue> inputs = {xEnc, xMark};
    auto output = model_->forward(inputs).toTensor();

    // Take the first step of the prediction horizon
    float scaledPred = output[0][0][0].item<float>();

    // Inverse-scale to recover the original price
    return targetScaler_.inverseTransform(static_cast<double>(scaledPred));
#else
    return -1.0;
#endif
}
