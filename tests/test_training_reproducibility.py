"""
Tests for reproducible training (TD-07).

All tests stay in pure PyTorch — no data files required.
"""
import numpy as np
import pytest
import torch
from argparse import Namespace

# conftest adds research/transformer to sys.path
from Interface import set_seed, Model_Interface, build_args


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _small_args(**overrides):
    """Return a minimal args Namespace that exercises the model."""
    defaults = dict(
        target='close',
        auxilFeatures=['f1', 'f2'],
        checkpoints='/tmp/test_checkpoints/',
        seqLen=10,
        labelLen=4,
        predLen=2,
        encIn=3,   # 2 aux + 1 target
        decIn=3,
        cOut=1,
        dModel=16,
        nHeads=2,
        eLayers=1,
        dLayers=1,
        dFf=32,
        factor=1,
        dropout=0.0,   # zero dropout for determinism in eval checks
        numWorkers=0,
        trainEpochs=1,
        batchSize=4,
        patience=5,
        learningRate=0.001,
        seed=42,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def _random_batch(batch=2, seq_len=10, pred_len=2, label_len=4, n_feat=3):
    """Return (enc_x, enc_mark, dec_x, dec_mark) on CPU."""
    enc_x = torch.randn(batch, seq_len, n_feat)
    enc_mark = torch.randn(batch, seq_len, 3)
    dec_x = torch.randn(batch, label_len + pred_len, n_feat)
    dec_mark = torch.randn(batch, label_len + pred_len, 3)
    return enc_x, enc_mark, dec_x, dec_mark


# ---------------------------------------------------------------------------
# set_seed
# ---------------------------------------------------------------------------

class TestSetSeed:
    def test_same_seed_produces_same_random_tensor(self):
        set_seed(0)
        t1 = torch.randn(5)
        set_seed(0)
        t2 = torch.randn(5)
        assert torch.allclose(t1, t2)

    def test_different_seeds_produce_different_tensors(self):
        set_seed(1)
        t1 = torch.randn(10)
        set_seed(2)
        t2 = torch.randn(10)
        # With overwhelming probability these will differ
        assert not torch.allclose(t1, t2)

    def test_same_seed_numpy(self):
        set_seed(7)
        a1 = np.random.rand(5)
        set_seed(7)
        a2 = np.random.rand(5)
        np.testing.assert_array_equal(a1, a2)

    def test_same_seed_python_random(self):
        import random
        set_seed(99)
        v1 = [random.random() for _ in range(5)]
        set_seed(99)
        v2 = [random.random() for _ in range(5)]
        assert v1 == v2


# ---------------------------------------------------------------------------
# Deterministic forward passes
# ---------------------------------------------------------------------------

class TestDeterministicForward:
    def test_same_seed_same_weights(self):
        """Two models built with the same seed must have identical weights."""
        set_seed(42)
        args = _small_args()
        iface1 = Model_Interface(args)

        set_seed(42)
        iface2 = Model_Interface(args)

        for (n1, p1), (n2, p2) in zip(
            iface1.model.named_parameters(),
            iface2.model.named_parameters(),
        ):
            assert torch.allclose(p1, p2), f"Parameter {n1} differs"

    def test_eval_mode_is_deterministic(self):
        """Two identical forward passes in eval() must return identical output."""
        set_seed(42)
        args = _small_args()
        iface = Model_Interface(args)
        iface.model.eval()

        enc_x, enc_mark, dec_x, dec_mark = _random_batch()
        with torch.no_grad():
            out1 = iface.model(enc_x, enc_mark, dec_x, dec_mark)[0]
            out2 = iface.model(enc_x, enc_mark, dec_x, dec_mark)[0]

        assert torch.allclose(out1, out2), "eval() forward not deterministic"

    def test_different_seeds_produce_different_weights(self):
        """Two models built with different seeds should differ (high probability)."""
        set_seed(1)
        iface1 = Model_Interface(_small_args())
        set_seed(2)
        iface2 = Model_Interface(_small_args())

        # Compare first layer weights
        p1 = next(iface1.model.parameters())
        p2 = next(iface2.model.parameters())
        assert not torch.allclose(p1, p2)

    def test_same_seed_same_forward_output(self):
        """Same seed + same input → identical output, even across two fresh models."""
        enc_x, enc_mark, dec_x, dec_mark = _random_batch()

        set_seed(42)
        iface1 = Model_Interface(_small_args())
        iface1.model.eval()
        with torch.no_grad():
            out1 = iface1.model(enc_x, enc_mark, dec_x, dec_mark)[0]

        set_seed(42)
        iface2 = Model_Interface(_small_args())
        iface2.model.eval()
        with torch.no_grad():
            out2 = iface2.model(enc_x, enc_mark, dec_x, dec_mark)[0]

        assert torch.allclose(out1, out2, atol=1e-6)

    def test_train_mode_with_zero_dropout_is_deterministic(self):
        """With dropout=0 train mode should also be deterministic."""
        args = _small_args(dropout=0.0)
        set_seed(5)
        iface = Model_Interface(args)
        iface.model.train()

        enc_x, enc_mark, dec_x, dec_mark = _random_batch()
        with torch.no_grad():
            out1 = iface.model(enc_x, enc_mark, dec_x, dec_mark)[0]
            out2 = iface.model(enc_x, enc_mark, dec_x, dec_mark)[0]

        assert torch.allclose(out1, out2, atol=1e-6)


# ---------------------------------------------------------------------------
# build_args
# ---------------------------------------------------------------------------

class TestBuildArgs:
    def test_default_seed_is_42(self):
        args = build_args()
        assert args.seed == 42

    def test_override_is_applied(self):
        args = build_args({"seed": 123, "dModel": 64})
        assert args.seed == 123
        assert args.dModel == 64

    def test_num_workers_is_zero_by_default(self):
        """num_workers=0 is required for deterministic DataLoader behaviour."""
        args = build_args()
        assert args.numWorkers == 0
