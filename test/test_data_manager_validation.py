"""DataManager 单日数据损坏检测的单元测试"""
import numpy as np
import pandas as pd
import pytest

from data_manager import DataManager


@pytest.fixture
def dm(tmp_path):
    return DataManager(db_path=str(tmp_path / "test.db"), cache_dir=str(tmp_path / "cache"))


def make_day(n=1000, adj_nan=0.0, qfq_nan=0.0, j_fill=None):
    df = pd.DataFrame({
        'ts_code': [f'{i:06d}.SZ' for i in range(n)],
        'trade_date': '20260527',
        'adj_factor': np.where(np.arange(n) < n * adj_nan, np.nan, 1.5),
        'close_qfq': np.where(np.arange(n) < n * qfq_nan, np.nan, 10.0),
        'open_qfq': 10.0, 'high_qfq': 11.0, 'low_qfq': 9.5,
        'kdj_qfq': np.full(n, 30.0) if j_fill is None else j_fill,
    })
    return df


def test_good_day_passes(dm):
    assert dm._validate_day_frame('20260527', make_day()) is True


def test_empty_day_rejected(dm):
    assert dm._validate_day_frame('20260527', pd.DataFrame()) is False
    assert dm._validate_day_frame('20260527', None) is False


def test_adj_factor_nan_rejected(dm):
    assert dm._validate_day_frame('20260527', make_day(adj_nan=0.5)) is False


def test_adj_factor_slightly_nan_passes(dm):
    assert dm._validate_day_frame('20260527', make_day(adj_nan=0.01)) is True


def test_qfq_nan_rejected(dm):
    assert dm._validate_day_frame('20260527', make_day(qfq_nan=0.5)) is False


def test_kdj_flat_zero_rejected(dm):
    # RSV 被 fillna(0) 后的典型形态：全部股票 J≈0
    assert dm._validate_day_frame('20260527', make_day(j_fill=np.zeros(1000))) is False


def test_kdj_crash_but_not_flat_passes(dm):
    # 真实大跌：部分股票 J<13，但分布正常，不应误判
    rng = np.random.default_rng(42)
    j = np.concatenate([rng.uniform(-10, 12, 800), rng.uniform(20, 100, 200)])
    assert dm._validate_day_frame('20260527', make_day(j_fill=j)) is True


def test_kdj_check_skipped_for_small_sample(dm):
    # 样本不足时不做整体分布校验，避免小样本误伤
    assert dm._validate_day_frame('20260527', make_day(n=100, j_fill=np.zeros(100))) is True
