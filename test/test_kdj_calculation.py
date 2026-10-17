"""KDJ 预热期 NaN 处理与一字板 RSV 前值填充的单元测试"""
import numpy as np
import pandas as pd
import pytest

from data_manager import DataManager


@pytest.fixture
def dm(tmp_path):
    return DataManager(db_path=str(tmp_path / "test.db"), cache_dir=str(tmp_path / "cache"))


def make_raw_df(n_days=17):
    """单只股票：前 9 日价格递增 1..9（RSV=100），之后横盘在 9。

    横盘 8 天后，最后一天的 9 日窗口全部为 9.0，RSV 分母为 0（一字板形态）。
    """
    prices = [float(i + 1) for i in range(9)] + [9.0] * (n_days - 9)
    return pd.DataFrame({
        'ts_code': '000001.SZ',
        'trade_date': [f'202401{i + 1:02d}' for i in range(n_days)],
        'open': prices, 'high': prices, 'low': prices, 'close': prices,
        'adj_factor': 1.0,
    })


def test_kdj_warmup_is_nan_not_zero(dm):
    df = dm._calculate_indicators(make_raw_df())
    j = df['kdj_qfq']
    # 前 8 根窗口不足 9，RSV 未定义 → J 保持 NaN，而不是被填成假 0
    assert j.iloc[:8].isna().all()
    # ewm 从首个有效 RSV 起步：递增行情 RSV=100，首日 K/D/J 即 100
    assert j.iloc[8] == pytest.approx(100.0)
    assert j.iloc[8:].notna().all()


def test_kdj_all_flat_window_carries_previous_rsv(dm):
    # 9 日窗口全部一字（0/0）时沿用前一日 RSV=100，J 不塌缩向 0
    df = dm._calculate_indicators(make_raw_df(17))
    assert df['kdj_qfq'].iloc[16] == pytest.approx(100.0)


def test_validator_passes_warmup_nan_kdj(dm):
    # 预热期 J=NaN 不应再触发"KDJ 整体塌缩"误判（NaN 比较结果为 False）
    n = 1000
    day = pd.DataFrame({
        'ts_code': [f'{i:06d}.SZ' for i in range(n)],
        'kdj_qfq': np.nan,
    })
    assert dm._validate_day_frame('20240527', day) is True
