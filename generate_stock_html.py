#!/usr/bin/env python3
"""
股票选股结果HTML报告生成器
支持个股K线图、成交额、KDJ指标展示
"""

import pandas as pd
import json


def generate_stock_charts(stock_data, ts_code, name):
    """为单只股票生成K线、成交额、KDJ图表的JSON配置"""
    if stock_data.empty:
        return None
    
    # 使用所有可用数据，不限于60天
    stock_data = stock_data.sort_values('trade_date')
    dates = stock_data['trade_date'].astype(str).tolist()
    
    # K线图数据 [日期, 开盘, 收盘, 最高, 最低]
    candlestick_data = []
    for _, row in stock_data.iterrows():
        candlestick_data.append([
            str(row['trade_date']),
            float(row['open_qfq']) if pd.notna(row['open_qfq']) else float(row['close_qfq']),
            float(row['close_qfq']),
            float(row['high_qfq']) if pd.notna(row['high_qfq']) else float(row['close_qfq']),
            float(row['low_qfq']) if pd.notna(row['low_qfq']) else float(row['close_qfq'])
        ])
    
    # 计算涨跌幅
    price_change_data = []
    for i, (_, row) in enumerate(stock_data.iterrows()):
        if i > 0:
            prev_close = stock_data.iloc[i-1]['close_qfq']
            change_pct = (row['close_qfq'] - prev_close) / prev_close * 100 if prev_close != 0 else 0
        else:
            change_pct = 0
        price_change_data.append(round(change_pct, 2))
    
    # 成交额数据 - 倍量判断
    volume_data = []
    for i, (idx, row) in enumerate(stock_data.iterrows()):
        is_up = row['close_qfq'] >= row['open_qfq']
        
        # 判断是否倍量（当天成交额 >= 前一天成交额的2倍）
        is_double_volume = False
        if i > 0:
            prev_amount = stock_data.iloc[i-1]['amount']
            if prev_amount > 0 and row['amount'] >= prev_amount * 2:
                is_double_volume = True
        
        # 颜色逻辑：倍量上涨=黄色，倍量下跌=紫色，正常上涨=红色，正常下跌=绿色
        if is_double_volume:
            color = '#fbbf24' if is_up else '#a855f7'  # 黄色 : 紫色
        else:
            color = '#ef4444' if is_up else '#22c55e'  # 红色 : 绿色
        
        volume_data.append({
            'value': float(row['amount']) / 10000,  # 转换为万元
            'itemStyle': {'color': color},
            'is_double': is_double_volume
        })
    
    # KDJ数据
    k_data = stock_data['kdj_k_qfq'].fillna(0).tolist() if 'kdj_k_qfq' in stock_data.columns else [0] * len(dates)
    d_data = stock_data['kdj_d_qfq'].fillna(0).tolist() if 'kdj_d_qfq' in stock_data.columns else [0] * len(dates)
    j_data = stock_data['kdj_qfq'].fillna(0).tolist()
    
    # MA60数据
    ma60_data = stock_data['ma_qfq_60'].ffill().tolist() if 'ma_qfq_60' in stock_data.columns else []
    
    # 多空指标数据
    zhixing_duokong_data = stock_data['zhixing_duokong'].ffill().tolist() if 'zhixing_duokong' in stock_data.columns else []
    zhixing_mid_duokong_data = stock_data['zhixing_mid_duokong'].ffill().tolist() if 'zhixing_mid_duokong' in stock_data.columns else []
    
    chart_config = {
        'ts_code': ts_code,
        'name': name,
        'dates': dates,
        'candlestick': candlestick_data,
        'volume': volume_data,
        'price_change': price_change_data,
        'kdj_k': [float(x) for x in k_data],
        'kdj_d': [float(x) for x in d_data],
        'kdj_j': [float(x) for x in j_data],
        'ma60': [float(x) for x in ma60_data] if ma60_data else [],
        'zhixing_duokong': [float(x) for x in zhixing_duokong_data] if zhixing_duokong_data else [],
        'zhixing_mid_duokong': [float(x) for x in zhixing_mid_duokong_data] if zhixing_mid_duokong_data else []
    }
    
    return chart_config


def generate_stock_selection_html(result, df, end_date, industry_count):
    """生成交互式选股结果HTML报告，支持下载CSV和个股图表"""
    if result.empty:
        return
    
    # 创建 html/日期 目录
    import os
    html_dir = os.path.join('html', end_date)
    os.makedirs(html_dir, exist_ok=True)
    
    # 准备表格数据
    table_data = []
    stock_charts = {}  # 存储每只股票的数据
    
    for _, row in result.iterrows():
        ts_code = row['ts_code']
        name = row['name']
        
        # 获取该股票的历史数据（最近60天）
        stock_history = df[df['ts_code'] == ts_code].copy()
        
        # 生成图表数据
        chart_data = generate_stock_charts(stock_history, ts_code, name)
        if chart_data:
            stock_charts[ts_code] = chart_data
        
        cycle_data = df[(df['ts_code'] == ts_code) & (df['trade_date'] <= row['trade_date'])]
        cycle_max = cycle_data['amount'].max() if not cycle_data.empty else 0
        today_vol = row['amount']
        is_lowest_volume = today_vol <= cycle_max * 0.30 if cycle_max else False
        bvk_count = df[(df['ts_code'] == ts_code) & df['bottom_violent_k']].shape[0] if 'bottom_violent_k' in df.columns else 0
        
        table_data.append({
            '代码': ts_code,
            '名称': name,
            '行业': row['industry_name'] if pd.notna(row['industry_name']) else '未知',
            '日期': str(row['trade_date']),
            '收盘价': f"{row['close_qfq']:.2f}",
            '60日线': f"{row['ma_qfq_60']:.2f}",
            'J值': f"{row['kdj_qfq']:.2f}",
            'MACD-DIF': f"{row['macd_dif_qfq']:.4f}",
            '成交额': f"{row['amount']:.2f}",
            '60日线趋势': '✅' if row['ma60_upward'] else '❌',
            '回调最低量': '✅' if is_lowest_volume else '❌',
            '成交额前60%': '✅' if row['is_amount_top30'] else '❌',
            '底部暴力K': f'{bvk_count}次' if bvk_count > 0 else '❌',
        })
    
    # 创建DataFrame用于CSV下载
    result_df = pd.DataFrame(table_data)
    csv_data = result_df.to_csv(index=False, encoding='utf-8-sig')
    
    # 行业分布数据
    industry_data = []
    for industry, count in industry_count.items():
        industry_data.append({'行业': industry, '股票数': count})
    industry_df = pd.DataFrame(industry_data)
    industry_csv = industry_df.to_csv(index=False, encoding='utf-8-sig')
    
    # 股票图表数据JSON
    charts_json = json.dumps(stock_charts, ensure_ascii=False, default=str)
    
    # 生成HTML
    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>选股结果报告 - {end_date}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; }}
        .sortable th {{ cursor: pointer; user-select: none; }}
        .sortable th:hover {{ background-color: #f3f4f6; }}
        .sort-asc::after {{ content: " ▲"; }}
        .sort-desc::after {{ content: " ▼"; }}
        .chart-container {{ height: 400px; }}
        .modal {{ display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); }}
        .modal-content {{ background-color: #fefefe; margin: 2% auto; padding: 20px; border-radius: 12px; width: 90%; max-width: 1200px; max-height: 90vh; overflow-y: auto; }}
        .close {{ color: #aaa; float: right; font-size: 28px; font-weight: bold; cursor: pointer; }}
        .close:hover {{ color: black; }}
    </style>
</head>
<body class="bg-gray-50 min-h-screen">
    <div class="max-w-7xl mx-auto px-4 py-8">
        <!-- 标题区域 -->
        <div class="bg-white rounded-xl shadow-sm p-6 mb-6">
            <div class="flex justify-between items-center">
                <div>
                    <h1 class="text-2xl font-bold text-gray-900">📊 选股结果报告</h1>
                    <p class="text-gray-500 mt-1">日期: {end_date} | 共选出 {len(result)} 只股票</p>
                </div>
                <div class="flex gap-3">
                    <a href="sentiment_rebound_strategy.html" 
                       class="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"></path>
                        </svg>
                        情绪反弹策略
                    </a>
                    <button onclick="downloadCSV('stock_selection')" 
                            class="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                        </svg>
                        下载选股结果 CSV
                    </button>
                    <button onclick="downloadCSV('industry')" 
                            class="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                        </svg>
                        下载行业分布 CSV
                    </button>
                </div>
            </div>
        </div>
        
        <!-- 统计卡片 -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
            <div class="bg-white rounded-xl shadow-sm p-4">
                <div class="text-sm text-gray-500">选股总数</div>
                <div class="text-2xl font-bold text-blue-600">{len(result)}</div>
            </div>
            <div class="bg-white rounded-xl shadow-sm p-4">
                <div class="text-sm text-gray-500">涉及行业</div>
                <div class="text-2xl font-bold text-green-600">{len(industry_count)}</div>
            </div>
            <div class="bg-white rounded-xl shadow-sm p-4">
                <div class="text-sm text-gray-500">最多行业</div>
                <div class="text-lg font-bold text-purple-600">{industry_count.index[0] if len(industry_count) > 0 else '-'}</div>
                <div class="text-sm text-gray-400">{industry_count.iloc[0] if len(industry_count) > 0 else 0} 只</div>
            </div>
            <div class="bg-white rounded-xl shadow-sm p-4">
                <div class="text-sm text-gray-500">平均J值</div>
                <div class="text-2xl font-bold text-orange-600">{result['kdj_qfq'].mean():.2f}</div>
            </div>
        </div>
        
        <!-- 选股结果表格 -->
        <div class="bg-white rounded-xl shadow-sm overflow-hidden">
            <div class="px-6 py-4 border-b border-gray-200">
                <h2 class="text-lg font-semibold text-gray-900">📋 选股明细（点击代码查看图表）</h2>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-sm text-left sortable" id="stockTable">
                    <thead class="text-xs text-gray-700 uppercase bg-gray-50">
                        <tr>
                            <th class="px-6 py-3" onclick="sortTable(0)">代码</th>
                            <th class="px-6 py-3" onclick="sortTable(1)">名称</th>
                            <th class="px-6 py-3" onclick="sortTable(2)">行业</th>
                            <th class="px-6 py-3" onclick="sortTable(3)">日期</th>
                            <th class="px-6 py-3" onclick="sortTable(4)">收盘价</th>
                            <th class="px-6 py-3" onclick="sortTable(5)">60日线</th>
                            <th class="px-6 py-3" onclick="sortTable(6)">J值</th>
                            <th class="px-6 py-3" onclick="sortTable(7)">MACD-DIF</th>
                            <th class="px-6 py-3" onclick="sortTable(8)">成交额</th>
                            <th class="px-6 py-3" onclick="sortTable(9)">60日线趋势</th>
                            <th class="px-6 py-3" onclick="sortTable(10)">回调最低量</th>
                            <th class="px-6 py-3" onclick="sortTable(11)">成交额前60%</th>
                            <th class="px-6 py-3" onclick="sortTable(12)">底部暴力K</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-200">
                        {''.join([f"""
                        <tr class="hover:bg-gray-50 cursor-pointer" onclick="showChart('{row['代码']}', '{row['名称']}')">
                            <td class="px-6 py-4 font-medium text-blue-600 hover:text-blue-800">{row['代码']}</td>
                            <td class="px-6 py-4">{row['名称']}</td>
                            <td class="px-6 py-4"><span class="px-2 py-1 bg-blue-100 text-blue-800 rounded-full text-xs">{row['行业']}</span></td>
                            <td class="px-6 py-4">{row['日期']}</td>
                            <td class="px-6 py-4">{row['收盘价']}</td>
                            <td class="px-6 py-4">{row['60日线']}</td>
                            <td class="px-6 py-4 font-semibold {'text-red-600' if float(row['J值']) < 0 else 'text-orange-600' if float(row['J值']) < 10 else 'text-green-600'}">{row['J值']}</td>
                            <td class="px-6 py-4">{row['MACD-DIF']}</td>
                            <td class="px-6 py-4">{row['成交额']}</td>
                            <td class="px-6 py-4">{row['60日线趋势']}</td>
                            <td class="px-6 py-4">{row['回调最低量']}</td>
                            <td class="px-6 py-4">{row['成交额前60%']}</td>
                            <td class="px-6 py-4">{row['底部暴力K']}</td>
                        </tr>
                        """ for row in table_data])}
                    </tbody>
                </table>
            </div>
        </div>
        
        <!-- 行业分布 -->
        <div class="bg-white rounded-xl shadow-sm p-6 mt-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-4">📊 行业分布</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                {''.join([f"""
                <div class="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                    <span class="text-sm text-gray-600">{industry}</span>
                    <span class="text-sm font-bold text-blue-600">{count}只</span>
                </div>
                """ for industry, count in industry_count.items()])}
            </div>
        </div>
    </div>
    
    <!-- 图表模态框 -->
    <div id="chartModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal()">&times;</span>
            <h2 id="modalTitle" class="text-xl font-bold mb-4"></h2>
            <div id="klineChart" class="chart-container mb-4"></div>
            <div id="volumeChart" class="chart-container mb-4" style="height: 150px;"></div>
            <div id="kdjChart" class="chart-container" style="height: 200px;"></div>
        </div>
    </div>
    
    <script>
        // 股票图表数据
        const stockCharts = {charts_json};
        
        // CSV数据
        const stockCSV = `{csv_data}`;
        const industryCSV = `{industry_csv}`;
        
        // 下载CSV函数
        function downloadCSV(type) {{
            const csv = type === 'stock_selection' ? stockCSV : industryCSV;
            const filename = type === 'stock_selection' ? 'stock_selection_{end_date}.csv' : 'industry_distribution_{end_date}.csv';
            const blob = new Blob(['\\uFEFF' + csv], {{ type: 'text/csv;charset=utf-8;' }});
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = filename;
            link.click();
        }}
        
        // 表格排序
        let sortDirection = {{}};
        function sortTable(columnIndex) {{
            const table = document.getElementById('stockTable');
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            sortDirection[columnIndex] = !sortDirection[columnIndex];
            
            table.querySelectorAll('th').forEach((th, idx) => {{
                th.classList.remove('sort-asc', 'sort-desc');
                if (idx === columnIndex) {{
                    th.classList.add(sortDirection[columnIndex] ? 'sort-desc' : 'sort-asc');
                }}
            }});
            
            rows.sort((a, b) => {{
                const aVal = a.cells[columnIndex].textContent.trim();
                const bVal = b.cells[columnIndex].textContent.trim();
                const aNum = parseFloat(aVal);
                const bNum = parseFloat(bVal);
                
                if (!isNaN(aNum) && !isNaN(bNum)) {{
                    return sortDirection[columnIndex] ? bNum - aNum : aNum - bNum;
                }}
                return sortDirection[columnIndex] ? bVal.localeCompare(aVal) : aVal.localeCompare(bVal);
            }});
            
            rows.forEach(row => tbody.appendChild(row));
        }}
        
        // 显示图表
        let klineChart, volumeChart, kdjChart;
        
        function showChart(tsCode, name) {{
            const data = stockCharts[tsCode];
            if (!data) {{
                alert('该股票暂无图表数据');
                return;
            }}
            
            document.getElementById('modalTitle').textContent = `${{name}} (${{tsCode}}) - 技术分析`;
            document.getElementById('chartModal').style.display = 'block';
            
            // 初始化或销毁旧图表
            if (klineChart) klineChart.dispose();
            if (volumeChart) volumeChart.dispose();
            if (kdjChart) kdjChart.dispose();
            
            // K线图
            klineChart = echarts.init(document.getElementById('klineChart'));
            
            // 构建K线数据（包含涨跌幅）
            const candlestickWithChange = data.candlestick.map((d, i) => ({{
                value: [d[1], d[2], d[3], d[4]],
                itemStyle: {{
                    color: d[2] >= d[1] ? '#ef4444' : '#22c55e',
                    color0: d[2] >= d[1] ? '#ef4444' : '#22c55e',
                    borderColor: d[2] >= d[1] ? '#ef4444' : '#22c55e',
                    borderColor0: d[2] >= d[1] ? '#ef4444' : '#22c55e'
                }}
            }}));
            
            klineChart.setOption({{
                title: {{ text: 'K线图 + MA60 + 多空指标', left: 'center' }},
                tooltip: {{ 
                    trigger: 'axis', 
                    axisPointer: {{ type: 'cross' }},
                    formatter: function(params) {{
                        let result = params[0].axisValue + '<br/>';
                        params.forEach(param => {{
                            if (param.seriesType === 'candlestick') {{
                                const dataIndex = param.dataIndex;
                                const change = data.price_change[dataIndex];
                                const changeColor = change >= 0 ? '#ef4444' : '#22c55e';
                                const changeSymbol = change >= 0 ? '+' : '';
                                // 从原始数据获取开盘收盘最高最低
                                const rawData = data.candlestick[dataIndex];
                                const open = rawData[1];
                                const close = rawData[2];
                                const high = rawData[3];
                                const low = rawData[4];
                                result += `涨跌幅: <span style="color:${{changeColor}}">${{changeSymbol}}${{change}}%</span><br/>`;
                                result += `开盘: ${{open}}<br/>`;
                                result += `收盘: ${{close}}<br/>`;
                                result += `最高: ${{high}}<br/>`;
                                result += `最低: ${{low}}<br/>`;
                            }} else if (param.seriesName === 'MA60') {{
                                result += `MA60: ${{param.data}}<br/>`;
                            }} else if (param.seriesName === '多空指标') {{
                                result += `多空指标: ${{param.data}}<br/>`;
                            }} else if (param.seriesName === '中多空') {{
                                result += `中多空: ${{param.data}}<br/>`;
                            }}
                        }});
                        return result;
                    }}
                }},
                legend: {{ data: ['K线', 'MA60', '多空指标', '中多空'], top: 30 }},
                grid: {{ left: '10%', right: '10%', bottom: '15%', top: '80px' }},
                xAxis: {{ type: 'category', data: data.dates, scale: true }},
                yAxis: {{ type: 'value', scale: true }},
                dataZoom: [{{ type: 'inside' }}, {{ type: 'slider', start: 70, end: 100 }}],
                series: [
                    {{
                        type: 'candlestick',
                        name: 'K线',
                        data: candlestickWithChange,
                        itemStyle: {{ color: '#ef4444', color0: '#22c55e', borderColor: '#ef4444', borderColor0: '#22c55e' }}
                    }},
                    {{
                        type: 'line',
                        name: 'MA60',
                        data: data.ma60,
                        smooth: true,
                        lineStyle: {{ color: '#f59e0b', width: 2 }},
                        symbol: 'none'
                    }},
                    {{
                        type: 'line',
                        name: '多空指标',
                        data: data.zhixing_duokong,
                        smooth: true,
                        lineStyle: {{ color: '#3b82f6', width: 2, type: 'dashed' }},
                        symbol: 'none'
                    }},
                    {{
                        type: 'line',
                        name: '中多空',
                        data: data.zhixing_mid_duokong,
                        smooth: true,
                        lineStyle: {{ color: '#10b981', width: 2, type: 'dotted' }},
                        symbol: 'none'
                    }}
                ]
            }});
            
            // 成交额图
            volumeChart = echarts.init(document.getElementById('volumeChart'));
            volumeChart.setOption({{
                title: {{ text: '成交额（万元）- 黄色=倍量上涨, 紫色=倍量下跌', left: 'center', textStyle: {{ fontSize: 12 }} }},
                tooltip: {{ 
                    trigger: 'axis',
                    formatter: function(params) {{
                        const dataIndex = params[0].dataIndex;
                        const volData = data.volume[dataIndex];
                        const isDouble = volData.is_double ? '是' : '否';
                        return params[0].axisValue + '<br/>' +
                               '成交额: ' + params[0].value.toFixed(2) + ' 万元<br/>' +
                               '是否倍量: ' + isDouble;
                    }}
                }},
                grid: {{ left: '10%', right: '10%', top: '40px', bottom: '20px' }},
                xAxis: {{ type: 'category', data: data.dates, show: false }},
                yAxis: {{ type: 'value' }},
                series: [{{
                    type: 'bar',
                    data: data.volume,
                    itemStyle: {{
                        color: function(params) {{
                            return data.volume[params.dataIndex].itemStyle.color;
                        }}
                    }}
                }}]
            }});
            
            // KDJ图
            kdjChart = echarts.init(document.getElementById('kdjChart'));
            kdjChart.setOption({{
                title: {{ text: 'KDJ指标', left: 'center', textStyle: {{ fontSize: 14 }} }},
                tooltip: {{ trigger: 'axis' }},
                legend: {{ data: ['K', 'D', 'J'], bottom: 0 }},
                grid: {{ left: '10%', right: '10%', top: '40px', bottom: '40px' }},
                xAxis: {{ type: 'category', data: data.dates }},
                yAxis: {{ type: 'value', min: 0, max: 100 }},
                series: [
                    {{ type: 'line', data: data.kdj_k, name: 'K', smooth: true, lineStyle: {{ color: '#3b82f6' }} }},
                    {{ type: 'line', data: data.kdj_d, name: 'D', smooth: true, lineStyle: {{ color: '#f59e0b' }} }},
                    {{ type: 'line', data: data.kdj_j, name: 'J', smooth: true, lineStyle: {{ color: '#ef4444' }} }}
                ]
            }});
            
            // 响应式
            window.addEventListener('resize', () => {{
                klineChart.resize();
                volumeChart.resize();
                kdjChart.resize();
            }});
        }}
        
        function closeModal() {{
            document.getElementById('chartModal').style.display = 'none';
        }}
        
        // 点击模态框外部关闭
        window.onclick = function(event) {{
            const modal = document.getElementById('chartModal');
            if (event.target == modal) {{
                modal.style.display = 'none';
            }}
        }}
    </script>
</body>
</html>'''
    
    # 保存HTML文件
    filename = os.path.join(html_dir, f"stock_selection_{end_date}.html")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"\n📊 已生成交互式选股报告: {filename}")
    print(f"   包含功能: 表格排序、CSV下载、行业分布统计、个股K线/成交额/KDJ图表")


def generate_c154_html(result, df, end_date, funnel_stats, industry_count):
    """生成 C154 最优组合选股结果的交互式 HTML 报告"""
    import os
    html_dir = os.path.join('html', end_date)
    os.makedirs(html_dir, exist_ok=True)

    if result.empty:
        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>C154 最优组合选股 - {end_date}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>body {{ font-family: Inter, sans-serif; }}</style>
</head>
<body class="bg-gray-50 min-h-screen">
    <div class="max-w-5xl mx-auto px-4 py-8">
        <div class="bg-white rounded-xl shadow-sm p-6 mb-6">
            <h1 class="text-2xl font-bold text-orange-600">🏆 C154 最优组合选股</h1>
            <p class="text-gray-500 mt-1">日期: {end_date} | 无符合条件的股票</p>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-6 text-center text-gray-500">
            今天没有股票同时满足 C154 的全部6个条件。
        </div>
    </div>
</body>
</html>"""
        filename = os.path.join(html_dir, f"c154_selection_{end_date}.html")
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"  C154 报告(空): {filename}")
        return

    table_data = []
    stock_charts = {}
    stock_details = {}
    for _, row in result.iterrows():
        ts_code = row['ts_code']
        name = row['name']
        stock_history = df[df['ts_code'] == ts_code].copy()
        chart_data = generate_stock_charts(stock_history, ts_code, name)
        if chart_data:
            stock_charts[ts_code] = chart_data
        bvk_count = 0
        if 'bottom_violent_k' in df.columns:
            bvk_count = df[(df['ts_code'] == ts_code) & df['bottom_violent_k']].shape[0]
        am_count = 0
        if 'has_am_in_period' in df.columns:
            am_count = df[(df['ts_code'] == ts_code) & df['has_am_in_period']].shape[0]
        j_val = float(row['kdj_qfq'])
        j_class = 'text-red-600' if j_val < 0 else 'text-orange-600' if j_val < 5 else 'text-green-600'
        pct_val = float(row['pct_chg'])
        pct_class = 'text-red-600' if pct_val > 0 else 'text-green-600'
        ma60_val = float(row['ma_qfq_60']) if pd.notna(row.get('ma_qfq_60')) else 0
        ma60_up = bool(row.get('ma60_upward', False))
        close_val = float(row['close_qfq'])
        above_ma60 = close_val >= ma60_val if ma60_val > 0 else False
        cycle_data = df[(df['ts_code'] == ts_code) & (df['trade_date'] <= row['trade_date'])]
        cycle_max = cycle_data['amount'].max() if not cycle_data.empty else 0
        is_lowest_volume = bool(row['amount'] <= cycle_max * 0.30) if cycle_max > 0 else False
        is_amount_top = bool(row.get('is_amount_top30', False))
        has_step = bool(row.get('first_j13_step', False))
        has_bvk = bool(row.get('has_bottom_violent_k', False))
        has_am = bool(row.get('has_am_in_period', False))
        no_dist = not bool(row.get('has_distribution_signal', False)) and not bool(row.get('has_distribution_signal_v2', False)) and not bool(row.get('has_distribution_signal_v3', False))
        dist_signals = []
        if row.get('has_distribution_signal'):
            dist_signals.append('V1')
        if row.get('has_distribution_signal_v2'):
            dist_signals.append('V2')
        if row.get('has_distribution_signal_v3'):
            dist_signals.append('V3')
        score_val = float(row.get('score', 0)) if pd.notna(row.get('score')) else 0
        score_level = str(row.get('score_level', 'D')) if pd.notna(row.get('score_level')) else 'D'
        level_colors = {'A': 'bg-red-100 text-red-700', 'B': 'bg-orange-100 text-orange-700',
                        'C': 'bg-yellow-100 text-yellow-700', 'D': 'bg-gray-100 text-gray-500'}
        score_color = 'text-red-600' if score_val >= 80 else 'text-orange-600' if score_val >= 65 else 'text-yellow-600' if score_val >= 50 else 'text-gray-500'

        stock_details[ts_code] = {
            'name': name,
            'industry': row.get('industry_name', '未知'),
            'close': f"{close_val:.2f}",
            'pct': f"{pct_val:.2f}%",
            'ma60': f"{ma60_val:.2f}",
            'above_ma60': above_ma60,
            'ma60_up': ma60_up,
            'j_val': f"{j_val:.2f}",
            'macd_dif': f"{row.get('macd_dif_qfq', 0):.4f}",
            'amount': f"{row['amount']/10000:.2f}万",
            'is_lowest_volume': is_lowest_volume,
            'is_amount_top': is_amount_top,
            'has_step': has_step,
            'no_dist': no_dist,
            'dist_signals': ','.join(dist_signals) if dist_signals else '无',
            'has_bvk': has_bvk,
            'bvk_count': bvk_count,
            'has_am': has_am,
            'am_count': am_count,
            'score': f"{score_val:.0f}",
            'score_level': score_level,
            'score_color': score_color,
            'level_color': level_colors.get(score_level, 'bg-gray-100 text-gray-500'),
            'turnover': f"{row.get('turnover_rate', 0):.1f}%" if pd.notna(row.get('turnover_rate')) else '-',
            'volume_ratio': f"{row.get('volume_ratio', 0):.2f}" if pd.notna(row.get('volume_ratio')) else '-',
            'body_ratio': f"{row.get('body_ratio', 0):.2f}" if pd.notna(row.get('body_ratio')) else '-',
            'total_mv': f"{row.get('total_mv', 0)/10000:.0f}万" if pd.notna(row.get('total_mv')) else '-',
        }
        table_data.append({
            '代码': ts_code,
            '名称': name,
            '行业': row.get('industry_name', '未知'),
            '收盘价': f"{close_val:.2f}",
            '涨跌幅': f"{pct_val:.2f}%",
            '涨跌幅样式': pct_class,
            'J值': f"{j_val:.2f}",
            'J值样式': j_class,
            '评分': f"{score_val:.0f}",
            '评分样式': score_color,
            '等级': score_level,
            '等级样式': level_colors.get(score_level, 'bg-gray-100 text-gray-500'),
            'MACD-DIF': f"{row.get('macd_dif_qfq', 0):.4f}",
            '成交额万': f"{row['amount']/10000:.2f}",
            '暴力K次数': bvk_count,
            '异动次数': am_count,
        })

    csv_data = pd.DataFrame(table_data).to_csv(index=False, encoding='utf-8-sig')
    industry_items = ''.join([
        f'<div class="flex items-center justify-between p-3 bg-gray-50 rounded-lg">'
        f'<span class="text-sm text-gray-600">{ind}</span>'
        f'<span class="text-sm font-bold text-orange-600">{cnt}只</span></div>'
        for ind, cnt in industry_count.items()
    ])
    charts_json = json.dumps(stock_charts, ensure_ascii=False, default=str)
    details_json = json.dumps(stock_details, ensure_ascii=False, default=str)
    sel_codes = json.dumps([row['ts_code'].replace('.SH', '').replace('.SZ', '') for _, row in result.iterrows()], ensure_ascii=False)

    stage_labels = [
        ('全市场', '全市场（250天内）'),
        ('阶梯放量+J13', '阶梯放量+J13低吸'),
        ('不跌', '+不跌'),
        ('无出货', '+无出货'),
        ('底部暴力K', '+底部暴力K'),
        ('J<5', '+J<5'),
        ('异动', '+异动'),
        ('最终', '最终满足条件（当日）'),
    ]
    funnel_rows = ''.join([
        f'<div class="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">'
        f'<span class="text-sm text-gray-600 w-48">{label}</span>'
        f'<span class="font-bold text-orange-600 text-lg">{funnel_stats.get(key, "-")} 只</span></div>'
        for key, label in stage_labels
    ])

    table_rows = ''.join([
        f'<tr class="hover:bg-gray-50 cursor-pointer" onclick="showChart(&apos;{r["代码"]}&apos;, &apos;{r["名称"]}&apos;)">'
        f'<td class="px-6 py-4 font-medium text-blue-600 hover:text-blue-800">{r["代码"]}</td>'
        f'<td class="px-6 py-4 font-medium">{r["名称"]}</td>'
        f'<td class="px-6 py-4"><span class="px-2 py-1 bg-blue-100 text-blue-800 rounded-full text-xs">{r["行业"]}</span></td>'
        f'<td class="px-6 py-4">{r["收盘价"]}</td>'
        f'<td class="px-6 py-4 font-semibold {r["涨跌幅样式"]}">{r["涨跌幅"]}</td>'
        f'<td class="px-6 py-4 font-bold {r["J值样式"]}">{r["J值"]}</td>'
        f'<td class="px-6 py-4 font-bold {r["评分样式"]}">{r["评分"]}</td>'
        f'<td class="px-6 py-4"><span class="px-2 py-1 {r["等级样式"]} rounded-full text-xs font-bold">{r["等级"]}</span></td>'
        f'<td class="px-6 py-4">{r["MACD-DIF"]}</td>'
        f'<td class="px-6 py-4">{r["成交额万"]}</td>'
        f'<td class="px-6 py-4">{r["暴力K次数"]}次</td>'
        f'<td class="px-6 py-4">{r["异动次数"]}次</td></tr>'
        for r in table_data
    ])

    avg_j = result["kdj_qfq"].mean()
    avg_pct = result["pct_chg"].mean()
    n = len(result)
    n_ind = len(industry_count)

    has_score = 'score' in result.columns and 'score_level' in result.columns
    if has_score:
        level_counts = result['score_level'].value_counts().to_dict()
        n_a = level_counts.get('A', 0)
        n_b = level_counts.get('B', 0)
        n_ab = n_a + n_b
        score_cards = f'''
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">B级以上(推荐)</div><div class="text-2xl font-bold text-red-600">{n_ab}只</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">A级</div><div class="text-2xl font-bold text-red-600">{n_a}只</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">B级</div><div class="text-2xl font-bold text-orange-600">{n_b}只</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">C级</div><div class="text-2xl font-bold text-yellow-600">{level_counts.get("C", 0)}只</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">D级</div><div class="text-2xl font-bold text-gray-400">{level_counts.get("D", 0)}只</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">平均J值</div><div class="text-2xl font-bold text-red-600">{avg_j:.2f}</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">平均涨幅</div><div class="text-2xl font-bold text-blue-600">{avg_pct:.2f}%</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">涉及行业</div><div class="text-2xl font-bold text-green-600">{n_ind}</div></div>
        '''
    else:
        score_cards = f'''
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">选股总数</div><div class="text-2xl font-bold text-orange-600">{n}</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">涉及行业</div><div class="text-2xl font-bold text-green-600">{n_ind}</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">平均J值</div><div class="text-2xl font-bold text-red-600">{avg_j:.2f}</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">平均涨幅</div><div class="text-2xl font-bold text-blue-600">{avg_pct:.2f}%</div></div>
        '''

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>C154 最优组合选股 - {end_date}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: Inter, sans-serif; }}
        .sortable th {{ cursor: pointer; user-select: none; }}
        .sortable th:hover {{ background-color: #f3f4f6; }}
        .sort-asc::after {{ content: " ▲"; }}
        .sort-desc::after {{ content: " ▼"; }}
        .chart-container {{ height: 400px; }}
        .modal {{ display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); }}
        .modal-content {{ background-color: #fefefe; margin: 2% auto; padding: 20px; border-radius: 12px; width: 90%; max-width: 1200px; max-height: 90vh; overflow-y: auto; }}
        .close {{ color: #aaa; float: right; font-size: 28px; font-weight: bold; cursor: pointer; }}
        .close:hover {{ color: black; }}
    </style>
</head>
<body class="bg-gray-50 min-h-screen">
    <div class="max-w-6xl mx-auto px-4 py-8">
        <div class="bg-white rounded-xl shadow-sm p-6 mb-6">
            <div class="flex justify-between items-center flex-wrap gap-4">
                <div>
                    <h1 class="text-2xl font-bold text-orange-600">🏆 C154 最优组合选股</h1>
                    <p class="text-gray-500 mt-1">日期: {end_date} | 共选出 <span class="text-orange-600 font-bold text-xl">{n}</span> 只股票</p>
                </div>
                <div class="flex gap-3">
                    <button onclick="downloadCSV()" class="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                        下载 CSV
                    </button>
                    <button onclick="downloadSEL()" class="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
                        导入同花顺 (.sel)
                    </button>
                </div>
            </div>
        </div>

        <div class="bg-gradient-to-r from-orange-50 to-amber-50 border border-orange-200 rounded-xl p-5 mb-6">
            <h2 class="text-sm font-semibold text-orange-700 mb-3">📋 C154 条件组合（全部 AND）</h2>
            <div class="grid grid-cols-2 md:grid-cols-3 gap-2">
                <span class="px-2 py-1 bg-red-100 text-red-700 rounded text-xs font-medium">1. 阶梯放量+J13低吸</span>
                <span class="px-2 py-1 bg-blue-100 text-blue-700 rounded text-xs font-medium">2. 不跌 (pct_chg>=0)</span>
                <span class="px-2 py-1 bg-green-100 text-green-700 rounded text-xs font-medium">3. 无出货 (V1/V2/V3)</span>
                <span class="px-2 py-1 bg-purple-100 text-purple-700 rounded text-xs font-medium">4. 底部暴力K</span>
                <span class="px-2 py-1 bg-yellow-100 text-yellow-700 rounded text-xs font-medium">5. J&lt;5</span>
                <span class="px-2 py-1 bg-pink-100 text-pink-700 rounded text-xs font-medium">6. 周期内异动</span>
            </div>
            <p class="text-xs text-gray-500 mt-3">回测（250交易日）：样本717，平均涨幅1.66%，胜率60.0%，盈亏比1.72</p>
        </div>

        <div class="bg-white rounded-xl shadow-sm p-5 mb-6">
            <h2 class="text-lg font-semibold text-gray-800 mb-4">🔽 选股漏斗</h2>
            <div class="space-y-2">{funnel_rows}</div>
        </div>

        <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4 mb-6">
            {score_cards}
        </div>

        <div class="bg-white rounded-xl shadow-sm overflow-hidden mb-6">
            <div class="px-6 py-4 border-b border-gray-200"><h2 class="text-lg font-semibold text-gray-900">📋 选股明细（点击代码查看图表）</h2></div>
            <div class="overflow-x-auto">
                <table class="w-full text-sm text-left sortable" id="stockTable">
                    <thead class="text-xs text-gray-700 uppercase bg-gray-50">
                        <tr>
                            <th class="px-6 py-3" onclick="sortTable(0)">代码</th>
                            <th class="px-6 py-3" onclick="sortTable(1)">名称</th>
                            <th class="px-6 py-3" onclick="sortTable(2)">行业</th>
                            <th class="px-6 py-3" onclick="sortTable(3)">收盘价</th>
                            <th class="px-6 py-3" onclick="sortTable(4)">涨跌幅</th>
                            <th class="px-6 py-3" onclick="sortTable(5)">J值</th>
                            <th class="px-6 py-3" onclick="sortTable(6)">评分</th>
                            <th class="px-6 py-3" onclick="sortTable(7)">等级</th>
                            <th class="px-6 py-3" onclick="sortTable(8)">MACD-DIF</th>
                            <th class="px-6 py-3" onclick="sortTable(9)">成交额(万)</th>
                            <th class="px-6 py-3" onclick="sortTable(10)">暴力K次数</th>
                            <th class="px-6 py-3" onclick="sortTable(11)">异动次数</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-200">{table_rows}</tbody>
                </table>
            </div>
        </div>

        <div class="bg-white rounded-xl shadow-sm p-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-4">📊 行业分布</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">{industry_items}</div>
        </div>
    </div>

    <div id="chartModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal()">&times;</span>
            <h2 id="modalTitle" class="text-xl font-bold mb-4"></h2>
            <div id="stockInfoPanel" class="bg-gray-50 rounded-lg p-4 mb-4">
                <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3" id="stockInfoGrid"></div>
                <div class="grid grid-cols-3 md:grid-cols-6 gap-2 mt-3" id="stockSignalGrid"></div>
            </div>
            <div id="klineChart" class="chart-container mb-4"></div>
            <div id="volumeChart" class="chart-container mb-4" style="height:150px;"></div>
            <div id="kdjChart" class="chart-container" style="height:200px;"></div>
        </div>
    </div>

    <script>
    const stockCharts = {charts_json};
    const stockDetails = {details_json};
    const stockCSV = `{csv_data}`;
    const selCodes = {sel_codes};

    function downloadSEL() {{
        if(selCodes.length===0){{alert('无股票数据');return;}}
        const buf = new ArrayBuffer(2 + selCodes.length * 8);
        const view = new DataView(buf);
        view.setUint16(0, selCodes.length, true);
        let offset = 2;
        selCodes.forEach(code => {{
            const market = (code[0]==='6'||code[0]==='9') ? 0x11 : 0x21;
            view.setUint8(offset, 0x07);
            view.setUint8(offset + 1, market);
            for(let i = 0; i < 6; i++) {{
                view.setUint8(offset + 2 + i, code.charCodeAt(i));
            }}
            offset += 8;
        }});
        const blob = new Blob([buf], {{type: 'application/octet-stream'}});
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'c154_selection_{end_date}.sel';
        link.click();
    }}

    function downloadCSV() {{
        const blob = new Blob(['\\uFEFF' + stockCSV], {{ type: 'text/csv;charset=utf-8;' }});
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'c154_selection_{end_date}.csv';
        link.click();
    }}

    let sortDir = {{}};
    function sortTable(col) {{
        const tbl = document.getElementById('stockTable');
        const rows = Array.from(tbl.querySelector('tbody').querySelectorAll('tr'));
        sortDir[col] = !sortDir[col];
        tbl.querySelectorAll('th').forEach((th,i) => {{ th.classList.remove('sort-asc','sort-desc'); if(i===col) th.classList.add(sortDir[col]?'sort-desc':'sort-asc'); }});
        rows.sort((a,b) => {{
            const av=a.cells[col].textContent.trim(), bv=b.cells[col].textContent.trim();
            const an=parseFloat(av), bn=parseFloat(bv);
            if(!isNaN(an)&&!isNaN(bn)) return sortDir[col]?bn-an:an-bn;
            return sortDir[col]?bv.localeCompare(av):av.localeCompare(bv);
        }});
        rows.forEach(r => tbl.querySelector('tbody').appendChild(r));
    }}

    let kc,vc,kdjc;
    function showChart(ts,nm) {{
        const d=stockCharts[ts]; if(!d){{alert('暂无图表');return;}}
        document.getElementById('modalTitle').textContent=nm+' ('+ts+') - C154 技术分析';
        document.getElementById('chartModal').style.display='block';
        const info=stockDetails[ts];
        if(info){{
            document.getElementById('stockInfoGrid').innerHTML=`<div class="bg-white rounded p-2"><div class="text-xs text-gray-500">行业</div><div class="font-bold text-blue-600">${{info.industry}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">收盘价</div><div class="font-bold">${{info.close}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">涨跌幅</div><div class="font-bold ${{info.pct.startsWith('-')?'text-green-600':'text-red-600'}}">${{info.pct}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">60日线</div><div class="font-bold">${{info.ma60}} ${{info.above_ma60?'📈':'📉'}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">J值</div><div class="font-bold ${{parseFloat(info.j_val)<0?'text-red-600':'text-orange-600'}}">${{info.j_val}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">评分</div><div class="font-bold ${{info.score_color}}">${{info.score}}分</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">等级</div><div class="font-bold"><span class="px-2 py-1 ${{info.level_color}} rounded-full text-xs">${{info.score_level}}</span></div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">MACD-DIF</div><div class="font-bold">${{info.macd_dif}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">成交额</div><div class="font-bold">${{info.amount}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">60日线趋势</div><div class="font-bold">${{info.ma60_up?'<span class="text-green-600">上升</span>':'<span class="text-red-600">下降</span>'}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">换手率</div><div class="font-bold">${{info.turnover}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">量比</div><div class="font-bold">${{info.volume_ratio}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">实体比</div><div class="font-bold">${{info.body_ratio}}</div></div><div class="bg-white rounded p-2"><div class="text-xs text-gray-500">总市值</div><div class="font-bold">${{info.total_mv}}</div></div>`;
            document.getElementById('stockSignalGrid').innerHTML=`<div class="text-center p-1 rounded text-xs ${{info.has_step?'bg-green-100 text-green-700':'bg-gray-100 text-gray-400'}}">${{info.has_step?'✅ J13阶梯':'❌ J13阶梯'}}</div><div class="text-center p-1 rounded text-xs ${{info.no_dist?'bg-green-100 text-green-700':'bg-red-100 text-red-700'}}">${{info.no_dist?'✅ 无出货':'⚠️ '+info.dist_signals}}</div><div class="text-center p-1 rounded text-xs ${{info.has_bvk?'bg-green-100 text-green-700':'bg-gray-100 text-gray-400'}}">${{info.has_bvk?'✅ 暴力K('+info.bvk_count+'次)':'❌ 暴力K'}}</div><div class="text-center p-1 rounded text-xs ${{info.has_am?'bg-green-100 text-green-700':'bg-gray-100 text-gray-400'}}">${{info.has_am?'✅ 异动('+info.am_count+'次)':'❌ 异动'}}</div><div class="text-center p-1 rounded text-xs ${{info.is_lowest_volume?'bg-green-100 text-green-700':'bg-gray-100 text-gray-400'}}">${{info.is_lowest_volume?'✅ 回调最低量':'❌ 回调最低量'}}</div><div class="text-center p-1 rounded text-xs ${{info.is_amount_top?'bg-green-100 text-green-700':'bg-gray-100 text-gray-400'}}">${{info.is_amount_top?'✅ 成交额前60%':'❌ 成交额前60%'}}</div>`;
        }}
        if(kc)kc.dispose(); if(vc)vc.dispose(); if(kdjc)kdjc.dispose();
        kc=echarts.init(document.getElementById('klineChart'));
        const cd=d.candlestick.map(c=>({{value:[c[1],c[2],c[3],c[4]],itemStyle:{{color:c[2]>=c[1]?'#ef4444':'#22c55e',color0:c[2]>=c[1]?'#ef4444':'#22c55e',borderColor:c[2]>=c[1]?'#ef4444':'#22c55e',borderColor0:c[2]>=c[1]?'#ef4444':'#22c55e'}}}}));
        kc.setOption({{
            title:{{text:'K线 + MA60 + 知行多空',left:'center',textStyle:{{fontSize:14}}}},
            tooltip:{{trigger:'axis',axisPointer:{{type:'cross'}},
                formatter:function(params){{
                    let r=params[0].axisValue+'<br/>';
                    params.forEach(p=>{{
                        if(p.seriesType==='candlestick'){{
                            const i=p.dataIndex;const ch=d.price_change[i];const cc=ch>=0?'#ef4444':'#22c55e';const cs=ch>=0?'+':'';
                            const raw=d.candlestick[i];
                            r+='涨跌幅: <span style="color:'+cc+'">'+cs+ch+'%</span><br/>开盘: '+raw[1]+' 收盘: '+raw[2]+'<br/>最高: '+raw[3]+' 最低: '+raw[4]+'<br/>';
                        }} else if(p.seriesName==='MA60'){{r+='MA60: '+p.data+'<br/>';
                        }} else if(p.seriesName==='知行多空'){{r+='知行多空: '+p.data+'<br/>';
                        }} else if(p.seriesName==='知行中'){{r+='知行中: '+p.data+'<br/>';}}
                    }});
                    return r;
                }}
            }},
            legend:{{data:['K线','MA60','知行多空','知行中'],bottom:0}},
            grid:{{left:'8%',right:'8%',top:'40px',bottom:'80px'}},
            xAxis:{{type:'category',data:d.dates}},
            yAxis:{{type:'value',scale:true}},
            dataZoom:[{{type:'inside'}},{{type:'slider',start:70,end:100}}],
            series:[
                {{type:'candlestick',data:cd,name:'K线'}},
                {{type:'line',data:d.ma60,name:'MA60',smooth:true,lineStyle:{{color:'#f59e0b',width:2}},symbol:'none'}},
                {{type:'line',data:d.zhixing_duokong,name:'知行多空',smooth:true,lineStyle:{{color:'#3b82f6',width:1,type:'dashed'}},symbol:'none'}},
                {{type:'line',data:d.zhixing_mid_duokong,name:'知行中',smooth:true,lineStyle:{{color:'#8b5cf6',width:1,type:'dotted'}},symbol:'none'}}
            ]
        }});
        vc=echarts.init(document.getElementById('volumeChart'));
        const vd=d.volume.map(v=>({{value:v.value,itemStyle:v.itemStyle}}));
        vc.setOption({{
            title:{{text:'成交额（万元）- 黄色=倍量上涨 紫色=倍量下跌',left:'center',textStyle:{{fontSize:12}}}},
            tooltip:{{trigger:'axis',
                formatter:function(params){{
                    const i=params[0].dataIndex;const vol=d.volume[i];
                    return params[0].axisValue+'<br/>成交额: '+params[0].value.toFixed(2)+' 万元<br/>是否倍量: '+(vol.is_double?'是':'否');
                }}
            }},
            grid:{{left:'8%',right:'8%',top:'40px',bottom:'30px'}},
            xAxis:{{type:'category',data:d.dates,show:false}},
            yAxis:{{type:'value'}},
            series:[{{type:'bar',data:vd}}]
        }});
        kdjc=echarts.init(document.getElementById('kdjChart'));
        kdjc.setOption({{
            title:{{text:'KDJ指标',left:'center',textStyle:{{fontSize:14}}}},
            tooltip:{{trigger:'axis',
                formatter:function(params){{
                    let r=params[0].axisValue+'<br/>';params.forEach(p=>{{r+=p.seriesName+': '+p.data.toFixed(2)+'<br/>';}});
                    return r;
                }}
            }},
            legend:{{data:['K','D','J'],bottom:0}},
            grid:{{left:'10%',right:'10%',top:'40px',bottom:'40px'}},
            xAxis:{{type:'category',data:d.dates}},
            yAxis:{{type:'value',min:0,max:100}},
            series:[
                {{type:'line',data:d.kdj_k,name:'K',smooth:true,lineStyle:{{color:'#3b82f6'}}}},
                {{type:'line',data:d.kdj_d,name:'D',smooth:true,lineStyle:{{color:'#f59e0b'}}}},
                {{type:'line',data:d.kdj_j,name:'J',smooth:true,lineStyle:{{color:'#ef4444'}}}}
            ]
        }});
        window.addEventListener('resize',()=>{{kc.resize();vc.resize();kdjc.resize();}});
    }}
    function closeModal(){{document.getElementById('chartModal').style.display='none';}}
    window.onclick=function(e){{if(e.target==document.getElementById('chartModal'))document.getElementById('chartModal').style.display='none';}}
    </script>
</body>
</html>"""

    filename = os.path.join(html_dir, f"c154_selection_{end_date}.html")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"  C154 HTML 报告: {filename}")


