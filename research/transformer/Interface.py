"""
Interface.py — canonical training + inference entry point.

Training (CLI):
    python research/transformer/Interface.py --data-path all_stocks_processed.csv

Inference (import):
    from transformer.Interface import Model_Interface
    iface = Model_Interface(args)
    preds = iface.predict(seq_x, seq_x_mark, seq_y_mark, targetScaler=ts)
"""
import argparse
import logging
import os
import random
import time
import warnings

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch import optim
from torch.utils.data import DataLoader

import transformer.Model
from transformer.DataFrame import DataFrameDataset
from transformer.Tools import EarlyStopping, adjustLearningRate

warnings.filterwarnings('ignore')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("training.log"),
        logging.StreamHandler(),
    ],
)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def set_seed(seed: int) -> None:
    """Fix all random sources for reproducible training.

    Full GPU determinism also requires the environment variable:
        CUBLAS_WORKSPACE_CONFIG=:4096:8
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


# ---------------------------------------------------------------------------
# Model interface
# ---------------------------------------------------------------------------

class Model_Interface:
    def __init__(self, args):
        self.args = args
        self.device = torch.device('cpu')
        self.model = self._build_model().to(self.device)

    def _build_model(self):
        return transformer.Model.Model(self.args).float()

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def get_data(self, flag, data):
        feature_scaler = None
        target_scaler = None

        if flag != 'train' and hasattr(self.args, 'checkpoints'):
            try:
                feature_scaler = joblib.load(
                    os.path.join(self.args.checkpoints, 'featureScaler.pkl'))
                target_scaler = joblib.load(
                    os.path.join(self.args.checkpoints, 'targetScaler.pkl'))
            except FileNotFoundError:
                print("Warning: scaler files not found, fitting new scalers")

        dataset = DataFrameDataset(
            df=data,
            flag=flag,
            size=(self.args.seqLen, self.args.labelLen, self.args.predLen),
            target=self.args.target,
            auxilFeatures=self.args.auxilFeatures,
            featureScaler=feature_scaler,
            targetScaler=target_scaler,
            stockColumn='ticker',
        )

        print(f"{flag.upper()} dataset: {len(data)} rows → {len(dataset)} windows")

        loader = DataLoader(
            dataset,
            batch_size=self.args.batchSize,
            shuffle=(flag == 'train'),
            num_workers=0,  # deterministic; no inter-process randomness
            drop_last=(flag != 'pred'),
        )
        return dataset, loader

    def split_data(self, df):
        if not isinstance(df.index, pd.DatetimeIndex):
            df = df.set_index('date')
        n = len(df)
        train_end = int(n * 0.70)
        val_end = train_end + int(n * 0.15)
        return (
            df.iloc[:train_end].reset_index(),
            df.iloc[train_end:val_end].reset_index(),
            df.iloc[val_end:].reset_index(),
        )

    # ------------------------------------------------------------------
    # Validation loop
    # ------------------------------------------------------------------

    def vali(self, vali_loader):
        losses, rmses, mapes = [], [], []
        self.model.eval()
        with torch.no_grad():
            for batch_x, batch_y, batch_x_mark, batch_y_mark in vali_loader:
                batch_x = batch_x.float().to(self.device)
                batch_y = batch_y.float()
                batch_x_mark = batch_x_mark.float().to(self.device)
                batch_y_mark = batch_y_mark.float().to(self.device)

                dec_inp = torch.zeros_like(
                    batch_y[:, -self.args.predLen:, :]).float()
                dec_inp = torch.cat(
                    [batch_y[:, :self.args.labelLen, :], dec_inp],
                    dim=1).float().to(self.device)

                outputs = self.model(batch_x, batch_x_mark, dec_inp, batch_y_mark)[0]
                outputs = outputs[:, -self.args.predLen:, -1:]
                targets = batch_y[:, -self.args.predLen:, -1:].to(self.device)

                mse, rmse, mape = _compute_metrics(outputs, targets)
                losses.append(mse)
                rmses.append(rmse)
                mapes.append(mape)

        return float(np.mean(losses)), float(np.mean(rmses)), float(np.mean(mapes))

    # ------------------------------------------------------------------
    # Training loop
    # ------------------------------------------------------------------

    def train(self, data):
        train_df, val_df, test_df = self.split_data(data)

        print(
            f"\nData split: train={len(train_df)} "
            f"val={len(val_df)} test={len(test_df)} total={len(data)}"
        )

        path = self.args.checkpoints
        os.makedirs(path, exist_ok=True)

        train_data, train_loader = self.get_data('train', train_df)

        if hasattr(train_data, 'featureScaler'):
            joblib.dump(train_data.featureScaler,
                        os.path.join(path, 'featureScaler.pkl'))
        if hasattr(train_data, 'targetScaler'):
            joblib.dump(train_data.targetScaler,
                        os.path.join(path, 'targetScaler.pkl'))

        _, val_loader = self.get_data('val', val_df)
        _, test_loader = self.get_data('test', test_df)

        early_stopping = EarlyStopping(patience=self.args.patience)
        optimizer = optim.Adam(self.model.parameters(), lr=self.args.learningRate)
        criterion = nn.MSELoss()

        train_steps = len(train_loader)
        time_now = time.time()
        logging.info(f"Training: {self.args.trainEpochs} epochs, {train_steps} steps/epoch")

        for epoch in range(self.args.trainEpochs):
            iter_count = 0
            train_loss = []
            self.model.train()

            for i, (batch_x, batch_y, batch_x_mark, batch_y_mark) in enumerate(train_loader):
                iter_count += 1
                optimizer.zero_grad()

                batch_x = batch_x.float().to(self.device)
                batch_y = batch_y.float().to(self.device)
                batch_x_mark = batch_x_mark.float().to(self.device)
                batch_y_mark = batch_y_mark.float().to(self.device)

                dec_inp = torch.zeros_like(
                    batch_y[:, -self.args.predLen:, :]).float()
                dec_inp = torch.cat(
                    [batch_y[:, :self.args.labelLen, :], dec_inp],
                    dim=1).to(self.device)

                outputs = self.model(batch_x, batch_x_mark, dec_inp, batch_y_mark)[0]
                outputs = outputs[:, -self.args.predLen:, -1:]
                targets = batch_y[:, -self.args.predLen:, -1:]

                loss = criterion(outputs, targets)
                train_loss.append(loss.item())

                if (i + 1) % 100 == 0:
                    speed = (time.time() - time_now) / iter_count
                    eta = speed * ((self.args.trainEpochs - epoch) * train_steps - i)
                    logging.info(
                        f"Iter {i+1}/{train_steps} Epoch {epoch+1}/{self.args.trainEpochs} "
                        f"loss={loss.item():.6f} speed={speed:.3f}s/iter eta={eta/60:.1f}min"
                    )
                    iter_count = 0
                    time_now = time.time()

                loss.backward()
                optimizer.step()

            train_mse = float(np.mean(train_loss))
            val_mse, val_rmse, val_mape = self.vali(val_loader)
            test_mse, test_rmse, test_mape = self.vali(test_loader)

            logging.info(
                f"[Epoch {epoch+1:03d}] "
                f"train_mse={train_mse:.6f} "
                f"val_mse={val_mse:.6f} val_rmse={val_rmse:.6f} val_mape={val_mape:.3f}% "
                f"test_mse={test_mse:.6f}"
            )

            early_stopping(val_mse, self.model, path)
            if early_stopping.earlyStop:
                logging.info("Early stopping triggered")
                break

            adjustLearningRate(optimizer, epoch + 1, self.args)

        best_path = os.path.join(path, 'checkpoint.pth')
        self.model.load_state_dict(torch.load(best_path))
        logging.info(f"Best model loaded from {best_path}")
        return val_mse

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(self, seq_x, seq_x_mark, seq_y_mark, load=False,
                setting=None, targetScaler=None):
        self.model.eval()

        if load and setting:
            best = os.path.join(self.args.checkpoints, setting, 'checkpoint.pth')
            self.model.load_state_dict(torch.load(best))

        with torch.no_grad():
            seq_x = seq_x.float().to(self.device)
            seq_x_mark = seq_x_mark.float().to(self.device)
            seq_y_mark = seq_y_mark.float().to(self.device)

            dec_inp = torch.zeros(
                [seq_x.shape[0], self.args.predLen, seq_x.shape[2]]).float()
            dec_inp = torch.cat(
                [seq_x[:, -self.args.labelLen:, :], dec_inp],
                dim=1).float().to(self.device)

            outputs = self.model(seq_x, seq_x_mark, dec_inp, seq_y_mark)[0]
            preds = outputs.detach().cpu().numpy().squeeze(0)

            if targetScaler is not None:
                return targetScaler.inverse_transform(
                    preds.reshape(-1, 1)).flatten()
            return preds


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _compute_metrics(outputs, targets):
    mse = torch.mean((outputs - targets) ** 2).item()
    rmse = float(np.sqrt(mse))
    mape = torch.mean(
        torch.abs((targets - outputs) / (targets + 1e-8))).item() * 100
    return mse, rmse, mape


# ---------------------------------------------------------------------------
# Default feature list (34 features matching training pipeline)
# ---------------------------------------------------------------------------

DEFAULT_AUX_FEATURES = [
    'high', 'low', 'volume', 'adj close',
    'P', 'R1', 'R2', 'R3', 'S1', 'S2', 'S3',
    'obv', 'volume_zscore', 'rsi',
    'macd', 'macds', 'macdh',
    'sma', 'lma', 'sema', 'lema',
    'overnight_gap',
    'return_lag_1', 'return_lag_3', 'return_lag_5',
    'volatility',
    'SR_K', 'SR_D', 'SR_RSI_K', 'SR_RSI_D',
    'ATR', 'HL_PCT', 'PCT_CHG',
]


def build_args(overrides: dict | None = None):
    """Return a Namespace with all training defaults, optionally overridden."""
    from argparse import Namespace
    defaults = dict(
        target='close',
        auxilFeatures=DEFAULT_AUX_FEATURES,
        checkpoints='./checkpoints/',
        seqLen=30,
        labelLen=10,
        predLen=1,
        encIn=len(DEFAULT_AUX_FEATURES) + 1,
        decIn=len(DEFAULT_AUX_FEATURES) + 1,
        cOut=1,
        dModel=256,
        nHeads=8,
        eLayers=3,
        dLayers=2,
        dFf=512,
        factor=1,
        dropout=0.1,
        numWorkers=0,
        trainEpochs=100,
        batchSize=128,
        patience=10,
        learningRate=0.0005,
        seed=42,
    )
    if overrides:
        defaults.update(overrides)
    return Namespace(**defaults)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_cli():
    p = argparse.ArgumentParser(
        description="Train the TradingTransformer model",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('--data-path', required=True,
                   help='Path to the feature CSV (all tickers concatenated)')
    p.add_argument('--checkpoints', default='./checkpoints/')
    p.add_argument('--seq-len', type=int, default=30)
    p.add_argument('--label-len', type=int, default=10)
    p.add_argument('--pred-len', type=int, default=1)
    p.add_argument('--d-model', type=int, default=256)
    p.add_argument('--n-heads', type=int, default=8)
    p.add_argument('--e-layers', type=int, default=3)
    p.add_argument('--d-layers', type=int, default=2)
    p.add_argument('--d-ff', type=int, default=512)
    p.add_argument('--dropout', type=float, default=0.1)
    p.add_argument('--batch-size', type=int, default=128)
    p.add_argument('--epochs', type=int, default=100)
    p.add_argument('--patience', type=int, default=10)
    p.add_argument('--lr', type=float, default=0.0005)
    p.add_argument('--seed', type=int, default=42)
    return p.parse_args()


def main():
    cli = _parse_cli()
    set_seed(cli.seed)

    n_features = len(DEFAULT_AUX_FEATURES) + 1
    args = build_args(dict(
        checkpoints=cli.checkpoints,
        seqLen=cli.seq_len,
        labelLen=cli.label_len,
        predLen=cli.pred_len,
        encIn=n_features,
        decIn=n_features,
        dModel=cli.d_model,
        nHeads=cli.n_heads,
        eLayers=cli.e_layers,
        dLayers=cli.d_layers,
        dFf=cli.d_ff,
        dropout=cli.dropout,
        batchSize=cli.batch_size,
        trainEpochs=cli.epochs,
        patience=cli.patience,
        learningRate=cli.lr,
        seed=cli.seed,
    ))

    data = pd.read_csv(cli.data_path, parse_dates=['date'])
    iface = Model_Interface(args)
    val_mse = iface.train(data)
    logging.info(f"Training complete. Final val_mse={val_mse:.6f}")


if __name__ == '__main__':
    main()
