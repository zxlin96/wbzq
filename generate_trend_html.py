#!/usr/bin/env python3
"""
趋势图 HTML 生成器
生成美观的交互式趋势图页面
"""

import pandas as pd
import json
import os
from pathlib import Path


def generate_industry_trend_html(trend_df, end_date, top_n=10):
    """生成行业总成交额趋势图 HTML"""
    
    # 获取 Top N 行业
    top_industries = trend_df.groupby('industry')['total_amount'].mean().nlargest(top_n).index.tolist()
    trend_df = trend_df[trend_df['industry'].isin(top_industries)]
    
    # 准备数据
    dates = trend_df['date'].unique().tolist()
    industries = top_industries
    
    # 为每个行业准备数据
    series_data = []
    colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', 
              '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc', '#ff9f7f']
    
    for i, industry in enumerate(industries):
        industry_data = trend_df[trend_df['industry'] == industry]
        data_points = []
        for date in dates:
            row = industry_data[industry_data['date'] == date]
            if not row.empty:
                data_points.append(round(row['total_amount'].values[0], 2))
            else:
                data_points.append(None)
        
        series_data.append({
            'name': industry,
            'type': 'line',
            'data': data_points,
            'smooth': True,
            'symbol': 'circle',
            'symbolSize': 6,
            'lineStyle': {'width': 2},
            'itemStyle': {'color': colors[i % len(colors)]},
            'emphasis': {'focus': 'series'}
        })
    
    # 计算平均值
    avg_amount = trend_df['total_amount'].mean()
    
    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>行业总成交额趋势 - {end_date}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }}
        .glass {{ background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.1); }}
    </style>
</head>
<body class="p-4 md:p-8">
    <div class="max-w-7xl mx-auto">
        <!-- 标题 -->
        <div class="glass p-6 mb-6">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h1 class="text-2xl md:text-3xl font-bold text-gray-800">📊 行业总成交额趋势</h1>
                    <p class="text-gray-500 mt-1">日期: {end_date} | Top {top_n} 行业</p>
                </div>
                <div class="flex gap-3">
                    <a href="../../index.html" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                        ← 返回首页
                    </a>
                    <a href="stock_selection_{end_date}.html" class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors">
                        查看选股 →
                    </a>
                </div>
            </div>
        </div>
        
        <!-- 统计卡片 -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div class="glass p-4">
                <div class="text-sm text-gray-500">平均成交额</div>
                <div class="text-2xl font-bold text-blue-600">{avg_amount:,.2f} 万</div>
            </div>
            <div class="glass p-4">
                <div class="text-sm text-gray-500">最高成交额行业</div>
                <div class="text-lg font-bold text-green-600">{trend_df.groupby('industry')['total_amount'].mean().idxmax()}</div>
            </div>
            <div class="glass p-4">
                <div class="text-sm text-gray-500">数据天数</div>
                <div class="text-2xl font-bold text-purple-600">{len(dates)} 天</div>
            </div>
        </div>
        
        <!-- 图表 -->
        <div class="glass p-6">
            <div id="chart" style="width: 100%; height: 600px;"></div>
        </div>
        
        <!-- 数据表格 -->
        <div class="glass p-6 mt-6">
            <h2 class="text-lg font-semibold text-gray-800 mb-4">📋 行业成交额详情</h2>
            <div class="overflow-x-auto">
                <table class="w-full text-sm text-left">
                    <thead class="text-xs text-gray-700 uppercase bg-gray-100">
                        <tr>
                            <th class="px-4 py-3">行业</th>
                            <th class="px-4 py-3 text-right">平均成交额(万)</th>
                            <th class="px-4 py-3 text-right">最高成交额(万)</th>
                            <th class="px-4 py-3 text-right">最低成交额(万)</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-200">
                        {generate_industry_table_rows(trend_df)}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    
    <script>
        const chart = echarts.init(document.getElementById('chart'));
        
        const option = {{
            title: {{
                text: '行业总成交额趋势（Top {top_n}）',
                left: 'center',
                textStyle: {{ fontSize: 18, fontWeight: 'bold' }}
            }},
            tooltip: {{
                trigger: 'axis',
                axisPointer: {{ type: 'cross' }},
                backgroundColor: 'rgba(255,255,255,0.95)',
                borderColor: '#ccc',
                borderWidth: 1,
                textStyle: {{ color: '#333' }}
            }},
            legend: {{
                data: {json.dumps(industries, ensure_ascii=False)},
                top: 40,
                type: 'scroll'
            }},
            grid: {{
                left: '3%',
                right: '4%',
                bottom: '15%',
                top: '20%',
                containLabel: true
            }},
            toolbox: {{
                feature: {{
                    saveAsImage: {{ title: '保存图片' }},
                    dataZoom: {{ title: {{ zoom: '区域缩放', back: '还原' }} }},
                    restore: {{ title: '还原' }}
                }}
            }},
            dataZoom: [
                {{ type: 'inside', start: 0, end: 100 }},
                {{ type: 'slider', start: 0, end: 100, bottom: 10 }}
            ],
            xAxis: {{
                type: 'category',
                boundaryGap: false,
                data: {json.dumps([str(d)[:10] for d in dates], ensure_ascii=False)},
                axisLabel: {{ rotate: 45 }}
            }},
            yAxis: {{
                type: 'value',
                name: '成交额（万元）',
                axisLabel: {{
                    formatter: function(value) {{
                        return (value / 10000).toFixed(1) + '亿';
                    }}
                }}
            }},
            series: {json.dumps(series_data, ensure_ascii=False)}
        }};
        
        chart.setOption(option);
        
        window.addEventListener('resize', () => chart.resize());
    </script>
</body>
</html>'''
    
    return html_content


def generate_j13_trend_html(daily_counts, end_date):
    """生成 J13 每日数量趋势图 HTML"""
    
    # 准备数据
    dates = daily_counts['trade_date'].astype(str).tolist()
    counts = daily_counts['count'].tolist()
    
    # 计算统计值
    avg_count = daily_counts['count'].mean()
    max_count = daily_counts['count'].max()
    max_date = daily_counts.loc[daily_counts['count'].idxmax(), 'trade_date']
    min_count = daily_counts['count'].min()
    percentile_90 = daily_counts['count'].quantile(0.9)  # 90% 分位数
    
    # 标记超过平均值的点
    mark_points = []
    for i, count in enumerate(counts):
        if count > avg_count * 1.5:  # 超过平均值50%
            mark_points.append({
                'coord': [i, count],
                'value': count,
                'itemStyle': {'color': '#ee6666'}
            })
    
    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>J13 每日数量趋势 - {end_date}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); min-height: 100vh; }}
        .glass {{ background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.1); }}
        .stat-card {{ transition: transform 0.2s; }}
        .stat-card:hover {{ transform: translateY(-2px); }}
    </style>
</head>
<body class="p-4 md:p-8">
    <div class="max-w-7xl mx-auto">
        <!-- 标题 -->
        <div class="glass p-6 mb-6">
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h1 class="text-2xl md:text-3xl font-bold text-gray-800">📈 J13 每日数量趋势</h1>
                    <p class="text-gray-500 mt-1">日期: {end_date} | KDJ J值小于13的股票数量</p>
                </div>
                <div class="flex gap-3">
                    <a href="../../index.html" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                        ← 返回首页
                    </a>
                    <a href="stock_selection_{end_date}.html" class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors">
                        查看选股 →
                    </a>
                </div>
            </div>
        </div>
        
        <!-- 统计卡片 -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div class="glass p-4 stat-card">
                <div class="text-sm text-gray-500">平均数量</div>
                <div class="text-2xl font-bold text-blue-600">{avg_count:.1f} 只</div>
            </div>
            <div class="glass p-4 stat-card">
                <div class="text-sm text-gray-500">最高数量</div>
                <div class="text-2xl font-bold text-green-600">{max_count} 只</div>
                <div class="text-xs text-gray-400">{max_date}</div>
            </div>
            <div class="glass p-4 stat-card">
                <div class="text-sm text-gray-500">最低数量</div>
                <div class="text-2xl font-bold text-orange-600">{min_count} 只</div>
            </div>
            <div class="glass p-4 stat-card">
                <div class="text-sm text-gray-500">数据天数</div>
                <div class="text-2xl font-bold text-purple-600">{len(dates)} 天</div>
            </div>
            <div class="glass p-4 stat-card">
                <div class="text-sm text-gray-500">90%分位数</div>
                <div class="text-2xl font-bold text-red-600">{percentile_90:.1f} 只</div>
            </div>
        </div>
        
        <!-- 图表 -->
        <div class="glass p-6">
            <div id="chart" style="width: 100%; height: 500px;"></div>
        </div>
        
        <!-- 说明 -->
        <div class="glass p-6 mt-6">
            <h2 class="text-lg font-semibold text-gray-800 mb-3">ℹ️ 说明</h2>
            <div class="text-gray-600 space-y-2">
                <p>• <b>J13</b>：KDJ指标的J值小于13，表示股票处于超卖状态</p>
                <p>• <b>红色标记点</b>：数量超过平均值50%的交易日，可能存在批量买入机会</p>
                <p>• 数据每日自动更新，基于当日收盘后的KDJ指标计算</p>
            </div>
        </div>
    </div>
    
    <script>
        const chart = echarts.init(document.getElementById('chart'));
        
        const option = {{
            title: {{
                text: 'J13 每日出现总数趋势',
                subtext: 'KDJ J值 < 13 的股票数量',
                left: 'center',
                textStyle: {{ fontSize: 18, fontWeight: 'bold' }}
            }},
            tooltip: {{
                trigger: 'axis',
                backgroundColor: 'rgba(255,255,255,0.95)',
                borderColor: '#ccc',
                borderWidth: 1,
                formatter: function(params) {{
                    const date = params[0].axisValue;
                    const count = params[0].value;
                    return `<b>${{date}}</b><br/>J13数量: <b>${{count}}</b> 只`;
                }}
            }},
            grid: {{
                left: '3%',
                right: '4%',
                bottom: '15%',
                top: '20%',
                containLabel: true
            }},
            toolbox: {{
                feature: {{
                    saveAsImage: {{ title: '保存图片' }},
                    dataZoom: {{ title: {{ zoom: '区域缩放', back: '还原' }} }},
                    restore: {{ title: '还原' }}
                }}
            }},
            dataZoom: [
                {{ type: 'inside', start: 0, end: 100 }},
                {{ type: 'slider', start: 0, end: 100, bottom: 10 }}
            ],
            xAxis: {{
                type: 'category',
                boundaryGap: false,
                data: {json.dumps(dates, ensure_ascii=False)},
                axisLabel: {{ rotate: 45 }}
            }},
            yAxis: {{
                type: 'value',
                name: '数量（只）',
                min: 0
            }},
            series: [{{
                name: 'J13数量',
                type: 'line',
                data: {json.dumps(counts, ensure_ascii=False)},
                smooth: true,
                symbol: 'circle',
                symbolSize: 8,
                lineStyle: {{ width: 3, color: '#5470c6' }},
                itemStyle: {{ color: '#5470c6' }},
                areaStyle: {{
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        {{ offset: 0, color: 'rgba(84, 112, 198, 0.3)' }},
                        {{ offset: 1, color: 'rgba(84, 112, 198, 0.05)' }}
                    ])
                }},
                markLine: {{
                    data: [
                        {{
                            type: 'average',
                            name: '平均值',
                            lineStyle: {{ color: '#91cc75', type: 'dashed', width: 2 }}
                        }},
                        {{
                            yAxis: {percentile_90},
                            name: '90%分位',
                            lineStyle: {{ color: '#ee6666', type: 'dashed', width: 2 }}
                        }}
                    ],
                    label: {{
                        formatter: function(params) {{
                            if (params.name === '90%分位') return '90%分位: ' + params.value + ' 只';
                            return '平均: ' + params.value + ' 只';
                        }},
                        position: 'end'
                    }}
                }},
                markPoint: {{
                    data: [
                        {{ type: 'max', name: '最大值', itemStyle: {{ color: '#ee6666' }} }},
                        {{ type: 'min', name: '最小值', itemStyle: {{ color: '#73c0de' }} }}
                    ]
                }}
            }}]
        }};
        
        chart.setOption(option);
        
        window.addEventListener('resize', () => chart.resize());
    </script>
</body>
</html>'''
    
    return html_content


def generate_industry_table_rows(trend_df):
    """生成行业统计表格行"""
    rows = []
    industry_stats = trend_df.groupby('industry').agg({
        'total_amount': ['mean', 'max', 'min']
    }).round(2)
    
    for industry in industry_stats.index:
        stats = industry_stats.loc[industry]
        rows.append(f'''
            <tr class="hover:bg-gray-50">
                <td class="px-4 py-3 font-medium">{industry}</td>
                <td class="px-4 py-3 text-right">{stats[('total_amount', 'mean')]:,.2f}</td>
                <td class="px-4 py-3 text-right">{stats[('total_amount', 'max')]:,.2f}</td>
                <td class="px-4 py-3 text-right">{stats[('total_amount', 'min')]:,.2f}</td>
            </tr>
        ''')
    
    return '\n'.join(rows)


if __name__ == '__main__':
    # 测试代码
    print("趋势图 HTML 生成器已加载")
