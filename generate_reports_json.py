#!/usr/bin/env python3
"""
生成 GitHub Pages 索引文件
"""

import os
import json
import re
import csv
from datetime import datetime
from pathlib import Path


def generate_reports_json():
    """生成 reports.json 索引文件，包含主策略和 MACD 策略结果"""
    
    reports = []
    
    # 查找 html/ 目录下的所有日期目录
    html_base_dir = Path('html')
    if not html_base_dir.exists():
        print("⚠️  html/ 目录不存在，跳过生成索引")
        return
    
    # 获取所有日期目录并按日期排序
    date_dirs = sorted([d for d in html_base_dir.iterdir()
                        if d.is_dir() and d.name.isdigit() and len(d.name) == 8],
                       key=lambda x: x.name, reverse=True)
    
    for date_dir in date_dirs:
        date_str = date_dir.name
        
        # 检查该日期目录下的文件
        stock_selection_file = date_dir / f"stock_selection_{date_str}.html"
        industry_trend_file = date_dir / "industry_total_amount_trend.html"
        j13_trend_file = date_dir / "first_j13_step_daily_count.html"
        macd_csv_file = Path(f"macd_result_{date_str}.csv")
        macd_html_file = date_dir / f"macd_selection_{date_str}.html"
        sentiment_rebound_file = date_dir / "sentiment_rebound_strategy.html"
        multi_indicator_file = date_dir / f"multi_indicator_selection_{date_str}.html"
        
        # 添加所有日期（包括没有选股的）
        reports.append({
            'date': date_str,
            'stockSelection': f"html/{date_str}/stock_selection_{date_str}.html" if stock_selection_file.exists() else None,
            'industryTrend': f"html/{date_str}/industry_total_amount_trend.html" if industry_trend_file.exists() else None,
            'j13Trend': f"html/{date_str}/first_j13_step_daily_count.html" if j13_trend_file.exists() else None,
            'macdResult': f"macd_result_{date_str}.csv" if macd_csv_file.exists() else None,
            'macdHtml': f"html/{date_str}/macd_selection_{date_str}.html" if macd_html_file.exists() else None,
            'sentimentRebound': f"html/{date_str}/sentiment_rebound_strategy.html" if sentiment_rebound_file.exists() else None,
            'multiIndicator': f"html/{date_str}/multi_indicator_selection_{date_str}.html" if multi_indicator_file.exists() else None,
        })
    
    # 扫描根目录下的 macd_result_*.csv，补充 html 中没有对应目录的日期
    existing_dates = {r['date'] for r in reports}
    for csv_file in sorted(Path('.').glob('macd_result_*.csv'), reverse=True):
        date_str = csv_file.stem.replace('macd_result_', '')
        if date_str not in existing_dates:
            macd_html_path = html_base_dir / date_str / f"macd_selection_{date_str}.html"
            sentiment_rebound_path = html_base_dir / date_str / "sentiment_rebound_strategy.html"
            reports.append({
                'date': date_str,
                'stockSelection': None,
                'industryTrend': None,
                'j13Trend': None,
                'macdResult': str(csv_file),
                'macdHtml': f"html/{date_str}/macd_selection_{date_str}.html" if macd_html_path.exists() else None,
                'sentimentRebound': f"html/{date_str}/sentiment_rebound_strategy.html" if sentiment_rebound_path.exists() else None,
                'multiIndicator': None,
            })
            existing_dates.add(date_str)
    
    # 按日期降序重新排序
    reports.sort(key=lambda x: x['date'], reverse=True)
    
    # 统计信息
    total_stocks = 0
    macd_stocks = 0
    if reports:
        try:
            latest_report = reports[0]
            stock_selection_file = latest_report['stockSelection']
            if stock_selection_file and os.path.exists(stock_selection_file):
                with open(stock_selection_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if '共选出' in content:
                        match = re.search(r'共选出 (\d+) 只', content)
                        if match:
                            total_stocks = int(match.group(1))
        except:
            pass
        try:
            latest_report = reports[0]
            macd_file = latest_report.get('macdResult')
            if macd_file and os.path.exists(macd_file):
                with open(macd_file, 'r', encoding='utf-8-sig') as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                    macd_stocks = max(0, len(rows) - 1)
        except:
            pass
    
    latest_date = reports[0]['date'] if reports else '-'
    
    data = {
        'lastUpdate': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'totalStocks': total_stocks,
        'macdStocks': macd_stocks,
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
    print(f"   - 主策略选股: {total_stocks} 只")
    print(f"   - MACD 零轴金叉: {macd_stocks} 只")


if __name__ == '__main__':
    generate_reports_json()