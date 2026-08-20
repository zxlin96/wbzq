#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成邮件通知报告 (generate_email_report.py)

读取策略运行结果，生成 email_report.html 供邮件通知使用。

数据源：
  - reports.json            → 选股数量概览
  - macd_result_*.csv       → MACD 零轴金叉结果
  - html/dca/dca_summary.json → ETF 定投汇总
  - strategy_state.json     → 情绪反弹策略状态
  - multi_indicator_hints_*.json → 策略3 超阈值行业提示

使用方式：
    python generate_email_report.py
    python generate_email_report.py --status success
    python generate_email_report.py --status failure
"""

import argparse
import json
import os
import glob
import logging
import pandas as pd


def load_reports_json():
    """加载 reports.json"""
    path = "reports.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def load_strategy_state():
    """加载情绪反弹策略状态"""
    path = "strategy_state.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def load_dca_summary():
    """加载 ETF 定投汇总"""
    path = "html/dca/dca_summary.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def load_csv_result(pattern):
    """加载最新的 CSV 结果文件"""
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()
    latest = files[-1]
    try:
        df = pd.read_csv(latest, encoding="utf-8-sig")
        return df
    except Exception:
        return pd.DataFrame()


def load_multi_indicator_hints():
    """加载最新的超阈值行业提示 JSON 文件。

    Returns:
        list: 提示清单 list[dict]；文件缺失或损坏时返回 []。
    """
    files = sorted(glob.glob("multi_indicator_hints_*.json"))
    if not files:
        return []
    latest = files[-1]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("加载超阈值行业提示失败 (%s): %s", latest, e)
        return []
    if not isinstance(data, list):
        logging.warning("超阈值行业提示文件 %s 内容非 list 类型: %s", latest, type(data).__name__)
        return []
    return data


def escape_html(text):
    """转义 HTML 特殊字符"""
    if not isinstance(text, str):
        text = str(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_stock_table(df, label):
    """生成股票表格 HTML（通用，支持带 score_level 和不带 score_level 的 CSV）"""
    if df.empty:
        return f"<p>暂无{label}数据</p>"

    has_score = "score_level" in df.columns
    if has_score:
        filtered = df[df["score_level"].isin(["A", "B"])].copy()
    else:
        # 对于主策略或 MACD 结果，取前 10 只
        filtered = df.head(10).copy()

    if filtered.empty:
        if has_score:
            return f"<p>{label}：无 A/B 级股票（共 {len(df)} 只）</p>"
        return f"<p>{label}：暂无数据</p>"

    level_colors = {"A": "#e74c3c", "B": "#3498db", "C": "#95a5a6", "D": "#bdc3c7"}

    rows = ""
    for _, row in filtered.iterrows():
        if has_score:
            level = row.get("score_level", "")
            color = level_colors.get(level, "#333")
            rows += f"""
            <tr>
                <td>{escape_html(row.get('ts_code', ''))}</td>
                <td><strong>{escape_html(row.get('name', ''))}</strong></td>
                <td>{escape_html(row.get('industry_name', ''))}</td>
                <td>{row.get('close_qfq', 0):.2f}</td>
                <td>{row.get('pct_chg', 0):.2f}%</td>
                <td style="color:{color};font-weight:bold">{level}</td>
                <td>{row.get('score', 0):.0f}</td>
            </tr>"""
        else:
            # MACD 结果：显示 DIF 和偏差 %
            line_diff = abs(row.get('zhixing_duokong', 0) - row.get('zhixing_mid_duokong', 0))
            close = row.get('close_qfq', 0)
            line_pct = line_diff / close * 100 if close > 0 else 0
            rows += f"""
            <tr>
                <td>{escape_html(row.get('ts_code', ''))}</td>
                <td><strong>{escape_html(row.get('name', ''))}</strong></td>
                <td>{escape_html(row.get('industry_name', ''))}</td>
                <td>{row.get('close_qfq', 0):.2f}</td>
                <td>{row.get('pct_chg', 0):.2f}%</td>
                <td>{row.get('macd_dif_qfq', 0):.4f}</td>
                <td>{line_pct:.3f}%</td>
            </tr>"""

    ab_count = len(filtered)
    total_count = len(df)

    if has_score:
        header = f"<p><strong>{label}精选（A/B 级 {ab_count} 只，全部 {total_count} 只）</strong></p>"
        headers = """
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">代码</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">名称</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">行业</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">收盘价</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">涨幅</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:center">等级</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">评分</th>"""
    else:
        header = f"<p><strong>{label}（前 {ab_count} 只，共 {total_count} 只）</strong></p>"
        headers = """
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">代码</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">名称</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">行业</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">收盘价</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">涨幅</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">DIF</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">黄白偏差%</th>"""

    return f"""
    {header}
    <table style="border-collapse:collapse;width:100%;font-size:13px;margin-bottom:16px">
        <tr style="background:#f5f5f5">
            {headers}
        </tr>
        {rows}
    </table>"""


def _position_return_info(position, etf_last_price=0):
    """获取单轮持仓的累计收益率、投入金额、持有市值"""
    return_pct = position.get("return_pct")
    total_invested = position.get("total_invested", 0) or 0
    shares = position.get("shares", 0) or 0
    last_price = etf_last_price or position.get("last_price", 0)
    holding_value = round(shares * last_price, 2) if shares > 0 and last_price > 0 else 0
    if return_pct is not None:
        return float(return_pct), total_invested, holding_value
    # 兼容旧数据：没有 return_pct 时用浮盈浮亏
    avg_cost = position.get("avg_cost")
    if avg_cost and avg_cost > 0 and last_price > 0:
        return (last_price - avg_cost) / avg_cost * 100, total_invested, holding_value
    return 0, total_invested, holding_value


def generate_dca_section(dca_summary):
    """生成 ETF 定投汇总 HTML（支持多轮持仓）"""
    if not dca_summary:
        return "<p>暂无 ETF 定投数据</p>"

    etf_list = dca_summary.get("etf_list", [])
    if not etf_list:
        return "<p>暂无 ETF 定投数据</p>"

    rows = ""
    for etf in etf_list:
        positions = etf.get("positions", [])
        name = escape_html(etf.get("name", ""))
        ts_code = escape_html(etf.get("ts_code", ""))
        ret_pct = etf.get("return_pct", 0) or 0
        invested = etf.get("total_invested", 0) or 0
        last_price = etf.get("last_price", 0) or 0

        ret_str = f"+{ret_pct:.2f}%" if ret_pct > 0 else f"{ret_pct:.2f}%"
        ret_color = "#27ae60" if ret_pct >= 0 else "#e74c3c"

        if not positions:
            # 空仓 — 单行显示
            rows += f"""
            <tr>
                <td><strong>{name}</strong><br><span style="color:#999;font-size:11px">{ts_code}</span></td>
                <td style="color:#95a5a6;font-weight:bold">空仓等待</td>
                <td>—</td>
                <td>—</td>
            </tr>"""
        elif len(positions) == 1:
            # 单轮 — 单行显示
            pos = positions[0]
            color = pos.get("action_color", "#333")
            label = escape_html(pos.get("action_label", ""))
            pos_ret, pos_invested, pos_remaining = _position_return_info(pos, last_price)
            pos_ret_str = f"+{pos_ret:.2f}%" if pos_ret > 0 else f"{pos_ret:.2f}%"
            pos_ret_color = "#27ae60" if pos_ret >= 0 else "#e74c3c"

            rows += f"""
            <tr>
                <td><strong>{name}</strong><br><span style="color:#999;font-size:11px">{ts_code}</span></td>
                <td style="color:{color};font-weight:bold">{label}</td>
                <td style="color:{pos_ret_color};font-weight:bold">{pos_ret_str}</td>
                <td>{pos_remaining:.0f}元</td>
            </tr>"""
        else:
            # 多轮 — 汇总行 + 每轮子行
            total_remaining = sum(
                _position_return_info(pos, last_price)[2] for pos in positions
            )
            rows += f"""
            <tr>
                <td><strong>{name}</strong><br><span style="color:#999;font-size:11px">{ts_code}</span></td>
                <td style="color:#666;font-size:12px">{len(positions)}轮持仓</td>
                <td>—</td>
                <td>{total_remaining:.0f}元</td>
            </tr>"""
            # 每轮子行
            for pos in positions:
                round_id = pos.get("round_id", "")
                color = pos.get("action_color", "#333")
                label = escape_html(pos.get("action_label", ""))
                shares = pos.get("shares", 0)
                pos_ret, pos_invested, pos_remaining = _position_return_info(pos, last_price)
                pos_ret_str = f"+{pos_ret:.2f}%" if pos_ret > 0 else f"{pos_ret:.2f}%"
                pos_ret_color = "#27ae60" if pos_ret >= 0 else "#e74c3c"
                avg_cost = pos.get("avg_cost", 0) or 0

                rows += f"""
            <tr style="background:#fafafa">
                <td style="padding-left:24px;color:#888;font-size:12px">↳ R{round_id} | {shares:.0f}份 | 成本{avg_cost:.3f}</td>
                <td style="color:{color};font-size:12px">{label}</td>
                <td style="color:{pos_ret_color};font-size:12px">{pos_ret_str}</td>
                <td style="font-size:12px;color:#666">{pos_remaining:.0f}元</td>
            </tr>"""

    return f"""
    <table style="border-collapse:collapse;width:100%;font-size:13px;margin-bottom:16px">
        <tr style="background:#f5f5f5">
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">ETF</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">操作建议</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">收益率</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">持有市值</th>
        </tr>
        {rows}
    </table>"""


def generate_sentiment_section(state):
    """生成情绪反弹策略 HTML"""
    if not state:
        return "<p>暂无情绪反弹策略数据</p>"

    position = state.get("position", 0)
    level = state.get("investment_level_idx", 0)
    trades = state.get("trades", [])

    # 最近一笔交易
    last_trade = trades[-1] if trades else None
    last_trade_str = "无交易记录"
    if last_trade:
        action = last_trade.get("action", "")
        date = last_trade.get("date", "")
        reason = last_trade.get("reason", "")
        price = last_trade.get("price", "")
        if price:
            last_trade_str = f"{date} {action} @ {price}（{reason}）"
        else:
            last_trade_str = f"{date} {action}（{reason}）"

    position_str = f"{position} 份" if position > 0 else "空仓"

    return f"""
    <table style="border-collapse:collapse;font-size:13px;margin-bottom:16px">
        <tr>
            <td style="padding:4px 12px"><strong>当前持仓:</strong></td>
            <td style="padding:4px 12px">{position_str}</td>
        </tr>
        <tr>
            <td style="padding:4px 12px"><strong>投资级别:</strong></td>
            <td style="padding:4px 12px">第 {level + 1} 级</td>
        </tr>
        <tr>
            <td style="padding:4px 12px"><strong>历史交易:</strong></td>
            <td style="padding:4px 12px">{len(trades)} 笔</td>
        </tr>
        <tr>
            <td style="padding:4px 12px"><strong>最近交易:</strong></td>
            <td style="padding:4px 12px">{escape_html(last_trade_str)}</td>
        </tr>
    </table>"""


def generate_multi_indicator_hint_section(hints):
    """生成策略3超阈值行业提示板块 HTML（不含 h3 标题）。

    Args:
        hints: 提示清单 list[dict]，每条含 {industry, count}

    Returns:
        str: HTML 片段字符串。
    """
    if not hints:
        return '<p style="color:#999;font-size:13px">暂无超阈值行业</p>'

    rows = ""
    for h in hints:
        industry = escape_html(h.get("industry", ""))
        count = h.get("count", 0)
        rows += f"""
        <tr>
            <td style="padding:6px 8px;border:1px solid #ddd;text-align:left">{industry}</td>
            <td style="padding:6px 8px;border:1px solid #ddd;text-align:right">{count}</td>
        </tr>"""

    return f"""
    <table style="border-collapse:collapse;width:100%;font-size:13px;margin-bottom:16px">
        <tr style="background:#f5f5f5">
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">行业</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">入选数量</th>
        </tr>
        {rows}
    </table>"""


def generate_email_report(status="success", repo="", ref="", run_url=""):
    """生成完整邮件 HTML"""
    # 加载数据
    reports = load_reports_json()
    strategy_state = load_strategy_state()
    dca_summary = load_dca_summary()
    macd_df = load_csv_result("macd_result_*.csv")
    multi_hints = load_multi_indicator_hints()

    # 状态图标和颜色
    status_map = {
        "success": ("✅ 成功", "#27ae60"),
        "failure": ("❌ 失败", "#e74c3c"),
        "cancelled": ("⚠️ 取消", "#f39c12"),
    }
    status_text, status_color = status_map.get(status, (status, "#333"))

    # 概览数据
    latest_date = reports.get("latestDate", "") if reports else ""
    total_stocks = reports.get("totalStocks", 0) if reports else 0
    macd_stocks = reports.get("macdStocks", 0) if reports else 0

    report_url = "https://zxlin96.github.io/wbzq/"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
             color: #333; max-width: 700px; margin: 0 auto; padding: 16px;">

<h2 style="border-bottom:2px solid #3498db;padding-bottom:8px">
    📊 股票策略执行通知
</h2>

<table style="font-size:14px;margin-bottom:20px">
    <tr>
        <td style="padding:4px 12px"><strong>📅 日期:</strong></td>
        <td style="padding:4px 12px">{latest_date}</td>
    </tr>
    <tr>
        <td style="padding:4px 12px"><strong>📋 状态:</strong></td>
        <td style="padding:4px 12px;color:{status_color};font-weight:bold">{status_text}</td>
    </tr>
</table>

<h3 style="color:#2c3e50;border-left:4px solid #3498db;padding-left:10px;margin-top:24px">
    📈 选股结果概览
</h3>
<table style="border-collapse:collapse;font-size:14px;margin-bottom:16px">
    <tr>
        <td style="padding:8px 16px;background:#ebf5fb;text-align:center;border-radius:4px;margin:4px">
            <div style="font-size:24px;font-weight:bold;color:#2980b9">{total_stocks}</div>
            <div style="font-size:12px;color:#666">主策略</div>
        </td>
        <td style="padding:8px 16px;background:#fef9e7;text-align:center;border-radius:4px;margin:4px">
            <div style="font-size:24px;font-weight:bold;color:#f39c12">{macd_stocks}</div>
            <div style="font-size:12px;color:#666">MACD 零轴金叉</div>
        </td>
    </tr>
</table>

<h3 style="color:#2c3e50;border-left:4px solid #f39c12;padding-left:10px;margin-top:24px">
    🏆 MACD 零轴金叉
</h3>
{generate_stock_table(macd_df, "MACD")}

<h3 style="color:#2c3e50;border-left:4px solid #27ae60;padding-left:10px;margin-top:24px">
    💰 ETF 定投策略汇总
</h3>
{generate_dca_section(dca_summary)}

<h3 style="color:#2c3e50;border-left:4px solid #9b59b6;padding-left:10px;margin-top:24px">
    📊 情绪反弹策略
</h3>
{generate_sentiment_section(strategy_state)}

<h3 style="color:#2c3e50;border-left:4px solid #e67e22;padding-left:10px;margin-top:24px">
    🏷️ 策略3 超阈值行业提示
</h3>
{generate_multi_indicator_hint_section(multi_hints)}

<hr style="border:none;border-top:1px solid #ddd;margin:20px 0">
<p style="font-size:13px">
    📈 <a href="{report_url}" style="color:#3498db">查看完整报告</a>
    &nbsp;&nbsp;|&nbsp;&nbsp;
    🔍 <a href="{run_url}" style="color:#3498db">查看执行详情</a>
</p>
<p style="color:#999;font-size:11px;margin-top:16px">
    此邮件由 GitHub Actions 自动发送 · {repo} · {ref}
</p>

</body>
</html>"""

    return html


def main():
    parser = argparse.ArgumentParser(description="生成邮件通知报告")
    parser.add_argument("--status", type=str, default="success",
                        help="执行状态: success/failure/cancelled")
    parser.add_argument("--repo", type=str, default="",
                        help="仓库名")
    parser.add_argument("--ref", type=str, default="",
                        help="分支")
    parser.add_argument("--run-url", type=str, default="",
                        help="Action Run URL")
    args = parser.parse_args()

    html = generate_email_report(
        status=args.status,
        repo=args.repo,
        ref=args.ref,
        run_url=args.run_url,
    )

    output_path = "email_report.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"邮件报告已生成: {output_path}")


if __name__ == "__main__":
    main()