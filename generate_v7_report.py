#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 V7 vs V6 对比验证报告 HTML（读 results/v7/ 下的矩阵/敏感性/聚合结果）
输出：html/v7_compare/V7_vs_V6_对比报告.html
"""
import json
import os

OUT_DIR = 'results/v7'
HTML_PATH = os.path.join('html', 'v7_compare', 'V7_vs_V6_对比报告.html')
CFGS = ['T4A', 'T4B', 'T1freeze', 'T1half', 'T3', 'T2full']
CFG_NAMES = {
    'T4A': 'T4A 回升分2期', 'T4B': 'T4B J≥20才打完',
    'T1freeze': 'T1 趋势闸门(冻结)', 'T1half': 'T1 趋势闸门(减半)',
    'T3': 'T3 J分位化', 'T2full': 'T2full 全开',
}
INDUSTRY = ['通信', '创新药', '电力', '科创半导体', '卫星ETF富国',
            '有色金属', '人工智能', '创业板新能源', '机器人']


def load_all():
    matrix = {}
    for fn in sorted(os.listdir(os.path.join(OUT_DIR, 'matrix'))):
        if fn.endswith('.json'):
            with open(os.path.join(OUT_DIR, 'matrix', fn), encoding='utf-8') as f:
                r = json.load(f)
            matrix[r['etf']] = r
    sens = {}
    sdir = os.path.join(OUT_DIR, 'sensitivity')
    if os.path.isdir(sdir):
        for fn in sorted(os.listdir(sdir)):
            if fn.endswith('.json'):
                with open(os.path.join(sdir, fn), encoding='utf-8') as f:
                    r = json.load(f)
                sens[r['etf']] = r['variants']
    with open(os.path.join(OUT_DIR, 'aggregate.json'), encoding='utf-8') as f:
        agg = json.load(f)
    return matrix, sens, agg


def pooled(matrix, etfs, cfg, key, sub=None):
    vals = []
    for e in etfs:
        m = matrix.get(e, {}).get('variants', {}).get(cfg)
        if not m:
            continue
        src = m if sub is None else (m.get(sub) or {})
        v = src.get(key)
        if v is not None:
            vals.append(v)
    return sum(vals) / len(vals) if vals else None


def fmt(v, pct=False, nd=3):
    if v is None:
        return '—'
    return f"{v * 100:+.1f}%" if pct else f"{v:+.3f}"


def main():
    matrix, sens, agg = load_all()
    etfs = list(matrix.keys())
    ind = [e for e in INDUSTRY if e in matrix]

    # ---- 池化表 ----
    pooled_rows = []
    for cfg in ['V6'] + CFGS:
        pooled_rows.append({
            'cfg': cfg,
            'calmar': pooled(matrix, ind, cfg, 'calmar'),
            'dd': pooled(matrix, ind, cfg, 'max_dd'),
            'profit': sum((matrix[e]['variants'].get(cfg, {}).get('total_profit') or 0)
                          for e in ind),
        })
    # OOS 池化（用 aggregate 里的字段）
    oos_rows = {}
    for cfg in CFGS:
        sub = [r for r in agg if r['etf'] in ind and r['cfg'] == cfg]
        ci = [r['oos_calmar_impr'] for r in sub if r.get('oos_calmar_impr') is not None]
        dn = [r['oos_dd_narrow'] for r in sub if r.get('oos_dd_narrow') is not None]
        al = [r['oos_ann_loss_pp'] for r in sub if r.get('oos_ann_loss_pp') is not None]
        r1 = sum(1 for r in sub if r.get('adopt_rule1'))
        r2 = sum(1 for r in sub if r.get('adopt_rule2'))
        oos_rows[cfg] = {
            'ci': sum(ci) / len(ci) if ci else None,
            'dn': sum(dn) / len(dn) if dn else None,
            'al': sum(al) / len(al) if al else None,
            'r1': r1, 'r2': r2,
        }

    # ---- 每标的矩阵表 ----
    etf_rows = []
    for e in etfs:
        r = matrix[e]
        for cfg in ['V6'] + CFGS:
            m = r['variants'].get(cfg)
            if not m:
                continue
            etf_rows.append({
                'etf': e, 'cfg': cfg,
                'ann': m.get('ann_ret'), 'dd': m.get('max_dd'),
                'calmar': m.get('calmar'), 'profit': m.get('total_profit'),
                'is_calmar': (m.get('is') or {}).get('calmar'),
                'oos_calmar': (m.get('oos') or {}).get('calmar'),
                'oos_dd': (m.get('oos') or {}).get('max_dd'),
                'equivalence': r.get('equivalence_v6_v7off'),
                'window': f"{r['backtest_start']}~{r['data_end']}",
            })

    # ---- 敏感性稳定表 ----
    sens_rows = []
    if sens:
        import re
        keys = sorted({k for v in sens.values() for k in v if k.startswith('SENS_')})
        for k in keys:
            unst = st = 0
            vals = []
            for e, vs in sens.items():
                b, c = vs.get('BASE', {}).get('calmar'), vs.get(k, {}).get('calmar')
                if c is not None:
                    vals.append(c)
                if b is None or c is None or abs(b) < 1e-9:
                    continue
                if abs(c / b - 1) > 0.2:
                    unst += 1
                else:
                    st += 1
            sens_rows.append({'key': k.replace('SENS_', ''),
                              'mean': sum(vals) / len(vals) if vals else None,
                              'unstable': unst, 'stable': st})

    # ---- HTML ----
    calmar_chart = json.dumps({
        'title': {'text': '行业ETF组 全窗口 Calmar 均值（池化）', 'left': 'center', 'textStyle': {'fontSize': 14}},
        'tooltip': {},
        'xAxis': {'type': 'category',
                  'data': ['V6'] + [CFG_NAMES[c] for c in CFGS],
                  'axisLabel': {'rotate': 20, 'fontSize': 10}},
        'yAxis': {'type': 'value'},
        'series': [{'type': 'bar',
                    'data': [{'value': c['calmar'],
                              'itemStyle': {'color': '#2e7d32' if c['cfg'] == 'V6' else '#5b8ff9'}}
                             for c in pooled_rows]}],
    }, ensure_ascii=False)
    oos_chart = json.dumps({
        'title': {'text': '行业ETF组 OOS Calmar 改善均值（预注册规则1门槛 +15%）',
                  'left': 'center', 'textStyle': {'fontSize': 14}},
        'tooltip': {},
        'xAxis': {'type': 'category', 'data': [CFG_NAMES[c] for c in CFGS],
                  'axisLabel': {'rotate': 20, 'fontSize': 10}},
        'yAxis': {'type': 'value'},
        'series': [{'type': 'bar',
                    'data': [{'value': oos_rows[c]['ci'],
                              'itemStyle': {'color': '#c62828' if (oos_rows[c]['ci'] or 0) < 0.15 else '#2e7d32'}}
                             for c in CFGS]}],
    }, ensure_ascii=False)

    pooled_html = ''
    for c in pooled_rows:
        cfg = c['cfg']
        if cfg in oos_rows:
            oos_cells = (f"<td>{fmt(oos_rows[cfg]['ci'], pct=True)}</td>"
                         f"<td>{fmt(oos_rows[cfg]['dn'], pct=True)}</td>"
                         f"<td>{oos_rows[cfg]['r1']} / {oos_rows[cfg]['r2']}</td>")
        else:
            oos_cells = "<td>基线</td><td>—</td><td>—</td>"
        pooled_html += (f"<tr><td>{cfg}</td><td>{fmt(c['calmar'])}</td>"
                        f"<td>{fmt(c['dd'], pct=True)}</td><td>{c['profit']:+,.0f}</td>"
                        f"{oos_cells}</tr>")

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>V7 vs V6 对比验证报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
body{{font-family:'Microsoft YaHei',sans-serif;margin:0;background:#f5f6f8;color:#222}}
.wrap{{max-width:1180px;margin:0 auto;padding:24px}}
.banner{{background:#b71c1c;color:#fff;padding:18px 24px;border-radius:8px;font-size:17px}}
.meta{{color:#666;font-size:13px;margin:10px 0 24px}}
h2{{border-left:4px solid #2e7d32;padding-left:10px;font-size:18px}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px;margin:12px 0}}
th,td{{border:1px solid #ddd;padding:6px 9px;text-align:right}}
th{{background:#eef2ee}}
td:first-child,th:first-child{{text-align:left}}
.chart{{background:#fff;border-radius:8px;padding:10px;margin:14px 0}}
.note{{background:#fffbe6;border:1px solid #ffe58f;padding:10px 14px;border-radius:6px;font-size:13px}}
.ok{{color:#2e7d32;font-weight:bold}} .bad{{color:#c62828}}
</style></head><body><div class="wrap">
<div class="banner">判定：全部候选配置未通过预注册采纳标准 —— 生产保持 V6，Phase 4 灰度不启动。
规则1（OOS Calmar改善≥15%）与规则2（OOS回撤收窄≥10%且年化损失≤2pp）在行业ETF组池化均值上全部失败。</div>
<div class="meta">验证日期 2026-09-26 ｜ 数据快照 tushare 2026-09-26（9年缓存）｜ 等价性检查 V7全关 vs V6：
{'<span class="ok">14/14 逐笔一致</span>' if all(r.get('equivalence') for r in etf_rows if r['cfg'] == 'V6') else '<span class="bad">存在失败</span>'}
｜ 详见《V7_vs_V6_对比报告.md》</div>

<h2>一、池化结果（行业ETF组 {len(ind)} 只）</h2>
<table><tr><th>配置</th><th>全窗口 Calmar</th><th>全窗口 maxDD</th><th>全窗口利润合计</th>
<th>OOS Calmar改善</th><th>OOS回撤收窄</th><th>规则1/2通过(各/9)</th></tr>
{pooled_html}
</table>
<div class="note">回撤收窄为正=候选OOS回撤更浅。风险型配置 OOS 回撤反而更宽（闸门降低参与度，2025-26 段相对回撤更深）。
84 个候选-标的对中规则1散落通过19个、规则2通过7个，集中于宽基与孤立小基数样本，呈过拟合尖峰形态，不予采纳。</div>

<div class="chart" id="c1" style="height:300px"></div>
<div class="chart" id="c2" style="height:300px"></div>

<h2>二、全量矩阵（{len(etfs)} 标的 × {1 + len(CFGS)} 配置）</h2>
<table><tr><th>ETF</th><th>窗口</th><th>配置</th><th>年化</th><th>maxDD</th><th>Calmar</th>
<th>总利润</th><th>IS Calmar</th><th>OOS Calmar</th><th>OOS maxDD</th></tr>
{''.join(f"<tr><td>{r['etf']}</td><td>{r['window']}</td><td>{r['cfg']}</td>"
         f"<td>{fmt(r['ann'], pct=True)}</td><td>{fmt(r['dd'], pct=True)}</td>"
         f"<td>{fmt(r['calmar'])}</td><td>{r['profit']:+,.0f}</td>"
         f"<td>{fmt(r['is_calmar'])}</td><td>{fmt(r['oos_calmar'])}</td>"
         f"<td>{fmt(r['oos_dd'], pct=True)}</td></tr>" for r in etf_rows)}
</table>

<h2>三、参数敏感性（±20% 邻域，以 T2full 为基准）</h2>
<table><tr><th>扰动</th><th>池化 Calmar 均值</th><th>不稳定ETF数</th><th>稳定ETF数</th></tr>
{''.join(f"<tr><td>{r['key']}</td><td>{fmt(r['mean'])}</td><td>{r['unstable']}</td><td>{r['stable']}</td></tr>"
         for r in sens_rows)}
</table>
<div class="note">绝大多数扰动不稳定数 0-2/9：不优于 V6 的结论是系统性的，不是邻域噪声。</div>

<h2>四、局限</h2>
<div class="note">
① 净值口径为自洽口径（市值+累计卖出金额)/累计投入；生产 calc_nav_curve 漏加卖出返还本金，仅影响展示不建议用于排序。
② OOS 窗口随 ETF 各自 70% 切分而不同。③ NASDAQ100 生产基线为 V1（含倍投），矩阵行仅参考。
④ 次新ETF（科创半导体/卫星/创业板新能源）窗口 0.5-1.2 年，T1/T3 自动回退，参考价值低。
⑤ 0AMV 数据仍无自动更新链路（截止 2026-09-24）。
</div>
</div>
<script>
echarts.init(document.getElementById('c1')).setOption({calmar_chart});
echarts.init(document.getElementById('c2')).setOption({oos_chart});
window.addEventListener('resize', ()=>{{location.reload()}});
</script>
</body></html>"""
    os.makedirs(os.path.dirname(HTML_PATH), exist_ok=True)
    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"已生成 {HTML_PATH}（{len(etfs)} 标的，{len(etf_rows)} 行矩阵）")


if __name__ == '__main__':
    main()
