"""DataManager 单日原始数据拉取重试的单元测试"""
import pandas as pd
import pytest

import data_manager
from data_manager import DataManager


@pytest.fixture
def dm(tmp_path):
    return DataManager(db_path=str(tmp_path / "test.db"), cache_dir=str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # 跳过重试退避和频率保护的实际等待
    monkeypatch.setattr(data_manager.time, 'sleep', lambda s: None)


def make_daily(date, n=10):
    return pd.DataFrame({
        'ts_code': [f'{i:06d}.SZ' for i in range(n)],
        'trade_date': date,
        'open': 10.0, 'high': 11.0, 'low': 9.5, 'close': 10.5,
        'pre_close': 10.0, 'change': 0.5, 'pct_chg': 5.0,
        'vol': 1000.0, 'amount': 10000.0,
    })


def make_adj(date, n=10):
    return pd.DataFrame({
        'ts_code': [f'{i:06d}.SZ' for i in range(n)],
        'adj_factor': 1.5,
    })


def make_basic(date, n=10):
    return pd.DataFrame({
        'ts_code': [f'{i:06d}.SZ' for i in range(n)],
        'trade_date': date,
        'pe': 20.0, 'total_mv': 1e9,
    })


class FlakyPro:
    """指定接口的前 fail_times 次调用返回空（mode='empty'）或抛异常（mode='raise'），之后正常"""

    def __init__(self, endpoint, fail_times=1, mode='empty'):
        self.endpoint = endpoint
        self.fail_times = fail_times
        self.mode = mode
        self.calls = {'daily': 0, 'adj_factor': 0, 'daily_basic': 0}

    def _should_fail(self, name):
        self.calls[name] += 1
        return name == self.endpoint and self.calls[name] <= self.fail_times

    def _fail(self, name):
        if self._should_fail(name):
            if self.mode == 'raise':
                raise RuntimeError('模拟接口超时')
            return None
        return True

    def daily(self, trade_date, fields):
        if not self._fail('daily'):
            return None
        return make_daily(trade_date)

    def adj_factor(self, trade_date, fields):
        if not self._fail('adj_factor'):
            return None
        return make_adj(trade_date)

    def daily_basic(self, trade_date, fields):
        if not self._fail('daily_basic'):
            return None
        return make_basic(trade_date)


@pytest.fixture
def patch_pro(monkeypatch):
    def _patch(pro_stub):
        monkeypatch.setattr(data_manager, 'pro', pro_stub)
        return pro_stub
    return _patch


def test_retry_recovers_from_transient_empty(dm, patch_pro):
    pro = patch_pro(FlakyPro(endpoint='daily', fail_times=1))
    frames = dm._fetch_raw_data_parallel(['20260527'])
    assert len(frames) == 1
    assert pro.calls['daily'] == 2
    assert pro.calls['adj_factor'] == 1
    assert pro.calls['daily_basic'] == 1


def test_retry_recovers_from_exception(dm, patch_pro):
    pro = patch_pro(FlakyPro(endpoint='daily_basic', fail_times=2, mode='raise'))
    frames = dm._fetch_raw_data_parallel(['20260527'])
    assert len(frames) == 1
    assert pro.calls['daily_basic'] == 3
    # 合并了 adj_factor 与 daily_basic 字段
    assert 'adj_factor' in frames[0].columns
    assert frames[0]['adj_factor'].notna().all()
    assert 'pe' in frames[0].columns


def test_retry_empty_adj_factor(dm, patch_pro):
    # adj_factor 缺失正是此前 J≈0 损坏数据的来源，必须触发重试而非带 NaN 通过
    pro = patch_pro(FlakyPro(endpoint='adj_factor', fail_times=1))
    frames = dm._fetch_raw_data_parallel(['20260527'])
    assert len(frames) == 1
    assert frames[0]['adj_factor'].notna().all()


def test_gives_up_after_max_retries(dm, patch_pro):
    pro = patch_pro(FlakyPro(endpoint='daily', fail_times=999))
    frames = dm._fetch_raw_data_parallel(['20260527'])
    assert frames == []
    assert pro.calls['daily'] == 1 + DataManager.FETCH_MAX_RETRIES
