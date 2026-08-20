#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_reports_json.py multiIndicator 字段扩展 — 单元测试 (T17)

运行方式：
    pytest test/test_generate_reports_json_multi_indicator.py -v
    python test/test_generate_reports_json_multi_indicator.py
"""

import json
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from generate_reports_json import generate_reports_json


class _Chdir:
    """临时切换工作目录的上下文管理器。"""
    def __init__(self, path):
        self._cwd = os.getcwd()
        self._path = path

    def __enter__(self):
        os.chdir(self._path)
        return self

    def __exit__(self, *exc):
        os.chdir(self._cwd)


def _make_html_dir(date_str):
    d = os.path.join("html", date_str)
    os.makedirs(d, exist_ok=True)
    return d


def _load_reports():
    with open("reports.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_multi_indicator_present():
    """场景1: 放置 multi_indicator_selection_<date>.html → multiIndicator 为正确相对路径。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _Chdir(tmp):
            date_str = "20260819"
            d = _make_html_dir(date_str)
            with open(os.path.join(d, f"stock_selection_{date_str}.html"), "w", encoding="utf-8") as f:
                f.write("<html>共选出 5 只</html>")
            with open(os.path.join(d, f"multi_indicator_selection_{date_str}.html"), "w", encoding="utf-8") as f:
                f.write("<html>multi</html>")
            generate_reports_json()
            data = _load_reports()
            rec = data["reports"][0]
            assert rec["multiIndicator"] == f"html/{date_str}/multi_indicator_selection_{date_str}.html"


def test_multi_indicator_absent():
    """场景2: 不放置单日报告 → multiIndicator 为 None。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _Chdir(tmp):
            date_str = "20260819"
            d = _make_html_dir(date_str)
            with open(os.path.join(d, f"stock_selection_{date_str}.html"), "w", encoding="utf-8") as f:
                f.write("<html>共选出 5 只</html>")
            generate_reports_json()
            data = _load_reports()
            rec = data["reports"][0]
            assert rec["multiIndicator"] is None


def test_multi_indicator_supplement_branch():
    """场景3: 根目录 macd_result_<date>.csv 补充分支 → multiIndicator 为 None。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _Chdir(tmp):
            date_str = "20260820"
            os.makedirs("html", exist_ok=True)
            with open(f"macd_result_{date_str}.csv", "w", encoding="utf-8-sig") as f:
                f.write("ts_code,name\n000001.SZ,平安银行\n")
            generate_reports_json()
            data = _load_reports()
            rec = data["reports"][0]
            assert rec["multiIndicator"] is None


def test_existing_fields_regression():
    """场景4: 现有 7 个字段取值与改动前一致（回归保障）。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _Chdir(tmp):
            date_str = "20260819"
            d = _make_html_dir(date_str)
            with open(os.path.join(d, f"stock_selection_{date_str}.html"), "w", encoding="utf-8") as f:
                f.write("<html>共选出 3 只</html>")
            with open(os.path.join(d, "industry_total_amount_trend.html"), "w", encoding="utf-8") as f:
                f.write("<html>ind</html>")
            with open(os.path.join(d, "first_j13_step_daily_count.html"), "w", encoding="utf-8") as f:
                f.write("<html>j13</html>")
            with open(os.path.join(d, f"macd_selection_{date_str}.html"), "w", encoding="utf-8") as f:
                f.write("<html>macd</html>")
            with open(os.path.join(d, "sentiment_rebound_strategy.html"), "w", encoding="utf-8") as f:
                f.write("<html>sent</html>")
            with open(f"macd_result_{date_str}.csv", "w", encoding="utf-8-sig") as f:
                f.write("ts_code,name\n000001.SZ,平安银行\n")
            generate_reports_json()
            data = _load_reports()
            rec = data["reports"][0]
            assert rec["date"] == date_str
            assert rec["stockSelection"] == f"html/{date_str}/stock_selection_{date_str}.html"
            assert rec["industryTrend"] == f"html/{date_str}/industry_total_amount_trend.html"
            assert rec["j13Trend"] == f"html/{date_str}/first_j13_step_daily_count.html"
            assert rec["macdResult"] == f"macd_result_{date_str}.csv"
            assert rec["macdHtml"] == f"html/{date_str}/macd_selection_{date_str}.html"
            assert rec["sentimentRebound"] == f"html/{date_str}/sentiment_rebound_strategy.html"
            assert "multiIndicator" in rec


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))