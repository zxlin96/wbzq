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
    
    # 查找所有选股报告文件
    stock_selection_files = list(Path('.').glob('stock_selection_*.html'))
    
    # 按日期排序（从文件名提取日期）
    stock_selection_files.sort(key=lambda x: x.name, reverse=True)
    
    # 检查其他趋势图文件是否存在
    has_industry_trend = Path('industry_total_amount_trend.html').exists()
    has_j13_trend = Path('first_j13_step_daily_count.html').exists()
    
    # 为每个选股报告创建记录
    for html_file in stock_selection_files:
        file_name = html_file.name
        
        # 提取日期
        date_str = file_name.replace('stock_selection_', '').replace('.html', '')
        
        reports.append({
            'date': date_str,
            'stockSelection': file_name,
            'industryTrend': 'industry_total_amount_trend.html' if has_industry_trend else None,
            'j13Trend': 'first_j13_step_daily_count.html' if has_j13_trend else None
        })
    
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
