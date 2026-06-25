#pragma once

#include "strategy/Strategy.hpp"
#include "strategy/ScalerParams.hpp"

#include <deque>
#include <string>
#include <vector>

// LibTorch is optional.  When cmake finds Torch, it defines
// ML_STRATEGY_ENABLED and links ${TORCH_LIBRARIES}.
#ifdef ML_STRATEGY_ENABLED
#include <torch/script.h>
#endif

/**
 * MLStrategy — transformer-based trading strategy.
 *
 * On each bar it:
 *  1. Receives a FeatureMarketEvent (dynamic_cast from MarketEvent).
 *  2. Applies the feature scaler and pushes to a rolling seqLen-bar buffer.
 *  3. Once the buffer is full, runs inference via the TorchScript model.
 *  4. Emits LONG  when predicted_close > current_close * (1 + buyThreshold).
 *  5. Emits EXIT  when holding and predicted_close < current_close * (1 - exitThreshold).
 *
 * The TorchScript model is the one exported by research/exportModel.py.
 * Scaler CSVs are exported by the same script via save_scaler_params().
 *
 * Without LibTorch (ML_STRATEGY_ENABLED not defined) the class compiles but
 * runInference() is a no-op — useful for unit testing the event-loop logic.
 */
class MLStrategy : public Strategy {
public:
    enum class PositionDirection { FLAT, LONG, SHORT };

    /**
     * @param modelPath         Path to models/transformer.pt
     * @param featureScalerPath Path to models/feature_scaler.csv
     * @param targetScalerPath  Path to models/target_scaler.csv
     * @param seqLen            Encoder sequence length (default 30, from exportModel.py)
     * @param nFeatures         Number of model input features (default 34 = encIn)
     * @param buyThreshold      Minimum predicted upside to trigger a LONG (0.5 %)
     * @param exitThreshold     Predicted drawdown below which to EXIT (0 = any decline)
     * @param allowShort        Enable SHORT signals (default: false)
     */
    MLStrategy(const std::string& modelPath,
               const std::string& featureScalerPath,
               const std::string& targetScalerPath,
               int    seqLen        = 30,
               int    nFeatures     = 34,
               double buyThreshold  = 0.005,
               double exitThreshold = 0.0,
               bool   allowShort    = false);

    void onMarketEvent(const MarketEvent& event, EventQueue& queue) override;

private:
    int    seqLen_;
    int    nFeatures_;
    double buyThreshold_;
    double exitThreshold_;
    bool   allowShort_    = false;
    PositionDirection positionDir_ = PositionDirection::FLAT;

    ScalerParams featureScaler_;
    ScalerParams targetScaler_;

    // Rolling window — oldest bar at front, newest at back
    std::deque<std::vector<double>> featureBuffer_;
    std::deque<std::vector<double>> timeMarkBuffer_;

    bool   bufferFull()     const;

    /**
     * Runs the TorchScript model and returns the predicted close price
     * for the first step of the prediction horizon (inverse-scaled).
     * Returns -1.0 when LibTorch is unavailable or the buffer is not full.
     */
    double runInference() const;

#ifdef ML_STRATEGY_ENABLED
    mutable torch::jit::script::Module model_;
    bool modelLoaded_ = false;
#endif
};
