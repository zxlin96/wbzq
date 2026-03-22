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
    
    # 查找 html/ 目录下的所有日期目录
    html_base_dir = Path('html')
    if not html_base_dir.exists():
        print("⚠️  html/ 目录不存在，跳过生成索引")
        return
    
    # 获取所有日期目录并按日期排序
    date_dirs = sorted([d for d in html_base_dir.iterdir() if d.is_dir()], 
                       key=lambda x: x.name, reverse=True)
    
    for date_dir in date_dirs:
        date_str = date_dir.name
        
        # 检查该日期目录下的文件
        stock_selection_file = date_dir / f"stock_selection_{date_str}.html"
        industry_trend_file = date_dir / "industry_total_amount_trend.html"
        j13_trend_file = date_dir / "first_j13_step_daily_count.html"
        
        # 添加所有日期（包括没有选股的）
        reports.append({
            'date': date_str,
            'stockSelection': f"html/{date_str}/stock_selection_{date_str}.html" if stock_selection_file.exists() else None,
            'industryTrend': f"html/{date_str}/industry_total_amount_trend.html" if industry_trend_file.exists() else None,
            'j13Trend': f"html/{date_str}/first_j13_step_daily_count.html" if j13_trend_file.exists() else None
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
