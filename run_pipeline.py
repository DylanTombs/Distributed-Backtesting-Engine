#!/usr/bin/env python3
"""
run_pipeline.py — ML pipeline orchestrator.

Executes three stages in order:
  1. Feature engineering  (pipeline.py)    raw OHLCV  →  feature CSVs
  2. Model training       (Train.py)       feature CSVs  →  Model3.pth
  3. Model export         (exportModel.py) Model3.pth →  transformer.pt + scalers

Each stage is a subprocess so failures are caught immediately and the
exit code propagates to the calling shell / container orchestrator.

Usage:
  python run_pipeline.py
  python run_pipeline.py --skip-train          # re-export an existing checkpoint
  python run_pipeline.py --data-dir /data      # override default paths
"""

import argparse
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Configuration schema — validated before any subprocess is spawned
# ---------------------------------------------------------------------------

class PipelineConfig(BaseModel):
    """All pipeline settings, validated at startup."""

    # Paths
    data_dir: str = "data"
    feature_dir: str = "features"
    model_dir: str = "models"
    skip_train: bool = False
    no_tearsheet: bool = False  # skip HTML tearsheet generation

    # Sequence window
    seq_len: int = Field(default=30, gt=0, description="Encoder input length")
    label_len: int = Field(default=10, gt=0, description="Decoder overlap length")
    pred_len: int = Field(default=5, gt=0, description="Forecast horizon")

    # Transformer architecture
    d_model: int = Field(default=256, gt=0)
    n_heads: int = Field(default=8, gt=0)
    e_layers: int = Field(default=3, gt=0)
    d_layers: int = Field(default=2, gt=0)
    d_ff: int = Field(default=512, gt=0)
    dropout: float = Field(default=0.1, ge=0.0, le=1.0)

    # Training
    batch_size: int = Field(default=128, gt=0)
    train_epochs: int = Field(default=100, gt=0)
    learning_rate: float = Field(default=0.0005, gt=0.0)

    @field_validator("n_heads")
    @classmethod
    def heads_must_divide_d_model(cls, v: int, info) -> int:
        d_model = (info.data or {}).get("d_model", 256)
        if d_model % v != 0:
            raise ValueError(
                f"n_heads={v} must evenly divide d_model={d_model}"
            )
        return v

    @model_validator(mode="after")
    def data_dir_must_exist(self) -> "PipelineConfig":
        if not Path(self.data_dir).exists():
            raise ValueError(
                f"data_dir '{self.data_dir}' does not exist — "
                "create the directory or pass --data-dir <path>"
            )
        return self


def run(cmd: list, *, cwd: str | None = None) -> None:
    """Run a command, stream output, and exit on failure."""
    print(f"\n{'='*60}")
    print(f">>> {' '.join(str(c) for c in cmd)}")
    print("=" * 60, flush=True)

    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        print(f"\n[ERROR] Command failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TradingTransformer ML pipeline orchestrator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing raw OHLCV CSVs",
    )
    parser.add_argument(
        "--feature-dir",
        default="features",
        help="Directory to write enriched feature CSVs",
    )
    parser.add_argument(
        "--model-dir",
        default="models",
        help="Directory to write exported model artefacts",
    )
    parser.add_argument(
        "--skip-train",
        action="store_true",
        help="Skip training and use the existing checkpoint in research/models/",
    )
    parser.add_argument(
        "--no-tearsheet",
        action="store_true",
        help="Skip HTML tearsheet generation (useful in CI/headless environments)",
    )
    args = parser.parse_args()

    # Validate all settings eagerly — fail fast before spawning any subprocess.
    try:
        cfg = PipelineConfig(
            data_dir=args.data_dir,
            feature_dir=args.feature_dir,
            model_dir=args.model_dir,
            skip_train=args.skip_train,
            no_tearsheet=args.no_tearsheet,
        )
    except Exception as exc:
        print(f"[PipelineConfig] Invalid configuration: {exc}", file=sys.stderr)
        sys.exit(1)

    Path(cfg.feature_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.model_dir).mkdir(parents=True, exist_ok=True)

    python = sys.executable

    # ------------------------------------------------------------------
    # Stage 1 — Feature engineering
    # ------------------------------------------------------------------
    print("\n[Stage 1/3] Feature engineering")
    run([
        python,
        "research/features/pipeline.py",
        cfg.data_dir,
        "-o", cfg.feature_dir,
    ])

    # ------------------------------------------------------------------
    # Stage 2 — Training (skippable when re-exporting an existing model)
    # ------------------------------------------------------------------
    if cfg.skip_train:
        print("\n[Stage 2/3] Training skipped (--skip-train)")
    else:
        print("\n[Stage 2/3] Model training")
        run([python, "research/training/Train.py"])

    # ------------------------------------------------------------------
    # Stage 3 — Export to LibTorch + scaler CSVs
    # ------------------------------------------------------------------
    print("\n[Stage 3/3] Model export")
    run([python, "research/exportModel.py"])

    # exportModel.py writes to ./models/ (relative to CWD).
    # If --model-dir differs, copy the artefacts there.
    default_model_dir = Path("models")
    target_model_dir = Path(cfg.model_dir)
    if target_model_dir.resolve() != default_model_dir.resolve():
        import shutil
        for artefact in ["transformer.pt", "feature_scaler.csv", "target_scaler.csv"]:
            src = default_model_dir / artefact
            if src.exists():
                shutil.copy2(src, target_model_dir / artefact)
                print(f"Copied {src} → {target_model_dir / artefact}")

    print(f"\n[Pipeline complete] Artefacts written to {cfg.model_dir}/")
    print("  transformer.pt")
    print("  feature_scaler.csv")
    print("  target_scaler.csv")

    # ------------------------------------------------------------------
    # Stage 4 — HTML tearsheet (optional; skipped with --no-tearsheet)
    # Reads from output/ if backtest CSVs already exist there.
    # ------------------------------------------------------------------
    if not cfg.no_tearsheet:
        output_dir = "output"
        equity_csv  = Path(output_dir) / "ml_equity.csv"
        trades_csv  = Path(output_dir) / "ml_trades.csv"
        metrics_csv = Path(output_dir) / "ml_metrics.csv"

        if equity_csv.exists() and trades_csv.exists() and metrics_csv.exists():
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            tearsheet_path = Path(output_dir) / f"tearsheet_{ts}.html"
            print("\n[Stage 4/4] Generating HTML tearsheet")
            run([
                python,
                "research/analysis/tearsheet.py",
                "--equity",  str(equity_csv),
                "--trades",  str(trades_csv),
                "--metrics", str(metrics_csv),
                "--config",  "backtest_config.yaml",
                "--output",  str(tearsheet_path),
            ])
        else:
            print("\n[Stage 4/4] Tearsheet skipped — run ./ml_backtest first to "
                  "produce output/ml_equity.csv, ml_trades.csv, ml_metrics.csv")


if __name__ == "__main__":
    main()
