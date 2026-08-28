#!/usr/bin/env python3
"""
生成多指标联合选股超阈值行业趋势汇总页面

扫描 multi_indicator_hints_*.json 历史文件，聚合计算后渲染
ECharts 趋势图与统计表格至 html/multi_indicator_trend/index.html。

使用方式：
    python generate_multi_indicator_trend_html.py
"""

import json
import glob
import os
import re
import csv
import html as html_mod
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s | %(message)s")


def scan_hints_files(base_dir: str = "hints") -> list:
    """扫描 multi_indicator_hints_*.json 文件并按日期升序排序。

    Args:
        base_dir: 扫描基准目录，默认当前目录

    Returns:
        list[tuple[str, Path]]: [(date_str, file_path), ...] 按日期升序。
        文件名非 8 位数字的跳过，无匹配时返回 []。
    """
    pattern = os.path.join(base_dir, "multi_indicator_hints_*.json")
    results = []
    for fpath in glob.glob(pattern):
        fname = os.path.basename(fpath)
        match = re.match(r"multi_indicator_hints_(\d{8})\.json", fname)
        if not match:
            continue
        date_str = match.group(1)
        results.append((date_str, Path(fpath)))
    results.sort(key=lambda x: x[0])
    return results


def parse_hints_file(file_path) -> list:
    """解析单个 hints JSON 文件，容错返回 None。

    Args:
        file_path: JSON 文件路径（Path 或 str）

    Returns:
        list[dict] 净化后的提示清单，每条 {industry: str, count: int}。
        同 industry 聚合求和；损坏/结构不符返回 None。
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, list):
        return None

    industry_map = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        industry = item.get("industry")
        if not isinstance(industry, str) or not industry.strip():
            industry = "未知行业"
        count = item.get("count")
        if not isinstance(count, (int, float)) or count < 0:
            count = 0
        count = int(count)
        industry_map[industry] = industry_map.get(industry, 0) + count

    return [{"industry": k, "count": v} for k, v in industry_map.items()]


def build_trend_matrix(valid_records: list) -> dict:
    """构建行业×日期矩阵与汇总数据。

    Args:
        valid_records: list[tuple[str, list[dict]]] 有效 (date_str, hints) 列表

    Returns:
        dict: {dates, industries, matrix, daily_totals, global_max}
    """
    dates = [r[0] for r in valid_records]
    matrix = {}
    industry_days = {}

    for date_str, hints in valid_records:
        for h in hints:
            industry = h["industry"]
            count = h["count"]
            if industry not in matrix:
                matrix[industry] = {}
            matrix[industry][date_str] = count
            if count > 0:
                industry_days[industry] = industry_days.get(industry, 0) + 1

    industries = sorted(industry_days.keys(), key=lambda x: -industry_days[x])

    daily_totals = []
    global_max = 0
    for date_str in dates:
        total = 0
        for industry in matrix:
            v = matrix[industry].get(date_str, 0)
            total += v
            if v > global_max:
                global_max = v
        daily_totals.append(total)

    return {
        "dates": dates,
        "industries": industries,
        "matrix": matrix,
        "daily_totals": daily_totals,
        "global_max": global_max,
    }


def compute_trend_metrics(matrix_data: dict) -> dict:
    """计算持续天数、新进入/退出行业、概览统计。

    Args:
        matrix_data: build_trend_matrix 的返回值

    Returns:
        dict: {consecutive, new_enter, new_exit, summary}
    """
    dates = matrix_data["dates"]
    industries = matrix_data["industries"]
    matrix = matrix_data["matrix"]

    consecutive = []
    for industry in industries:
        latest_count = matrix[industry].get(dates[-1], 0) if dates else 0
        consecutive_days = 0
        for d in reversed(dates):
            if matrix[industry].get(d, 0) > 0:
                consecutive_days += 1
            else:
                break
        first_date = None
        total_days = 0
        for d in dates:
            if matrix[industry].get(d, 0) > 0:
                if first_date is None:
                    first_date = d
                total_days += 1
        consecutive.append({
            "industry": industry,
            "consecutive_days": consecutive_days,
            "latest_count": latest_count,
            "first_date": first_date or "-",
            "total_days": total_days,
        })
    consecutive.sort(key=lambda x: -x["consecutive_days"])

    new_enter = []
    new_exit = []
    if len(dates) >= 2:
        latest_set = {ind for ind in industries if matrix[ind].get(dates[-1], 0) > 0}
        prev_set = {ind for ind in industries if matrix[ind].get(dates[-2], 0) > 0}
        new_enter = sorted(latest_set - prev_set)
        new_exit = sorted(prev_set - latest_set)

    max_consec = consecutive[0]["consecutive_days"] if consecutive else 0
    max_consec_ind = consecutive[0]["industry"] if consecutive else "-"
    latest_count = sum(1 for ind in industries if matrix[ind].get(dates[-1], 0) > 0) if dates else 0

    summary = {
        "total_dates": len(dates),
        "total_industries": len(industries),
        "latest_industry_count": latest_count,
        "max_consecutive_days": max_consec,
        "max_consecutive_industry": max_consec_ind,
    }

    return {
        "consecutive": consecutive,
        "new_enter": new_enter,
        "new_exit": new_exit,
        "summary": summary,
    }


def build_echarts_options(matrix_data: dict, metrics: dict) -> dict:
    """构造 3 个 ECharts option dict（折线/堆叠柱/热力图）。

    Args:
        matrix_data: build_trend_matrix 的返回值
        metrics: compute_trend_metrics 的返回值

    Returns:
        dict: {line, bar, heatmap} 三个 ECharts option
    """
    dates = matrix_data["dates"]
    industries = matrix_data["industries"]
    matrix = matrix_data["matrix"]
    daily_totals = matrix_data["daily_totals"]
    global_max = matrix_data["global_max"]

    formatted_dates = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]

    line_option = {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "8%", "right": "5%", "top": "10%", "bottom": "15%"},
        "xAxis": {"type": "category", "data": formatted_dates, "axisLabel": {"rotate": 45}},
        "yAxis": {"type": "value", "name": "入选总数"},
        "series": [{"name": "超阈值行业总数", "type": "line", "data": daily_totals,
                     "smooth": True, "areaStyle": {"opacity": 0.3}}],
    }

    bar_series = []
    for industry in industries:
        data = [matrix[industry].get(d, 0) for d in dates]
        bar_series.append({"name": industry, "type": "bar", "stack": "total", "data": data})
    bar_option = {
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"data": industries, "top": "5%", "type": "scroll"},
        "grid": {"left": "8%", "right": "5%", "top": "20%", "bottom": "15%"},
        "xAxis": {"type": "category", "data": formatted_dates, "axisLabel": {"rotate": 45}},
        "yAxis": {"type": "value", "name": "入选数量"},
        "series": bar_series,
    }

    heatmap_data = []
    for ind_idx, industry in enumerate(industries):
        for date_idx, d in enumerate(dates):
            count = matrix[industry].get(d, 0)
            heatmap_data.append([date_idx, ind_idx, count])
    heatmap_option = {
        "tooltip": {"position": "top"},
        "grid": {"left": "15%", "right": "5%", "top": "10%", "bottom": "20%"},
        "xAxis": {"type": "category", "data": formatted_dates, "axisLabel": {"rotate": 45}},
        "yAxis": {"type": "category", "data": industries},
        "visualMap": {"min": 0, "max": global_max if global_max > 0 else 1,
                       "calculable": True, "orient": "horizontal", "left": "center", "bottom": "5%"},
        "series": [{"name": "入选数量", "type": "heatmap", "data": heatmap_data,
                     "label": {"show": False}}],
    }

    return {"line": line_option, "bar": bar_option, "heatmap": heatmap_option}


def render_trend_html(matrix_data: dict, metrics: dict, echarts_options: dict,
                      stock_details: list = None) -> str:
    """渲染完整 HTML 字符串。

    Args:
        matrix_data: build_trend_matrix 的返回值
        metrics: compute_trend_metrics 的返回值
        echarts_options: build_echarts_options 的返回值
        stock_details: 最新日期入选个股列表 [{ts_code, name, industry}, ...]

    Returns:
        str: 完整 HTML 页面字符串
    """
    dates = matrix_data["dates"]
    summary = metrics["summary"]
    consecutive = metrics["consecutive"]
    new_enter = metrics["new_enter"]
    new_exit = metrics["new_exit"]
    has_data = len(dates) > 0 and len(matrix_data["industries"]) > 0
    stock_details = stock_details or []

    last_update = dates[-1] if dates else "无数据"
    options_json = json.dumps(echarts_options, ensure_ascii=False)

    consec_rows = ""
    for item in consecutive:
        industry = html_mod.escape(str(item["industry"]))
        consec_rows += f"""
            <tr class="hover:bg-gray-50">
                <td class="px-4 py-3 font-medium text-gray-900">{industry}</td>
                <td class="px-4 py-3 text-right">{item['latest_count']}</td>
                <td class="px-4 py-3 text-right font-semibold text-blue-600">{item['consecutive_days']}</td>
                <td class="px-4 py-3 text-center">{item['first_date']}</td>
                <td class="px-4 py-3 text-right">{item['total_days']}</td>
            </tr>"""

    new_enter_tags = "".join(
        f'<span class="inline-block bg-green-100 text-green-700 text-xs px-2 py-1 rounded-full mr-2 mb-1">{html_mod.escape(str(ind))}</span>'
        for ind in new_enter
    ) or '<span class="text-gray-400 text-sm">无</span>'

    new_exit_tags = "".join(
        f'<span class="inline-block bg-gray-200 text-gray-600 text-xs px-2 py-1 rounded-full mr-2 mb-1">{html_mod.escape(str(ind))}</span>'
        for ind in new_exit
    ) or '<span class="text-gray-400 text-sm">无</span>'

    chart_section = ""
    if has_data:
        chart_section = f"""
        <div class="bg-white rounded-xl shadow-lg p-6 mb-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-4">📈 每日超阈值行业总数趋势</h2>
            <div id="chartLine" style="width:100%;height:350px;"></div>
        </div>
        <div class="bg-white rounded-xl shadow-lg p-6 mb-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-4">📊 各行业入选数量变化（堆叠柱状图）</h2>
            <div id="chartBar" style="width:100%;height:450px;"></div>
        </div>
        <div class="bg-white rounded-xl shadow-lg p-6 mb-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-4">🔥 行业×日期入选数量热力图</h2>
            <div id="chartHeatmap" style="width:100%;height:500px;"></div>
        </div>"""
    else:
        chart_section = """
        <div class="bg-white rounded-xl shadow-lg p-6 mb-6 text-center text-gray-500">
            暂无超阈值行业数据
        </div>"""

    consec_section = f"""
        <div class="bg-white rounded-xl shadow-lg p-6 mb-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-4">🏆 持续超阈值行业</h2>
            <div class="overflow-x-auto">
                <table class="w-full text-sm text-left">
                    <thead class="text-xs text-gray-700 uppercase bg-gray-50">
                        <tr>
                            <th class="px-4 py-3">行业</th>
                            <th class="px-4 py-3 text-right">最新入选数</th>
                            <th class="px-4 py-3 text-right">连续天数</th>
                            <th class="px-4 py-3 text-center">首次超阈值日期</th>
                            <th class="px-4 py-3 text-right">历史总天数</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-200">
                        {consec_rows if consec_rows else '<tr><td colspan="5" class="px-4 py-6 text-center text-gray-400">暂无数据</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>""" if has_data else ""

    stock_rows = ""
    for s in stock_details:
        code = html_mod.escape(str(s.get("ts_code", "")))
        name = html_mod.escape(str(s.get("name", "")))
        ind = html_mod.escape(str(s.get("industry", "")))
        stock_rows += f"""
            <tr class="hover:bg-gray-50" data-industry="{ind}">
                <td class="px-4 py-2 font-mono text-xs text-gray-600">{code}</td>
                <td class="px-4 py-2 font-medium text-gray-900">{name}</td>
                <td class="px-4 py-2 text-gray-700">{ind}</td>
            </tr>"""

    stock_industries = sorted(set(html_mod.escape(str(s.get("industry", ""))) for s in stock_details))
    industry_options = '<option value="">全部行业</option>' + "".join(
        f'<option value="{ind}">{ind}</option>' for ind in stock_industries
    )

    latest_date_label = f"{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:]}" if dates else ""
    stock_section = f"""
        <div class="bg-white rounded-xl shadow-lg p-6 mb-6">
            <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
                <h2 class="text-lg font-semibold text-gray-900">📋 最新入选个股明细（{latest_date_label}，<span id="stockCount">{len(stock_details)}</span> / {len(stock_details)} 只）</h2>
                <select id="industryFilter" onchange="filterStocks()" class="border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-700 focus:ring-2 focus:ring-blue-500 focus:border-blue-500">
                    {industry_options}
                </select>
            </div>
            <div class="overflow-x-auto" style="max-height:500px;overflow-y:auto;">
                <table class="w-full text-sm text-left">
                    <thead class="text-xs text-gray-700 uppercase bg-gray-50 sticky top-0">
                        <tr>
                            <th class="px-4 py-3">代码</th>
                            <th class="px-4 py-3">名称</th>
                            <th class="px-4 py-3">行业</th>
                        </tr>
                    </thead>
                    <tbody id="stockBody" class="divide-y divide-gray-200">
                        {stock_rows if stock_rows else '<tr><td colspan="3" class="px-4 py-6 text-center text-gray-400">暂无个股数据</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>
        <script>
        function filterStocks() {{
            const sel = document.getElementById('industryFilter').value;
            const rows = document.querySelectorAll('#stockBody tr[data-industry]');
            let visible = 0;
            rows.forEach(r => {{
                const show = !sel || r.getAttribute('data-industry') === sel;
                r.style.display = show ? '' : 'none';
                if (show) visible++;
            }});
            document.getElementById('stockCount').textContent = visible;
        }}
        </script>""" if stock_details else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>多指标选股趋势汇总</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>body {{ font-family: Inter, sans-serif; }}</style>
</head>
<body class="bg-gradient-to-br from-slate-50 to-blue-50 min-h-screen">
    <div class="max-w-7xl mx-auto px-4 py-8">
        <a href="../../index.html" class="inline-flex items-center text-blue-600 hover:text-blue-800 mb-6">
            <svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7"/></svg>
            返回首页
        </a>

        <div class="mb-8">
            <h1 class="text-3xl font-bold text-gray-900 mb-2">📊 多指标选股趋势汇总</h1>
            <p class="text-gray-600">超阈值行业历史趋势 · 持续强势识别 · 行业轮动信号</p>
            <p class="text-sm text-gray-500 mt-1">最后更新: {last_update}</p>
        </div>

        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <div class="bg-white rounded-xl shadow p-4 text-center">
                <div class="text-2xl font-bold text-blue-600">{summary['total_dates']}</div>
                <div class="text-xs text-gray-500 mt-1">历史交易日数</div>
            </div>
            <div class="bg-white rounded-xl shadow p-4 text-center">
                <div class="text-2xl font-bold text-teal-600">{summary['total_industries']}</div>
                <div class="text-xs text-gray-500 mt-1">行业总数</div>
            </div>
            <div class="bg-white rounded-xl shadow p-4 text-center">
                <div class="text-2xl font-bold text-orange-600">{summary['latest_industry_count']}</div>
                <div class="text-xs text-gray-500 mt-1">最新超阈值数</div>
            </div>
            <div class="bg-white rounded-xl shadow p-4 text-center">
                <div class="text-2xl font-bold text-purple-600">{summary['max_consecutive_days']}</div>
                <div class="text-xs text-gray-500 mt-1">最长连续天数</div>
            </div>
        </div>

        {chart_section}

        {consec_section}

        <div class="bg-white rounded-xl shadow-lg p-6 mb-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-4">🟢 新进入 / ⚪ 退出行业</h2>
            <div class="mb-3">
                <span class="text-sm font-medium text-gray-700 mr-2">新进入:</span>
                {new_enter_tags}
            </div>
            <div>
                <span class="text-sm font-medium text-gray-700 mr-2">退出:</span>
                {new_exit_tags}
            </div>
        </div>

        {stock_section}

        <div class="text-center mt-8 text-gray-500 text-sm">
            <p>多指标联合选股趋势汇总 · 自动生成</p>
        </div>
    </div>

    <script>
    const chartOptions = {options_json};
    if (chartOptions.line && chartOptions.line.series && chartOptions.line.series.length > 0) {{
        const cl = echarts.init(document.getElementById('chartLine'));
        cl.setOption(chartOptions.line);
        const cb = echarts.init(document.getElementById('chartBar'));
        cb.setOption(chartOptions.bar);
        const ch = echarts.init(document.getElementById('chartHeatmap'));
        ch.setOption(chartOptions.heatmap);
        window.addEventListener('resize', () => {{ cl.resize(); cb.resize(); ch.resize(); }});
    }}
    </script>
</body>
</html>"""


def scan_result_csvs(base_dir: str = ".") -> list:
    """扫描 multi_indicator_result_*.csv 文件并按日期升序排序。

    Returns:
        list[tuple[str, Path]]: [(date_str, file_path), ...] 按日期升序。
    """
    pattern = os.path.join(base_dir, "multi_indicator_result_*.csv")
    results = []
    for fpath in glob.glob(pattern):
        fname = os.path.basename(fpath)
        match = re.match(r"multi_indicator_result_(\d{8})\.csv", fname)
        if not match:
            continue
        date_str = match.group(1)
        results.append((date_str, Path(fpath)))
    results.sort(key=lambda x: x[0])
    return results


def parse_result_csv(file_path) -> list:
    """解析个股结果 CSV，返回 [{ts_code, name, industry}, ...]。"""
    stocks = []
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stocks.append({
                    "ts_code": row.get("ts_code", ""),
                    "name": row.get("name", ""),
                    "industry": row.get("industry_name", ""),
                })
    except Exception:
        return []
    return stocks


def generate_multi_indicator_trend_html() -> None:
    """主入口：扫描 hints 文件 → 聚合计算 → 渲染汇总页面。

    无文件或全异常时仍生成空数据页面。
    """
    files = scan_hints_files()
    valid_records = []
    skipped = 0

    for date_str, fpath in files:
        hints = parse_hints_file(fpath)
        if hints is None:
            logging.warning("跳过损坏文件: %s", fpath)
            skipped += 1
            continue
        valid_records.append((date_str, hints))

    logging.info("扫描完成: %d 个文件, 有效 %d, 跳过 %d", len(files), len(valid_records), skipped)

    stock_details = []
    csv_files = scan_result_csvs()
    if csv_files:
        latest_csv_date, latest_csv_path = csv_files[-1]
        stock_details = parse_result_csv(latest_csv_path)
        logging.info("个股明细: %s, %d 只", latest_csv_date, len(stock_details))

    matrix_data = build_trend_matrix(valid_records)
    metrics = compute_trend_metrics(matrix_data)
    echarts_options = build_echarts_options(matrix_data, metrics)
    html_content = render_trend_html(matrix_data, metrics, echarts_options,
                                     stock_details=stock_details)

    output_dir = Path("html/multi_indicator_trend")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    logging.info("汇总页面已生成: %s (日期数=%d, 行业数=%d)",
                 output_path, len(matrix_data["dates"]), len(matrix_data["industries"]))


if __name__ == "__main__":
    generate_multi_indicator_trend_html()