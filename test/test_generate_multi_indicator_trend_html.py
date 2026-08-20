#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_multi_indicator_trend_html.py 核心函数单元测试 (T18)

运行方式：
    pytest test/test_generate_multi_indicator_trend_html.py -v
    python test/test_generate_multi_indicator_trend_html.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from generate_multi_indicator_trend_html import (
    scan_hints_files,
    parse_hints_file,
    build_trend_matrix,
    compute_trend_metrics,
    build_echarts_options,
    render_trend_html,
)


# ============================================================================
# scan_hints_files
# ============================================================================

def test_scan_valid_filenames():
    """合法文件名被识别并按日期升序排序。"""
    with tempfile.TemporaryDirectory() as tmp:
        for d in ["20260819", "20260818", "20260820"]:
            Path(tmp, f"multi_indicator_hints_{d}.json").write_text("[]", encoding="utf-8")
        result = scan_hints_files(tmp)
        dates = [r[0] for r in result]
        assert dates == ["20260818", "20260819", "20260820"]


def test_scan_invalid_filenames_skipped():
    """非 8 位数字日期的文件名被跳过。"""
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "multi_indicator_hints_2026081.json").write_text("[]", encoding="utf-8")
        Path(tmp, "multi_indicator_hints_abc.json").write_text("[]", encoding="utf-8")
        Path(tmp, "multi_indicator_hints_20260819_extra.json").write_text("[]", encoding="utf-8")
        Path(tmp, "multi_indicator_hints_20260819.json").write_text("[]", encoding="utf-8")
        result = scan_hints_files(tmp)
        assert len(result) == 1
        assert result[0][0] == "20260819"


def test_scan_empty_dir():
    """空目录返回空列表。"""
    with tempfile.TemporaryDirectory() as tmp:
        assert scan_hints_files(tmp) == []


# ============================================================================
# parse_hints_file
# ============================================================================

def test_parse_valid():
    """合法 JSON 列表正常解析。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp, "h.json")
        p.write_text(json.dumps([{"industry": "电子", "count": 5}, {"industry": "医药", "count": 3}]), encoding="utf-8")
        result = parse_hints_file(p)
        assert result == [{"industry": "电子", "count": 5}, {"industry": "医药", "count": 3}]


def test_parse_same_industry_aggregation():
    """同 industry 聚合求和。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp, "h.json")
        p.write_text(json.dumps([{"industry": "电子", "count": 5}, {"industry": "电子", "count": 3}]), encoding="utf-8")
        result = parse_hints_file(p)
        assert result == [{"industry": "电子", "count": 8}]


def test_parse_industry_abnormal():
    """industry 非 str 或空串 → 归为"未知行业"。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp, "h.json")
        p.write_text(json.dumps([{"industry": 123, "count": 2}, {"industry": "", "count": 1}]), encoding="utf-8")
        result = parse_hints_file(p)
        assert result == [{"industry": "未知行业", "count": 3}]


def test_parse_count_abnormal():
    """count 非 int 或负数 → 归为 0。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp, "h.json")
        p.write_text(json.dumps([{"industry": "电子", "count": "abc"}, {"industry": "医药", "count": -5}]), encoding="utf-8")
        result = parse_hints_file(p)
        result_sorted = sorted(result, key=lambda x: x["industry"])
        assert result_sorted == [{"industry": "医药", "count": 0}, {"industry": "电子", "count": 0}]


def test_parse_corrupted_json():
    """损坏 JSON 返回 None。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp, "h.json")
        p.write_text("{broken json!!!", encoding="utf-8")
        assert parse_hints_file(p) is None


# ============================================================================
# build_trend_matrix
# ============================================================================

def test_matrix_normal_fill():
    """正常填充矩阵。"""
    records = [
        ("20260818", [{"industry": "电子", "count": 5}]),
        ("20260819", [{"industry": "电子", "count": 3}, {"industry": "医药", "count": 2}]),
    ]
    m = build_trend_matrix(records)
    assert m["dates"] == ["20260818", "20260819"]
    assert m["matrix"]["电子"]["20260818"] == 5
    assert m["matrix"]["电子"]["20260819"] == 3
    assert m["matrix"]["医药"]["20260819"] == 2
    assert m["daily_totals"] == [5, 5]
    assert m["global_max"] == 5


def test_matrix_absent_fill_zero():
    """缺席日期补 0。"""
    records = [
        ("20260818", [{"industry": "电子", "count": 5}]),
        ("20260819", [{"industry": "医药", "count": 2}]),
    ]
    m = build_trend_matrix(records)
    assert m["matrix"]["电子"].get("20260819", 0) == 0
    assert m["matrix"]["医药"].get("20260818", 0) == 0
    assert m["daily_totals"] == [5, 2]


def test_matrix_empty_input():
    """空输入返回空结构。"""
    m = build_trend_matrix([])
    assert m["dates"] == []
    assert m["industries"] == []
    assert m["daily_totals"] == []
    assert m["global_max"] == 0


# ============================================================================
# compute_trend_metrics
# ============================================================================

def test_metrics_consecutive_days():
    """持续天数计算正确。"""
    records = [
        ("20260817", [{"industry": "电子", "count": 5}]),
        ("20260818", [{"industry": "电子", "count": 3}]),
        ("20260819", [{"industry": "电子", "count": 4}]),
    ]
    m = build_trend_matrix(records)
    metrics = compute_trend_metrics(m)
    consec = {c["industry"]: c for c in metrics["consecutive"]}
    assert consec["电子"]["consecutive_days"] == 3
    assert consec["电子"]["total_days"] == 3


def test_metrics_new_enter_exit():
    """新进入/退出行业识别正确。"""
    records = [
        ("20260818", [{"industry": "电子", "count": 5}, {"industry": "医药", "count": 2}]),
        ("20260819", [{"industry": "电子", "count": 3}, {"industry": "银行", "count": 4}]),
    ]
    m = build_trend_matrix(records)
    metrics = compute_trend_metrics(m)
    assert metrics["new_enter"] == ["银行"]
    assert metrics["new_exit"] == ["医药"]


def test_metrics_single_day():
    """单日数据：无新进入/退出（需 ≥2 日才计算）。"""
    records = [("20260819", [{"industry": "电子", "count": 5}])]
    m = build_trend_matrix(records)
    metrics = compute_trend_metrics(m)
    assert metrics["new_enter"] == []
    assert metrics["new_exit"] == []
    assert metrics["summary"]["latest_industry_count"] == 1


def test_metrics_empty_data():
    """空数据：summary 全 0。"""
    m = build_trend_matrix([])
    metrics = compute_trend_metrics(m)
    assert metrics["summary"]["total_dates"] == 0
    assert metrics["summary"]["total_industries"] == 0
    assert metrics["summary"]["latest_industry_count"] == 0
    assert metrics["summary"]["max_consecutive_days"] == 0


# ============================================================================
# build_echarts_options
# ============================================================================

def test_echarts_line_option():
    """折线图 option 结构校验。"""
    records = [("20260819", [{"industry": "电子", "count": 5}])]
    m = build_trend_matrix(records)
    metrics = compute_trend_metrics(m)
    opts = build_echarts_options(m, metrics)
    assert opts["line"]["series"][0]["type"] == "line"
    assert opts["line"]["series"][0]["data"] == [5]
    assert opts["line"]["xAxis"]["data"] == ["2026-08-19"]


def test_echarts_bar_option():
    """堆叠柱状图 option 结构校验。"""
    records = [("20260819", [{"industry": "电子", "count": 5}, {"industry": "医药", "count": 3}])]
    m = build_trend_matrix(records)
    metrics = compute_trend_metrics(m)
    opts = build_echarts_options(m, metrics)
    assert len(opts["bar"]["series"]) == 2
    assert all(s["stack"] == "total" for s in opts["bar"]["series"])
    assert "电子" in opts["bar"]["legend"]["data"]


def test_echarts_heatmap_option():
    """热力图 option 结构校验。"""
    records = [("20260819", [{"industry": "电子", "count": 5}])]
    m = build_trend_matrix(records)
    metrics = compute_trend_metrics(m)
    opts = build_echarts_options(m, metrics)
    assert opts["heatmap"]["series"][0]["type"] == "heatmap"
    assert len(opts["heatmap"]["series"][0]["data"]) == 1
    assert opts["heatmap"]["series"][0]["data"][0] == [0, 0, 5]


def test_echarts_empty_data():
    """空数据：series 为空但结构完整。"""
    m = build_trend_matrix([])
    metrics = compute_trend_metrics(m)
    opts = build_echarts_options(m, metrics)
    assert opts["line"]["series"][0]["data"] == []
    assert opts["bar"]["series"] == []
    assert opts["heatmap"]["series"][0]["data"] == []


# ============================================================================
# render_trend_html
# ============================================================================

def test_render_contains_key_sections():
    """HTML 含关键区块。"""
    records = [("20260819", [{"industry": "电子", "count": 5}])]
    m = build_trend_matrix(records)
    metrics = compute_trend_metrics(m)
    opts = build_echarts_options(m, metrics)
    html = render_trend_html(m, metrics, opts)
    assert "多指标选股趋势汇总" in html
    assert "chartLine" in html
    assert "chartBar" in html
    assert "chartHeatmap" in html
    assert "持续超阈值行业" in html
    assert "电子" in html


def test_render_special_char_escape():
    """特殊字符在 HTML body 表格中被转义。"""
    records = [("20260819", [{"industry": "<电子&半导体>", "count": 5}])]
    m = build_trend_matrix(records)
    metrics = compute_trend_metrics(m)
    opts = build_echarts_options(m, metrics)
    html = render_trend_html(m, metrics, opts)
    assert "&lt;电子&amp;半导体&gt;" in html


def test_render_empty_data_prompt():
    """空数据时 HTML 含"暂无超阈值行业数据"文案。"""
    m = build_trend_matrix([])
    metrics = compute_trend_metrics(m)
    opts = build_echarts_options(m, metrics)
    html = render_trend_html(m, metrics, opts)
    assert "暂无超阈值行业数据" in html


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))