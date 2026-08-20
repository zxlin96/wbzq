#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多指标联合选股 + 超阈值行业提示 — 单元测试与集成测试

运行方式：
    pytest test/test_multi_indicator_pick.py -v
    python test/test_multi_indicator_pick.py
"""

import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

# 将项目根目录加入 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import STRATEGY_CONFIG as ST, StrategyThresholds
from multi_indicator_pick import (
    apply_multi_indicator_filter,
    compute_multi_indicator_funnel_stats,
    generate_industry_count_hints,
    save_multi_indicator_hints,
    save_multi_indicator_result,
    validate_multi_indicator_thresholds,
)
from generate_email_report import (
    load_multi_indicator_hints,
    generate_multi_indicator_hint_section,
)


# ============================================================================
# 辅助函数：构造 mock 数据
# ============================================================================

END_DATE = "20250620"


def make_stock(ts_code, name, industry, kdj_j=10.0, macd_dif=0.5, close=25.0,
               ma60=24.0, pct_chg=1.0, amount=1e8, total_mv=600000,
               high=25.5, low=24.5, pre_close=24.8):
    """构造单只股票的当日数据行（默认满足全部 9 项条件）。"""
    return {
        'ts_code': ts_code, 'name': name, 'industry_name': industry,
        'trade_date': END_DATE,
        'close_qfq': close, 'ma_qfq_60': ma60,
        'kdj_qfq': kdj_j, 'macd_dif_qfq': macd_dif,
        'pct_chg': pct_chg, 'amount': amount, 'total_mv': total_mv,
        'high_qfq': high, 'low_qfq': low, 'pre_close': pre_close,
    }


def make_df(stocks):
    """由股票列表构造 DataFrame。"""
    return pd.DataFrame(stocks)


def make_basic(stocks):
    """由股票列表构造基本信息 DataFrame。"""
    return pd.DataFrame([{
        'ts_code': s['ts_code'], 'name': s['name'],
        'industry_name': s['industry_name'], 'list_date': '20200101',
    } for s in stocks])


# ============================================================================
# T2.1 validate_multi_indicator_thresholds
# ============================================================================

def test_validate_default_ok():
    """默认 STRATEGY_CONFIG 应通过校验。"""
    validate_multi_indicator_thresholds(ST)


def test_validate_pct_chg_min_max():
    """PCT_CHG_MIN >= PCT_CHG_MAX 应抛 ValueError。"""
    t = StrategyThresholds()
    t.MULTI_PCT_CHG_MIN = 3.0
    t.MULTI_PCT_CHG_MAX = -3.0
    try:
        validate_multi_indicator_thresholds(t)
        assert False, "应抛 ValueError"
    except ValueError as e:
        assert "PCT_CHG" in str(e)


def test_validate_mv_min():
    """MV_MIN_BILLION <= 0 应抛 ValueError。"""
    t = StrategyThresholds()
    t.MULTI_MV_MIN_BILLION = -1
    try:
        validate_multi_indicator_thresholds(t)
        assert False, "应抛 ValueError"
    except ValueError as e:
        assert "MV_MIN" in str(e)


def test_validate_amount_top_percent():
    """AMOUNT_TOP_PERCENT 不在 (0,1] 应抛 ValueError。"""
    for bad_val in [0, 1.5, -0.1]:
        t = StrategyThresholds()
        t.MULTI_AMOUNT_TOP_PERCENT = bad_val
        try:
            validate_multi_indicator_thresholds(t)
            assert False, f"{bad_val} 应抛 ValueError"
        except ValueError as e:
            assert "AMOUNT_TOP_PERCENT" in str(e)


def test_validate_ma_period():
    """MA_PERIOD <= 0 应抛 ValueError。"""
    t = StrategyThresholds()
    t.MULTI_MA_PERIOD = 0
    try:
        validate_multi_indicator_thresholds(t)
        assert False, "应抛 ValueError"
    except ValueError as e:
        assert "MA_PERIOD" in str(e)


# ============================================================================
# T2.2 apply_multi_indicator_filter 边界值
# ============================================================================

def test_filter_kdj_boundary():
    """KDJ-J = 13.0 不入选，12.9 入选。"""
    stocks = [
        make_stock('A.SH', '股A', '通信', kdj_j=13.0),
        make_stock('B.SH', '股B', '通信', kdj_j=12.9),
    ]
    result, _ = apply_multi_indicator_filter(make_df(stocks), END_DATE, make_basic(stocks), ST)
    assert 'B.SH' in result['ts_code'].values
    assert 'A.SH' not in result['ts_code'].values


def test_filter_macd_boundary():
    """MACD-DIF = 0.0 不入选，0.01 入选。"""
    stocks = [
        make_stock('A.SH', '股A', '通信', macd_dif=0.0),
        make_stock('B.SH', '股B', '通信', macd_dif=0.01),
    ]
    result, _ = apply_multi_indicator_filter(make_df(stocks), END_DATE, make_basic(stocks), ST)
    assert 'B.SH' in result['ts_code'].values
    assert 'A.SH' not in result['ts_code'].values


def test_filter_st_excluded():
    """名称含 ST/*ST 不入选。"""
    stocks = [
        make_stock('A.SH', 'ST股A', '通信'),
        make_stock('B.SH', '*ST股B', '通信'),
        make_stock('C.SH', '正常股', '通信'),
    ]
    result, _ = apply_multi_indicator_filter(make_df(stocks), END_DATE, make_basic(stocks), ST)
    assert 'C.SH' in result['ts_code'].values
    assert 'A.SH' not in result['ts_code'].values
    assert 'B.SH' not in result['ts_code'].values


def test_filter_close_ma60_boundary():
    """收盘价 = MA60 不入选，close > MA60 入选。"""
    stocks = [
        make_stock('A.SH', '股A', '通信', close=24.0, ma60=24.0),
        make_stock('B.SH', '股B', '通信', close=24.01, ma60=24.0),
    ]
    result, _ = apply_multi_indicator_filter(make_df(stocks), END_DATE, make_basic(stocks), ST)
    assert 'B.SH' in result['ts_code'].values
    assert 'A.SH' not in result['ts_code'].values


def test_filter_pct_chg_boundary():
    """涨跌幅 = 3.0 不入选，= -3.0 不入选，2.99/-2.99 入选。"""
    stocks = [
        make_stock('A.SH', '股A', '通信', pct_chg=3.0),
        make_stock('B.SH', '股B', '通信', pct_chg=-3.0),
        make_stock('C.SH', '股C', '通信', pct_chg=2.99),
        make_stock('D.SH', '股D', '通信', pct_chg=-2.99),
    ]
    result, _ = apply_multi_indicator_filter(make_df(stocks), END_DATE, make_basic(stocks), ST)
    codes = set(result['ts_code'].values)
    assert 'C.SH' in codes
    assert 'D.SH' in codes
    assert 'A.SH' not in codes
    assert 'B.SH' not in codes


def test_filter_mv_boundary():
    """总市值 = 500000 万元（50 亿）不入选，500001 入选。"""
    stocks = [
        make_stock('A.SH', '股A', '通信', total_mv=500000),
        make_stock('B.SH', '股B', '通信', total_mv=500001),
    ]
    result, _ = apply_multi_indicator_filter(make_df(stocks), END_DATE, make_basic(stocks), ST)
    assert 'B.SH' in result['ts_code'].values
    assert 'A.SH' not in result['ts_code'].values


def test_filter_amplitude_boundary():
    """振幅 = 7.0 不入选，6.9 入选。"""
    # 振幅 = (high - low) / pre_close * 100
    # pre_close=100, high=103.5, low=96.5 -> 振幅 = 7.0
    # pre_close=100, high=103.45, low=96.55 -> 振幅 = 6.9
    stocks = [
        make_stock('A.SH', '股A', '通信', high=103.5, low=96.5, pre_close=100.0),
        make_stock('B.SH', '股B', '通信', high=103.45, low=96.55, pre_close=100.0),
    ]
    result, _ = apply_multi_indicator_filter(make_df(stocks), END_DATE, make_basic(stocks), ST)
    assert 'B.SH' in result['ts_code'].values
    assert 'A.SH' not in result['ts_code'].values


def test_filter_sort_by_kdj():
    """结果应按 KDJ-J 升序排列。"""
    stocks = [
        make_stock('A.SH', '股A', '通信', kdj_j=10.0),
        make_stock('B.SH', '股B', '通信', kdj_j=5.0),
        make_stock('C.SH', '股C', '通信', kdj_j=8.0),
    ]
    result, _ = apply_multi_indicator_filter(make_df(stocks), END_DATE, make_basic(stocks), ST)
    kdj_values = result['kdj_qfq'].tolist()
    assert kdj_values == sorted(kdj_values)


def test_filter_nan_field_excluded():
    """字段为 NaN 的股票应被剔除而不抛异常。"""
    stocks = [
        make_stock('A.SH', '股A', '通信', kdj_j=np.nan),
        make_stock('B.SH', '股B', '通信', kdj_j=10.0),
    ]
    result, _ = apply_multi_indicator_filter(make_df(stocks), END_DATE, make_basic(stocks), ST)
    assert 'B.SH' in result['ts_code'].values
    assert 'A.SH' not in result['ts_code'].values


def test_filter_no_data():
    """目标日期无数据应返回空结果。"""
    stocks = [make_stock('A.SH', '股A', '通信')]
    df = make_df(stocks)
    result, funnel = apply_multi_indicator_filter(df, "20251231", make_basic(stocks), ST)
    assert result.empty
    assert all(v == 0 for v in funnel.values())


def test_filter_input_not_modified():
    """入参 df 不应被修改。"""
    stocks = [make_stock('A.SH', '股A', '通信')]
    df = make_df(stocks)
    cols_before = list(df.columns)
    shape_before = df.shape
    apply_multi_indicator_filter(df, END_DATE, make_basic(stocks), ST)
    assert list(df.columns) == cols_before
    assert df.shape == shape_before


# ============================================================================
# T2.3 compute_multi_indicator_funnel_stats
# ============================================================================

def test_funnel_monotonic():
    """漏斗各阶段值应单调不增。"""
    stocks = [
        make_stock('A.SH', '股A', '通信', kdj_j=10.0),
        make_stock('B.SH', '股B', '通信', kdj_j=12.0, macd_dif=-0.1),
        make_stock('C.SH', '股C', '通信', kdj_j=11.0, pct_chg=5.0),
    ]
    funnel = compute_multi_indicator_funnel_stats(make_df(stocks), END_DATE, ST)
    values = list(funnel.values())
    for i in range(len(values) - 1):
        assert values[i] >= values[i + 1], f"阶段 {i} 值 {values[i]} < 阶段 {i+1} 值 {values[i+1]}"


def test_funnel_final_matches_filter():
    """漏斗最终值应等于筛选结果行数。"""
    stocks = [
        make_stock('A.SH', '股A', '通信', kdj_j=10.0),
        make_stock('B.SH', '股B', '通信', kdj_j=12.0, macd_dif=-0.1),
    ]
    df = make_df(stocks)
    result, funnel = apply_multi_indicator_filter(df, END_DATE, make_basic(stocks), ST)
    assert funnel['最终'] == len(result)


# ============================================================================
# T3.1 generate_industry_count_hints
# ============================================================================

def test_count_hints_empty_result():
    """空结果应返回 []。"""
    result = pd.DataFrame(columns=['ts_code', 'industry_name'])
    hints = generate_industry_count_hints(result, 10)
    assert hints == []


def test_count_hints_threshold_boundary():
    """count == threshold 不触发，count == threshold + 1 触发。"""
    result_11 = pd.DataFrame([{'ts_code': f'{i}.SH', 'industry_name': '通信'} for i in range(11)])
    hints_11 = generate_industry_count_hints(result_11, 10)
    assert len(hints_11) == 1
    assert hints_11[0]['count'] == 11
    result_10 = pd.DataFrame([{'ts_code': f'{i}.SH', 'industry_name': '通信'} for i in range(10)])
    hints_10 = generate_industry_count_hints(result_10, 10)
    assert len(hints_10) == 0


def test_count_hints_keys_only_industry_count():
    """提示条目键集合应严格等于 {industry, count}，无 ETF 字段。"""
    result = pd.DataFrame([{'ts_code': f'{i}.SH', 'industry_name': '通信'} for i in range(11)])
    hints = generate_industry_count_hints(result, 10)
    assert len(hints) == 1
    assert set(hints[0].keys()) == {'industry', 'count'}
    assert 'etf_code' not in hints[0]
    assert 'etf_name' not in hints[0]


def test_count_hints_type_str_int():
    """industry 为 str，count 为 int。"""
    result = pd.DataFrame([{'ts_code': f'{i}.SH', 'industry_name': '通信'} for i in range(11)])
    hints = generate_industry_count_hints(result, 10)
    assert isinstance(hints[0]['industry'], str)
    assert isinstance(hints[0]['count'], int)


def test_count_hints_sort_tiebreak():
    """count 降序，count 相同时按 industry 字典序升序。"""
    result = pd.DataFrame(
        [{'ts_code': f'a{i}.SH', 'industry_name': 'A行业'} for i in range(15)] +
        [{'ts_code': f'b{i}.SH', 'industry_name': 'B行业'} for i in range(20)] +
        [{'ts_code': f'c{i}.SH', 'industry_name': 'C行业'} for i in range(15)]
    )
    hints = generate_industry_count_hints(result, 10)
    assert len(hints) == 3
    assert hints[0]['industry'] == 'B行业'
    assert hints[0]['count'] == 20
    assert hints[1]['industry'] == 'A行业'
    assert hints[2]['industry'] == 'C行业'


def test_count_hints_industry_col_missing():
    """industry_name 列缺失应归'未知行业'，无 KeyError。"""
    result = pd.DataFrame([{'ts_code': f'{i}.SH'} for i in range(11)])
    hints = generate_industry_count_hints(result, 10)
    assert len(hints) == 1
    assert hints[0]['industry'] == '未知行业'


def test_count_hints_nan_industry():
    """industry_name 含 NaN 应归'未知行业'。"""
    result = pd.DataFrame([{'ts_code': f'{i}.SH', 'industry_name': np.nan} for i in range(12)])
    hints = generate_industry_count_hints(result, 10)
    assert len(hints) == 1
    assert hints[0]['industry'] == '未知行业'


def test_count_hints_no_etf_config_dependency():
    """提示生成不依赖 etf_config.json（无映射行业也提示）。"""
    result = pd.DataFrame(
        [{'ts_code': f'c{i}.SH', 'industry_name': '通信'} for i in range(12)] +
        [{'ts_code': f'x{i}.SH', 'industry_name': '未知行业'} for i in range(15)]
    )
    hints = generate_industry_count_hints(result, 10)
    assert len(hints) == 2
    industries = {h['industry'] for h in hints}
    assert '通信' in industries
    assert '未知行业' in industries


# ============================================================================
# T4.2 save_multi_indicator_hints
# ============================================================================

def test_save_hints_normal():
    """正常落盘应生成 JSON 文件，UTF-8 编码，中文可读。"""
    hints = [{'industry': '银行', 'count': 15}, {'industry': '通信', 'count': 12}]
    json_path = save_multi_indicator_hints(hints, END_DATE)
    try:
        assert json_path == f"multi_indicator_hints_{END_DATE}.json"
        assert os.path.exists(json_path)
        with open(json_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert '银行' in content
        assert '\\u' not in content
        data = json.loads(content)
        assert len(data) == 2
    finally:
        if os.path.exists(json_path):
            os.unlink(json_path)


def test_save_hints_empty():
    """空清单仍落盘 []。"""
    json_path = save_multi_indicator_hints([], END_DATE)
    try:
        assert os.path.exists(json_path)
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data == []
    finally:
        if os.path.exists(json_path):
            os.unlink(json_path)


def test_save_hints_returns_path():
    """返回值应为文件路径字符串。"""
    hints = [{'industry': '银行', 'count': 15}]
    json_path = save_multi_indicator_hints(hints, END_DATE)
    try:
        assert isinstance(json_path, str)
        assert json_path.endswith('.json')
    finally:
        if os.path.exists(json_path):
            os.unlink(json_path)


# ============================================================================
# T7 load_multi_indicator_hints
# ============================================================================

def test_load_hints_no_file():
    """无匹配文件应返回 []。"""
    hints = load_multi_indicator_hints()
    assert isinstance(hints, list)


def test_load_hints_corrupted():
    """JSON 损坏应返回 []。"""
    json_path = f"multi_indicator_hints_{END_DATE}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        f.write('{invalid json')
    try:
        hints = load_multi_indicator_hints()
        assert hints == []
    finally:
        if os.path.exists(json_path):
            os.unlink(json_path)


def test_load_hints_non_list():
    """内容非 list 应返回 []。"""
    json_path = f"multi_indicator_hints_{END_DATE}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({"industry": "银行"}, f)
    try:
        hints = load_multi_indicator_hints()
        assert hints == []
    finally:
        if os.path.exists(json_path):
            os.unlink(json_path)


def test_load_hints_latest():
    """多个文件应取最新。"""
    path_old = "multi_indicator_hints_20250619.json"
    path_new = "multi_indicator_hints_20250620.json"
    with open(path_old, 'w', encoding='utf-8') as f:
        json.dump([{'industry': '旧', 'count': 11}], f)
    with open(path_new, 'w', encoding='utf-8') as f:
        json.dump([{'industry': '新', 'count': 12}], f)
    try:
        hints = load_multi_indicator_hints()
        assert len(hints) == 1
        assert hints[0]['industry'] == '新'
    finally:
        for p in [path_old, path_new]:
            if os.path.exists(p):
                os.unlink(p)


# ============================================================================
# T8 generate_multi_indicator_hint_section
# ============================================================================

def test_hint_section_empty():
    """空清单应包含'暂无超阈值行业'。"""
    html = generate_multi_indicator_hint_section([])
    assert '暂无超阈值行业' in html


def test_hint_section_non_empty():
    """非空清单应含 2 列表格，行数正确。"""
    hints = [{'industry': '银行', 'count': 15}, {'industry': '通信', 'count': 12}]
    html = generate_multi_indicator_hint_section(hints)
    assert html.count('<tr') == 3
    assert '行业' in html
    assert '入选数量' in html


def test_hint_section_escape_html():
    """行业名含特殊字符应转义。"""
    hints = [{'industry': 'A&B<C>', 'count': 15}]
    html = generate_multi_indicator_hint_section(hints)
    assert '&amp;' in html
    assert '&lt;' in html
    assert '&gt;' in html


def test_hint_section_no_etf_text():
    """HTML 不应包含 ETF 字样。"""
    hints = [{'industry': '银行', 'count': 15}]
    html = generate_multi_indicator_hint_section(hints)
    assert 'ETF' not in html


# ============================================================================
# T4.1 save_multi_indicator_result
# ============================================================================

def test_save_csv_non_empty():
    """非空结果应生成 CSV 文件。"""
    result = pd.DataFrame([make_stock('A.SH', '股A', '通信')])
    csv_path = save_multi_indicator_result(result, END_DATE)
    try:
        assert csv_path == f"multi_indicator_result_{END_DATE}.csv"
        assert os.path.exists(csv_path)
        # 验证 utf-8-sig 编码可正常读取
        df_read = pd.read_csv(csv_path, encoding='utf-8-sig')
        assert len(df_read) == 1
    finally:
        if os.path.exists(csv_path):
            os.unlink(csv_path)


def test_save_csv_empty():
    """空结果应跳过写文件并返回空串。"""
    result = pd.DataFrame(columns=['ts_code'])
    csv_path = save_multi_indicator_result(result, END_DATE)
    assert csv_path == ""


# ============================================================================
# 集成测试：run_multi_indicator_strategy
# ============================================================================

def test_run_strategy_integration():
    """run_multi_indicator_strategy 应返回三元组 (DataFrame, dict, list)。"""
    from multi_indicator_pick import run_multi_indicator_strategy
    stocks = [
        make_stock('A.SH', '股A', '通信', kdj_j=10.0),
        make_stock('B.SH', '股B', '通信', kdj_j=12.0),
    ]
    df = make_df(stocks)
    basic = make_basic(stocks)
    result, funnel, hints = run_multi_indicator_strategy(df, END_DATE, basic, ST)
    assert isinstance(result, pd.DataFrame)
    assert isinstance(funnel, dict)
    assert isinstance(hints, list)
    # 清理产物
    csv_path = f"multi_indicator_result_{END_DATE}.csv"
    if os.path.exists(csv_path):
        os.unlink(csv_path)


# ============================================================================
# 独立运行入口
# ============================================================================

def _run_all_tests():
    """收集并运行所有 test_ 开头的函数。"""
    test_funcs = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith('test_') and callable(obj)
    ]
    passed, failed = 0, 0
    for name, func in test_funcs:
        try:
            func()
            print(f"  [PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1
    print(f"\n共 {len(test_funcs)} 个用例: {passed} 通过, {failed} 失败")
    return failed == 0


if __name__ == "__main__":
    ok = _run_all_tests()
    sys.exit(0 if ok else 1)