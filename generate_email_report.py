#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成邮件通知报告 (generate_email_report.py)

读取策略运行结果，生成 email_report.html 供邮件通知使用。

数据源：
  - reports.json            → 选股数量概览
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

HERE = os.path.dirname(os.path.abspath(__file__))


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


def generate_sentiment_section(state, latest_date=""):
    """生成情绪反弹策略 HTML（仅在 BUY 发生当天给出买入提醒）"""
    if not state:
        return "<p>暂无情绪反弹策略数据</p>"

    position = state.get("position", 0)
    level = state.get("investment_level_idx", 0)
    trades = state.get("trades", [])

    # 默认投资阶梯（与 sentiment_rebound_strategy.py 保持一致）
    investment_levels = [2000, 4000, 8000, 16000]
    next_level_idx = min(level, len(investment_levels) - 1)
    next_amount = investment_levels[next_level_idx]
    next_level = next_level_idx + 1

    # 最近一笔交易
    last_trade = trades[-1] if trades else None
    last_trade_str = "无交易记录"
    buy_alert = ""
    if last_trade:
        action = last_trade.get("action", "")
        date = last_trade.get("date", "")
        reason = last_trade.get("reason", "")
        price = last_trade.get("price", "")
        if price:
            last_trade_str = f"{date} {action} @ {price}（{reason}）"
        else:
            last_trade_str = f"{date} {action}（{reason}）"

        # 仅在 BUY 发生当天（即交易日期等于报告最新日期）给出醒目提醒
        if action == "BUY" and date == latest_date:
            buy_alert = f"""
        <tr>
            <td colspan="2" style="padding:12px;background:#fff3cd;border:2px solid #ff9800;border-radius:6px;color:#e65100;font-size:15px;font-weight:bold;text-align:center">
                ⚠️ 今日触发买入 · 明日请继续买入 ¥{next_amount:,}（第 {next_level} 级）· 请按计划执行
            </td>
        </tr>"""

    position_str = f"{position} 份" if position > 0 else "空仓"

    return f"""
    <table style="border-collapse:collapse;font-size:13px;margin-bottom:16px;width:100%">
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
        {buy_alert}
    </table>"""


def generate_multi_indicator_hint_section(hints, etf_map=None):
    """生成策略3超阈值行业提示板块 HTML（不含 h3 标题）。

    除行业与入选数量外，附加该行业对应的行业 ETF（名称 + 代码），
    用于在出现信号时提醒用户具体可关注的相关 ETF。

    Args:
        hints: 提示清单 list[dict]，每条含 {industry, count}
        etf_map: 行业→ETF 映射 dict（来自 backtest_industry_etf_config.json）

    Returns:
        str: HTML 片段字符串。
    """
    if etf_map is None:
        etf_map = load_backtest_etf_map()

    if not hints:
        return '<p style="color:#999;font-size:13px">暂无超阈值行业</p>'

    rows = ""
    for h in hints:
        industry = escape_html(h.get("industry", ""))
        count = h.get("count", 0)
        etf_info = etf_map.get(industry, {})
        etf_name = etf_info.get("name", "")
        ts_code = etf_info.get("ts_code", "")
        if etf_name and ts_code:
            etf_cell = f"{escape_html(etf_name)} <span style='color:#888'>({ts_code})</span>"
        elif etf_name:
            etf_cell = escape_html(etf_name)
        else:
            etf_cell = "<span style='color:#bbb'>暂无对应ETF</span>"
        rows += f"""
        <tr>
            <td style="padding:6px 8px;border:1px solid #ddd;text-align:left">{industry}</td>
            <td style="padding:6px 8px;border:1px solid #ddd;text-align:right">{count}</td>
            <td style="padding:6px 8px;border:1px solid #ddd;text-align:left">{etf_cell}</td>
        </tr>"""

    return f"""
    <table style="border-collapse:collapse;width:100%;font-size:13px;margin-bottom:16px">
        <tr style="background:#f5f5f5">
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">行业</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:right">入选数量</th>
            <th style="padding:6px 8px;border:1px solid #ddd;text-align:left">相关行业ETF</th>
        </tr>
        {rows}
    </table>"""


def load_backtest_etf_map():
    """加载行业→ETF映射配置，供邮件提醒展示相关ETF。"""
    try:
        cfg_path = os.path.join(HERE, "backtest_industry_etf_config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # 配置结构：{"说明": ..., "industry_etf_map": {行业: {...}}}
            if isinstance(cfg, dict) and "industry_etf_map" in cfg:
                return cfg["industry_etf_map"]
            return cfg
    except Exception as e:
        logging.warning("加载行业ETF映射失败: %s", e)
    return {}


def generate_backtest_c_section(start_date: str, end_date: str):
    """生成行业ETF回测板块（策略C：no-repeat + 知行多空过滤）。

    Args:
        start_date: 回测起始日 YYYYMMDD
        end_date: 回测结束日 YYYYMMDD

    Returns:
        str: HTML 片段；失败或无数据时返回空字符串/提示。
    """
    try:
        import backtest_industry_etf as bie
        import pandas as pd
    except Exception as e:
        logging.warning("导入 backtest_industry_etf 失败: %s", e)
        return ""

    try:
        industry_etf_map = bie.load_backtest_config(bie.DEFAULT_CONFIG_PATH)
        if not industry_etf_map:
            return '<p style="color:#999;font-size:13px">行业ETF回测：未找到行业→ETF映射配置</p>'

        signals = bie.load_hints(start_date, end_date)
        if signals.empty:
            return (f'<p style="color:#999;font-size:13px">行业ETF回测（知行多空过滤）：'
                    f'{start_date}~{end_date} 区间内无超阈值信号</p>')

        trades = bie.run_backtest(
            signals, industry_etf_map, start_date, end_date,
            no_repeat=True, filter_zhixing_ok=True,
        )
        if trades.empty:
            return (f'<p style="color:#999;font-size:13px">行业ETF回测（知行多空过滤）：'
                    f'{start_date}~{end_date} 区间内无可交易标的</p>')

        stats = bie.compute_stats(trades)

        rows = ""
        for day in [1, 3, 5, 10]:
            s = stats.get(day)
            if not s:
                continue
            rows += f"""
            <tr>
                <td style="padding:6px 8px;border:1px solid #ddd">T+{day}</td>
                <td style="padding:6px 8px;border:1px solid #ddd">{s['count']}</td>
                <td style="padding:6px 8px;border:1px solid #ddd">{s['win_rate']:.1f}%</td>
                <td style="padding:6px 8px;border:1px solid #ddd;color:{'#27ae60' if s['avg_return']>=0 else '#e74c3c'}">{s['avg_return']:+.2f}%</td>
                <td style="padding:6px 8px;border:1px solid #ddd">{s['max_return']:+.2f}%</td>
                <td style="padding:6px 8px;border:1px solid #ddd">{s['min_return']:+.2f}%</td>
            </tr>"""

        latest = trades.sort_values("signal_date").tail(3)
        latest_items = ""
        for _, r in latest.iterrows():
            t1 = r.get("return_t1", float("nan"))
            t1_str = f"{t1:+.2f}%" if pd.notna(t1) else "—"
            latest_items += (
                f"<li>{r['signal_date']} 信号 → {r['industry']} / "
                f"{r['etf_name']}（买入日 {r['buy_date']}，T+1 {t1_str}）</li>"
            )

        return f"""
        <p style="color:#666;font-size:13px;margin:4px 0">
            规则：行业超阈值信号 → 次日开盘买入对应行业ETF；<b>同一ETF持仓期只首仓</b>，
            且买入日需 <b>中期线&gt;多空线 且 收盘≥多空线</b>（仅参与多头趋势）。区间 {start_date}~{end_date}。
        </p>
        <table style="border-collapse:collapse;width:100%;max-width:680px;font-size:13px;margin-bottom:12px">
            <tr style="background:#f5f5f5">
                <th style="padding:6px 8px;border:1px solid #ddd">持有期</th>
                <th style="padding:6px 8px;border:1px solid #ddd">笔数</th>
                <th style="padding:6px 8px;border:1px solid #ddd">胜率</th>
                <th style="padding:6px 8px;border:1px solid #ddd">平均收益</th>
                <th style="padding:6px 8px;border:1px solid #ddd">最大</th>
                <th style="padding:6px 8px;border:1px solid #ddd">最小</th>
            </tr>
            {rows}
        </table>
        <p style="color:#666;font-size:13px;margin:6px 0 2px"><b>最近信号：</b></p>
        <ul style="color:#444;font-size:13px;margin:2px 0">{latest_items}</ul>
        """
    except Exception as e:
        logging.warning("生成行业ETF回测板块失败: %s", e)
        return ""


def generate_email_report(status="success", repo="", ref="", run_url=""):
    """生成完整邮件 HTML"""
    # 加载数据
    reports = load_reports_json()
    strategy_state = load_strategy_state()
    dca_summary = load_dca_summary()
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
    </tr>
</table>

<h3 style="color:#2c3e50;border-left:4px solid #9b59b6;padding-left:10px;margin-top:24px">
    📊 情绪反弹策略
</h3>
{generate_sentiment_section(strategy_state, latest_date)}

<h3 style="color:#2c3e50;border-left:4px solid #e67e22;padding-left:10px;margin-top:24px">
    🏷️ 策略3 超阈值行业提示
</h3>
{generate_multi_indicator_hint_section(multi_hints, load_backtest_etf_map())}

<h3 style="color:#2c3e50;border-left:4px solid #27ae60;padding-left:10px;margin-top:24px">
    💰 ETF 定投策略汇总
</h3>
{generate_dca_section(dca_summary)}

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