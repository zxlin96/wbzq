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
                                result += `涨跌幅: <span style="color:${{changeColor}}">${{changeSymbol}}${{change}}%</span><br/>`;
                                result += `开盘: ${{param.data[1]}}<br/>`;
                                result += `收盘: ${{param.data[2]}}<br/>`;
                                result += `最高: ${{param.data[3]}}<br/>`;
                                result += `最低: ${{param.data[4]}}<br/>`;
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
    filename = f"stock_selection_{end_date}.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"\n📊 已生成交互式选股报告: {filename}")
    print(f"   包含功能: 表格排序、CSV下载、行业分布统计、个股K线/成交额/KDJ图表")


if __name__ == "__main__":
    # 测试代码
    print("股票HTML报告生成器模块")
