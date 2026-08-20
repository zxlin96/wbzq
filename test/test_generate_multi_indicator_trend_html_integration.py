#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_multi_indicator_trend_html.py 主入口集成测试 (T19)

运行方式：
    pytest test/test_generate_multi_indicator_trend_html_integration.py -v
    python test/test_generate_multi_indicator_trend_html_integration.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from generate_multi_indicator_trend_html import generate_multi_indicator_trend_html


class _Chdir:
    def __init__(self, path):
        self._cwd = os.getcwd()
        self._path = path

    def __enter__(self):
        os.chdir(self._path)
        return self

    def __exit__(self, *exc):
        os.chdir(self._cwd)


def _write_hints(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_full_flow_multiple_valid():
    """场景1: 多个合法 hints 文件 → 生成汇总页面且包含所有日期数据。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _Chdir(tmp):
            _write_hints(Path(tmp, "multi_indicator_hints_20260818.json"),
                         [{"industry": "电子", "count": 5}])
            _write_hints(Path(tmp, "multi_indicator_hints_20260819.json"),
                         [{"industry": "电子", "count": 3}, {"industry": "医药", "count": 2}])
            generate_multi_indicator_trend_html()
            out = Path("html/multi_indicator_trend/index.html")
            assert out.exists()
            html = out.read_text(encoding="utf-8")
            assert "2026-08-18" in html
            assert "2026-08-19" in html
            assert "电子" in html
            assert "医药" in html


def test_full_flow_corrupted_skipped():
    """场景2: 混入损坏 hints 文件 → 脚本不中断、其他日期数据正常。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _Chdir(tmp):
            _write_hints(Path(tmp, "multi_indicator_hints_20260818.json"),
                         [{"industry": "电子", "count": 5}])
            Path(tmp, "multi_indicator_hints_20260819.json").write_text("{broken!!!", encoding="utf-8")
            generate_multi_indicator_trend_html()
            out = Path("html/multi_indicator_trend/index.html")
            assert out.exists()
            html = out.read_text(encoding="utf-8")
            assert "2026-08-18" in html
            assert "电子" in html


def test_full_flow_empty_dir():
    """场景3: 空目录 → 仍生成空数据页面，含"暂无超阈值行业数据"文案。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _Chdir(tmp):
            generate_multi_indicator_trend_html()
            out = Path("html/multi_indicator_trend/index.html")
            assert out.exists()
            html = out.read_text(encoding="utf-8")
            assert "暂无超阈值行业数据" in html


def test_full_flow_all_empty_lists():
    """场景4: 所有 hints 文件为 [] → 生成空数据页面。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _Chdir(tmp):
            _write_hints(Path(tmp, "multi_indicator_hints_20260818.json"), [])
            _write_hints(Path(tmp, "multi_indicator_hints_20260819.json"), [])
            generate_multi_indicator_trend_html()
            out = Path("html/multi_indicator_trend/index.html")
            assert out.exists()
            html = out.read_text(encoding="utf-8")
            assert "暂无超阈值行业数据" in html


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))