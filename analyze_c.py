import json, re
from collections import defaultdict

with open(r'c:\Users\zxlin\Desktop\大富翁\wbzq\html\20260522\ablation_report.html', 'r', encoding='utf-8') as f:
    content = f.read()

m = re.search(r'const DATA = (\[.*?\]);', content, re.DOTALL)
if m:
    data = json.loads(m.group(1))
    part_c = [d for d in data if d.get('_part') == 'C' and d.get('样本量', 0) >= 50]
    for d in part_c:
        d['综合得分'] = d.get('胜率', 0) * 0.4 + d.get('平均涨幅', 0) * 10 + d.get('盈亏比', 0) * 5

    part_c.sort(key=lambda x: x['综合得分'], reverse=True)

    print('=== Part C Top 30 (样本>=50) ===')
    header = '{:>4} {:<55} {:>6} {:>8} {:>6} {:>6} {:>8}'.format(
        '排名', '实验组', '样本量', '平均涨幅', '胜率', '盈亏比', '综合得分')
    print(header)
    for i, d in enumerate(part_c[:30], 1):
        line = '{:>4} {:<55} {:>6} {:>8.2f} {:>6.1f} {:>6.2f} {:>8.2f}'.format(
            i, d['实验组'], d['样本量'], d['平均涨幅'], d['胜率'], d['盈亏比'], d['综合得分'])
        print(line)

    # Group by condition count
    groups = defaultdict(list)
    for d in part_c:
        label = d['实验组']
        cond_part = label.split('-', 1)[1] if '-' in label else label
        n = cond_part.count('+') + 1
        groups[n].append(d)

    print('\n=== 按条件数量分组统计 (样本>=50) ===')
    print('{:>6} {:>6} {:>10} {:>10} {:>10} {:>10}'.format(
        '条件数', '组数', '最高得分', '平均得分', '最高涨幅', '平均涨幅'))
    for n in sorted(groups.keys()):
        g = groups[n]
        scores = [d['综合得分'] for d in g]
        gains = [d['平均涨幅'] for d in g]
        print('{:>6} {:>6} {:>10.2f} {:>10.2f} {:>10.2f} {:>10.2f}'.format(
            n, len(g), max(scores), sum(scores)/len(scores), max(gains), sum(gains)/len(gains)))

    # Top 5 for each condition count
    print('\n=== 各条件数 Top 5 ===')
    for n in sorted(groups.keys()):
        g = sorted(groups[n], key=lambda x: x['综合得分'], reverse=True)
        print(f'\n--- {n}个条件 Top 5 ---')
        for i, d in enumerate(g[:5], 1):
            print('  {:>2}. {:<55} 样本={:>5} 涨幅={:>6.2f}% 胜率={:>5.1f}% 盈亏比={:>5.2f} 得分={:>6.2f}'.format(
                i, d['实验组'], d['样本量'], d['平均涨幅'], d['胜率'], d['盈亏比'], d['综合得分']))

    # Condition frequency in top 50
    print('\n=== Top 50 中各条件出现频率 ===')
    top50 = sorted(part_c, key=lambda x: x['综合得分'], reverse=True)[:50]
    freq = defaultdict(int)
    for d in top50:
        label = d['实验组']
        cond_part = label.split('-', 1)[1] if '-' in label else label
        for c in cond_part.split('+'):
            freq[c.strip()] += 1
    for c, f in sorted(freq.items(), key=lambda x: -x[1]):
        print(f'  {c:<10} {f:>3}/50 ({f*2}%)')

    # Sample distribution
    all_c = [d for d in data if d.get('_part') == 'C']
    print(f'\nPart C 总组数: {len(all_c)}')
    print(f'样本>=500: {len([d for d in all_c if d.get("样本量",0)>=500])}')
    print(f'样本200-499: {len([d for d in all_c if 200<=d.get("样本量",0)<500])}')
    print(f'样本50-199: {len([d for d in all_c if 50<=d.get("样本量",0)<200])}')
    print(f'样本<50: {len([d for d in all_c if d.get("样本量",0)<50])}')
