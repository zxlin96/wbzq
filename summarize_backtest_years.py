#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""读取回测 CSV，按年、按月汇总 T+1/T+3/T+5/T+10 胜率与收益，并生成 HTML 报告。"""
import os
import sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES_CSV = os.path.join(HERE, "cmp_5y", "backtest_industry_etf_trades.csv")
OUT_HTML = os.path.join(HERE, "cmp_5y", "backtest_yearly_monthly.html")


def summarize(group_col: str, df: pd.DataFrame):
    """按 group_col(年/年月) 分组汇总各持有期统计。返回 list[dict]。"""
    rows = []
    for key, g in df.groupby(group_col):
        row = {"group": key, "n": len(g)}
        for day in [1, 3, 5, 10]:
            col = f"return_t{day}"
            if col not in g.columns:
                continue
            r = g[col].dropna()
            if r.empty:
                row[f"t{day}_wr"] = None
                row[f"t{day}_avg"] = None
                continue
            wins = (r > 0).sum()
            row[f"t{day}_wr"] = wins / len(r) * 100
            row[f"t{day}_avg"] = r.mean()
        rows.append(row)
    return rows


def section_html(title: str, rows, group_labels):
    head = """
    <tr style="background:#f5f5f5">
        <th style="padding:6px 8px;border:1px solid #ddd">{g}</th>
        <th style="padding:6px 8px;border:1px solid #ddd">笔数</th>
        <th style="padding:6px 8px;border:1px solid #ddd">T+1胜率</th>
        <th style="padding:6px 8px;border:1px solid #ddd">T+1均收益</th>
        <th style="padding:6px 8px;border:1px solid #ddd">T+3胜率</th>
        <th style="padding:6px 8px;border:1px solid #ddd">T+3均收益</th>
        <th style="padding:6px 8px;border:1px solid #ddd">T+5胜率</th>
        <th style="padding:6px 8px;border:1px solid #ddd">T+5均收益</th>
        <th style="padding:6px 8px;border:1px solid #ddd">T+10胜率</th>
        <th style="padding:6px 8px;border:1px solid #ddd">T+10均收益</th>
    </tr>
    """
    body = ""
    for r in rows:
        def cell(wr, avg):
            if wr is None:
                return '<td style="padding:6px 8px;border:1px solid #ddd">—</td>'
            color = "#27ae60" if avg >= 0 else "#e74c3c"
            return (f'<td style="padding:6px 8px;border:1px solid #ddd">{wr:.1f}%</td>'
                    f'<td style="padding:6px 8px;border:1px solid #ddd;color:{color}">{avg:+.2f}%</td>')
        t1 = cell(r.get("t1_wr"), r.get("t1_avg"))
        t3 = cell(r.get("t3_wr"), r.get("t3_avg"))
        t5 = cell(r.get("t5_wr"), r.get("t5_avg"))
        t10 = cell(r.get("t10_wr"), r.get("t10_avg"))
        body += f"""
        <tr>
            <td style="padding:6px 8px;border:1px solid #ddd">{r['group']}</td>
            <td style="padding:6px 8px;border:1px solid #ddd">{r['n']}</td>
            {t1}{t3}{t5}{t10}
        </tr>"""
    return f"<h3 style='margin-top:24px'>{title}</h3><table style='border-collapse:collapse;width:100%;font-size:12px'>{head}{body}</table>"


def main():
    if not os.path.exists(TRADES_CSV):
        print(f"未找到回测文件: {TRADES_CSV}")
        sys.exit(1)

    df = pd.read_csv(TRADES_CSV, dtype={"signal_date": str, "buy_date": str})
    df["signal_date"] = df["signal_date"].astype(str)
    df["year"] = df["signal_date"].str[:4]
    df["ym"] = df["signal_date"].str[:6]

    year_rows = summarize("year", df)
    month_rows = summarize("ym", df)

    # 控制台打印
    print("===== 按年 =====")
    for r in year_rows:
        print(f"{r['group']}  笔数={r['n']:3d}  "
              f"T+1 {r.get('t1_wr'):.1f}%/{r.get('t1_avg'):+.2f}%  "
              f"T+3 {r.get('t3_wr'):.1f}%/{r.get('t3_avg'):+.2f}%  "
              f"T+5 {r.get('t5_wr'):.1f}%/{r.get('t5_avg'):+.2f}%  "
              f"T+10 {r.get('t10_wr'):.1f}%/{r.get('t10_avg'):+.2f}%")
    print("\n===== 按年月（信号数>=3 的月份）=====")
    for r in month_rows:
        if r["n"] < 3:
            continue
        print(f"{r['group']}  笔数={r['n']:3d}  "
              f"T+1 {r.get('t1_wr'):.1f}%/{r.get('t1_avg'):+.2f}%  "
              f"T+5 {r.get('t5_wr'):.1f}%/{r.get('t5_avg'):+.2f}%")

    # HTML
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <title>行业ETF回测 年/月汇总</title></head><body style="font-family:sans-serif;max-width:1000px;margin:0 auto;padding:16px">
    <h2>行业ETF回测 年/月汇总（策略C：no-repeat + 知行多空过滤）</h2>
    <p style="color:#666">数据区间：{df['signal_date'].min()} ~ {df['signal_date'].max()}  总笔数：{len(df)}</p>
    {section_html("📅 按年", year_rows, "年份")}
    {section_html("📆 按年月", month_rows, "年月")}
    </body></html>"""
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 报告: {OUT_HTML}")


if __name__ == "__main__":
    main()
