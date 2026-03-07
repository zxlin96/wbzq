#!/usr/bin/env python3
"""
生成 GitHub Pages 索引文件
"""

import os
import json
from datetime import datetime
from pathlib import Path


def generate_reports_json():
    """生成 reports.json 索引文件"""
    
    reports = []
    
    # 查找所有 HTML 报告文件
    html_files = []
    for pattern in ['stock_selection_*.html', 'industry_total_amount_trend.html', 
                   'kdj_qfq_trend.html', 'first_j13_step_daily_count.html']:
        html_files.extend(Path('.').glob(pattern))
    
    # 按日期排序
    html_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    
    # 提取日期并分组
    date_reports = {}
    
    for html_file in html_files:
        file_name = html_file.name
        
        # 提取日期
        date_str = None
        if 'stock_selection_' in file_name:
            date_str = file_name.replace('stock_selection_', '').replace('.html', '')
        elif 'industry_total_amount_trend' in file_name:
            continue  # 跳过趋势图，在下面单独处理
        elif 'kdj_qfq_trend' in file_name:
            continue
        elif 'first_j13_step_daily_count' in file_name:
            continue
        
        if date_str:
            if date_str not in date_reports:
                date_reports[date_str] = {
                    'date': date_str,
                    'stockSelection': f'stock_selection_{date_str}.html',
                    'industryTrend': 'industry_total_amount_trend.html',
                    'kdjTrend': 'kdj_qfq_trend.html',
                    'firstJ13Trend': 'first_j13_step_daily_count.html'
                }
    
    # 转换为列表
    reports = list(date_reports.values())
    
    # 统计信息
    total_stocks = 0
    if reports:
        try:
            latest_report = reports[0]
            stock_selection_file = latest_report['stockSelection']
            if os.path.exists(stock_selection_file):
                with open(stock_selection_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if '共选出' in content:
                        import re
                        match = re.search(r'共选出 (\d+) 只', content)
                        if match:
                            total_stocks = int(match.group(1))
        except:
            pass
    
    latest_date = reports[0]['date'] if reports else '-'
    
    data = {
        'lastUpdate': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'totalStocks': total_stocks,
        'totalReports': len(reports),
        'latestDate': latest_date,
        'reports': reports
    }
    
    # 写入 JSON 文件
    with open('reports.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 已生成索引文件: reports.json")
    print(f"   - 报告数量: {len(reports)}")
    print(f"   - 最新日期: {latest_date}")
    print(f"   - 选股总数: {total_stocks}")


if __name__ == '__main__':
    generate_reports_json()
