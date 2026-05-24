#!/usr/bin/env python3
# Append generate_c154_html function to generate_stock_html.py

with open(r'c:\Users\zxlin\Desktop\大富翁\wbzq\generate_stock_html.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_function = '''

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
        table_data.append({
            '代码': ts_code,
            '名称': name,
            '行业': row.get('industry_name', '未知'),
            '收盘价': f"{row['close_qfq']:.2f}",
            '涨跌幅': f"{pct_val:.2f}%",
            '涨跌幅样式': pct_class,
            'J值': f"{j_val:.2f}",
            'J值样式': j_class,
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
        f'<tr class="hover:bg-gray-50 cursor-pointer" onclick="showChart(\'{r["代码"]}\', \'{r["名称"]}\')">'
        f'<td class="px-6 py-4 font-medium text-blue-600 hover:text-blue-800">{r["代码"]}</td>'
        f'<td class="px-6 py-4 font-medium">{r["名称"]}</td>'
        f'<td class="px-6 py-4"><span class="px-2 py-1 bg-blue-100 text-blue-800 rounded-full text-xs">{r["行业"]}</span></td>'
        f'<td class="px-6 py-4">{r["收盘价"]}</td>'
        f'<td class="px-6 py-4 font-semibold {r["涨跌幅样式"]}">{r["涨跌幅"]}</td>'
        f'<td class="px-6 py-4 font-bold {r["J值样式"]}">{r["J值"]}</td>'
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
                <button onclick="downloadCSV()" class="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                    下载 CSV
                </button>
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

        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">选股总数</div><div class="text-2xl font-bold text-orange-600">{n}</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">涉及行业</div><div class="text-2xl font-bold text-green-600">{n_ind}</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">平均J值</div><div class="text-2xl font-bold text-red-600">{avg_j:.2f}</div></div>
            <div class="bg-white rounded-xl shadow-sm p-4"><div class="text-sm text-gray-500">平均涨幅</div><div class="text-2xl font-bold text-blue-600">{avg_pct:.2f}%</div></div>
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
                            <th class="px-6 py-3" onclick="sortTable(6)">MACD-DIF</th>
                            <th class="px-6 py-3" onclick="sortTable(7)">成交额(万)</th>
                            <th class="px-6 py-3" onclick="sortTable(8)">暴力K次数</th>
                            <th class="px-6 py-3" onclick="sortTable(9)">异动次数</th>
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
            <div id="klineChart" class="chart-container mb-4"></div>
            <div id="volumeChart" class="chart-container mb-4" style="height:150px;"></div>
            <div id="kdjChart" class="chart-container" style="height:200px;"></div>
        </div>
    </div>

    <script>
    const stockCharts = {charts_json};
    const stockCSV = `{csv_data}`;

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
        tbl.querySelectorAll('th').forEach((th,i) => {{ th.classList.remove('sort-asc','sort-desc'); if(i===col) th.classList.add(sortDir[col]?'sort-desc':'sort-asc'); }}));
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
        document.getElementById('modalTitle').textContent=nm+' ('+ts+') - 技术分析';
        document.getElementById('chartModal').style.display='block';
        if(kc)kc.dispose(); if(vc)vc.dispose(); if(kdjc)kdjc.dispose();
        kc=echarts.init(document.getElementById('klineChart'));
        const cd=d.candlestick.map(c=>({{value:[c[1],c[2],c[3],c[4]],itemStyle:{{color:c[2]>=c[1]?'#ef4444':'#22c55e',color0:c[2]>=c[1]?'#ef4444':'#22c55e',borderColor:c[2]>=c[1]?'#ef4444':'#22c55e',borderColor0:c[2]>=c[1]?'#ef4444':'#22c55e'}}}}));
        kc.setOption({{title:{{text:'K线+MA60+知行多空',left:'center',textStyle:{{fontSize:14}}}},tooltip:{{trigger:'axis',axisPointer:{{type:'cross'}}}},legend:{{data:['K线','MA60','知行多空','知行中'],bottom:0}},grid:{{left:'8%',right:'8%',top:'40px',bottom:'50px'}},xAxis:{{type:'category',data:d.dates}},yAxis:{{type:'value',scale:true}},series:[{{type:'candlestick',data:cd,name:'K线'}},{{type:'line',data:d.ma60,name:'MA60',smooth:true,lineStyle:{{color:'#f59e0b',width:2}}}},{{type:'line',data:d.zhixing_duokong,name:'知行多空',smooth:true,lineStyle:{{color:'#3b82f6',width:1}}}},{{type:'line',data:d.zhixing_mid_duokong,name:'知行中',smooth:true,lineStyle:{{color:'#8b5cf6',width:1}}}}]}});
        vc=echarts.init(document.getElementById('volumeChart'));
        const vd=d.volume.map(v=>({{value:v.value,itemStyle:v.itemStyle}}));
        vc.setOption({{title:{{text:'成交额（万元）',left:'center',textStyle:{{fontSize:14}}}},tooltip:{{trigger:'axis'}},grid:{{left:'8%',right:'8%',top:'40px',bottom:'30px'}},xAxis:{{type:'category',data:d.dates}},yAxis:{{type:'value'}},series:[{{type:'bar',data:vd}}]}});
        kdjc=echarts.init(document.getElementById('kdjChart'));
        kdjc.setOption({{title:{{text:'KDJ指标',left:'center',textStyle:{{fontSize:14}}}},tooltip:{{trigger:'axis'}},legend:{{data:['K','D','J'],bottom:0}},grid:{{left:'10%',right:'10%',top:'40px',bottom:'40px'}},xAxis:{{type:'category',data:d.dates}},yAxis:{{type:'value',min:0,max:100}},series:[{{type:'line',data:d.kdj_k,name:'K',smooth:true,lineStyle:{{color:'#3b82f6'}}}},{{type:'line',data:d.kdj_d,name:'D',smooth:true,lineStyle:{{color:'#f59e0b'}}}},{{type:'line',data:d.kdj_j,name:'J',smooth:true,lineStyle:{{color:'#ef4444'}}}}]}});
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

'''

if '__main__' in content:
    content = content[:content.find('if __name__')]
content = content.rstrip() + '\n' + new_function + '\n'

with open(r'c:\Users\zxlin\Desktop\大富翁\wbzq\generate_stock_html.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done!')
