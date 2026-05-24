#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
消融实验网页报告生成器 (web_dashboard.py)

从 backtest_ablation.py 独立出来的网页相关函数，
负责生成交互式 HTML 报告。
"""

import os
import json
from datetime import datetime

MARKET_PHASES = [
    {"label": "多头1", "start": "20250625", "end": "20250903"},
    {"label": "多头2", "start": "20260105", "end": "20260202"},
    {"label": "多头3", "start": "20260408", "end": "20260522"},
    {"label": "空头1", "start": "20250904", "end": "20251231"},
    {"label": "空头2", "start": "20260203", "end": "20260407"},
]


def generate_html_report(part_a_stats, part_b_stats, part_a_trades, part_b_trades, hold_days, total_days, output_path, part_c_stats=None, part_c_trades=None, part_d_stats=None, part_d_trades=None):
    def stats_to_rows(stats_list):
        rows = []
        for s in stats_list:
            rows.append({
                "实验组": s.get("实验组", ""),
                "样本量": s.get("样本量", 0),
                "平均涨幅": s.get("平均涨幅", 0),
                "中位涨幅": s.get("中位涨幅", 0),
                "胜率": s.get("胜率", 0),
                "大胜率": s.get("大胜率(>3%)", 0),
                "盈亏比": s.get("盈亏比", 0),
                "最大涨幅": s.get("最大涨幅", 0),
                "最大跌幅": s.get("最大跌幅", 0),
            })
        return rows

    all_parts = []
    if part_a_stats:
        for r in stats_to_rows(part_a_stats):
            r["_part"] = "A"
            all_parts.append(r)
    if part_b_stats:
        for r in stats_to_rows(part_b_stats):
            r["_part"] = "B"
            all_parts.append(r)
    if part_c_stats:
        for r in stats_to_rows(part_c_stats):
            r["_part"] = "C"
            all_parts.append(r)
    if part_d_stats:
        for r in stats_to_rows(part_d_stats):
            r["_part"] = "D"
            r["市场阶段"] = r.get("市场阶段", "")
            r["阶段类型"] = r.get("阶段类型", "")
            all_parts.append(r)

    def trades_phase_data(trades_map, prefix):
        phase_results = []
        for label, trades_df in trades_map.items():
            if not label.startswith(prefix) or trades_df.empty:
                continue
            gains = trades_df["gain_pct"]
            for phase in MARKET_PHASES:
                phase_label = phase["label"]
                phase_type = "多头" if "多头" in phase_label else "空头"
                date_col = "signal_date" if "signal_date" in trades_df.columns else "trade_date" if "trade_date" in trades_df.columns else None
                if date_col:
                    phase_mask = (trades_df[date_col] >= phase["start"]) & (trades_df[date_col] <= phase["end"])
                    phase_gains = gains[phase_mask]
                else:
                    continue
                if len(phase_gains) == 0:
                    continue
                wins = phase_gains[phase_gains > 0]
                losses = phase_gains[phase_gains < 0]
                avg_w = wins.mean() if len(wins) > 0 else 0
                avg_l = abs(losses.mean()) if len(losses) > 0 else 0.01
                phase_results.append({
                    "实验组": label,
                    "市场阶段": phase_label,
                    "阶段类型": phase_type,
                    "样本量": int(len(phase_gains)),
                    "平均涨幅": round(float(phase_gains.mean()), 2),
                    "胜率": round(float((phase_gains > 0).mean() * 100), 1),
                    "盈亏比": round(float(avg_w / avg_l), 2) if avg_l > 0 else 0,
                })
        return phase_results

    phase_data = []
    phase_data += trades_phase_data(part_a_trades, "A") if part_a_trades else []
    phase_data += trades_phase_data(part_b_trades, "B") if part_b_trades else []
    phase_data += trades_phase_data(part_c_trades, "C") if part_c_trades else []

    all_json = json.dumps(all_parts, ensure_ascii=False, default=str)
    phase_json = json.dumps(phase_data, ensure_ascii=False, default=str)
    hold_d = hold_days
    total_d = total_days
    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    full_html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>选股条件消融实验报告</title>
<style>
:root {
  --bg: #0f1117; --card: #1a1d27; --border: #2a2d3a; --text: #e4e6f0;
  --muted: #8b8fa3; --accent: #6c5ce7; --green: #00b894; --red: #e74c3c;
  --blue: #0984e3; --orange: #fdcb6e; --pink: #fd79a8;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'SF Pro Display', 'Microsoft YaHei', -apple-system, sans-serif; background: var(--bg); color: var(--text); line-height:1.6; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
h1 { font-size:28px; font-weight:700; margin-bottom:8px; background: linear-gradient(135deg, var(--accent), var(--pink)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
.meta { color: var(--muted); font-size:14px; margin-bottom:24px; }
.tabs { display:flex; gap:4px; margin-bottom:20px; background:var(--card); border-radius:12px; padding:4px; border:1px solid var(--border); }
.tab { padding:10px 20px; border-radius:8px; cursor:pointer; font-size:14px; font-weight:500; transition:all .2s; color:var(--muted); user-select:none; }
.tab:hover { color:var(--text); background:rgba(108,92,231,0.1); }
.tab.active { background:var(--accent); color:#fff; }
.card { background:var(--card); border-radius:12px; border:1px solid var(--border); overflow:hidden; margin-bottom:20px; }
.toolbar { display:flex; gap:8px; padding:16px; border-bottom:1px solid var(--border); align-items:center; flex-wrap:wrap; }
.toolbar label { font-size:13px; color:var(--muted); }
.toolbar select, .toolbar input { background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:6px; padding:6px 10px; font-size:13px; }
.badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
.badge-a { background:rgba(9,132,227,0.2); color:var(--blue); }
.badge-b { background:rgba(253,203,110,0.2); color:var(--orange); }
.badge-c { background:rgba(108,92,231,0.2); color:var(--accent); }
.badge-d { background:rgba(253,121,168,0.2); color:var(--pink); }
.badge-bull { background:rgba(0,184,148,0.2); color:var(--green); }
.badge-bear { background:rgba(231,76,60,0.2); color:var(--red); }
table { width:100%; border-collapse:collapse; font-size:13px; }
thead th { background:var(--bg); color:var(--muted); font-weight:600; text-transform:uppercase; font-size:11px; letter-spacing:0.5px; padding:10px 12px; text-align:left; cursor:pointer; user-select:none; position:sticky; top:0; z-index:1; white-space:nowrap; transition:color .2s; }
thead th:hover { color:var(--text); }
thead th .sort-icon { margin-left:4px; font-size:10px; opacity:0.4; }
thead th.sorted-asc .sort-icon, thead th.sorted-desc .sort-icon { opacity:1; color:var(--accent); }
tbody tr { border-bottom:1px solid var(--border); transition:background .15s; }
tbody tr:hover { background:rgba(108,92,231,0.06); }
tbody td { padding:8px 12px; white-space:nowrap; }
.val-positive { color: var(--green); }
.val-negative { color: var(--red); }
.val-neutral { color: var(--muted); }
.highlight-row { background: rgba(0,184,148,0.08) !important; }
.table-wrap { max-height: 600px; overflow-y:auto; }
.summary-cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px,1fr)); gap:12px; margin-bottom:20px; }
.scard { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px; }
.scard .label { font-size:12px; color:var(--muted); margin-bottom:4px; }
.scard .value { font-size:24px; font-weight:700; }
.chart-row { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:20px; }
.chart-card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; }
.chart-card h3 { font-size:14px; color:var(--muted); margin-bottom:12px; font-weight:500; }
.bar-container { display:flex; align-items:center; gap:8px; margin-bottom:6px; font-size:12px; }
.bar-label { width:120px; text-align:right; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bar-track { flex:1; height:20px; background:var(--bg); border-radius:4px; overflow:hidden; position:relative; }
.bar-fill { height:100%; border-radius:4px; transition:width .3s; display:flex; align-items:center; justify-content:flex-end; padding-right:6px; font-size:11px; font-weight:600; color:#fff; min-width:30px; }
.filter-active { background: var(--accent) !important; color: #fff !important; }
</style>
</head>
<body>
<div class="container">
<h1>选股条件消融实验报告</h1>
<p class="meta">持有 """ + str(hold_d) + r""" 天 | """ + str(total_d) + r""" 个交易日 | """ + gen_time + r""" | 次日开盘价买入</p>

<div id="summary-cards" class="summary-cards"></div>

<div class="tabs" id="main-tabs">
  <div class="tab active" data-tab="overview">总览</div>
  <div class="tab" data-tab="ranking">综合排名</div>
  <div class="tab" data-tab="phase">择时分析</div>
  <div class="tab" data-tab="detail">详细数据</div>
</div>

<div id="tab-overview">
  <div class="chart-row" id="chart-row"></div>
  <div class="card">
    <div class="toolbar">
      <label>Part:</label>
      <select id="ov-part"><option value="">全部</option><option value="A">Part A</option><option value="B">Part B</option><option value="C" selected>Part C</option></select>
      <label>最低样本:</label>
      <input type="number" id="ov-min-sample" value="50" style="width:70px">
      <label>最低胜率%:</label>
      <input type="number" id="ov-min-win" value="0" style="width:70px">
    </div>
    <div class="table-wrap" style="max-height:500px">
      <table id="ov-table"><thead></thead><tbody></tbody></table>
    </div>
  </div>
</div>

<div id="tab-ranking" style="display:none">
  <div class="card">
    <div class="toolbar">
      <label>排名方式:</label>
      <select id="rank-mode">
        <option value="composite">综合得分 (0.4*胜率 + 10*涨幅 + 5*盈亏比)</option>
        <option value="winrate">胜率优先</option>
        <option value="gain">平均涨幅优先</option>
        <option value="plr">盈亏比优先</option>
        <option value="sample">样本量优先</option>
      </select>
      <label>最低样本:</label>
      <input type="number" id="rank-min-sample" value="50" style="width:70px">
    </div>
    <div class="table-wrap" style="max-height:700px">
      <table id="rank-table"><thead></thead><tbody></tbody></table>
    </div>
  </div>
</div>

<div id="tab-phase" style="display:none">
  <div class="card">
    <div class="toolbar">
      <label>市场阶段:</label>
      <select id="phase-filter"><option value="">全部</option><option value="多头">多头</option><option value="空头">空头</option></select>
      <label>最低样本:</label>
      <input type="number" id="phase-min-sample" value="50" style="width:70px">
    </div>
    <div class="table-wrap" style="max-height:700px">
      <table id="phase-table"><thead></thead><tbody></tbody></table>
    </div>
  </div>
</div>

<div id="tab-detail" style="display:none">
  <div class="card">
    <div class="toolbar">
      <label>Part:</label>
      <select id="det-part"><option value="">全部</option><option value="A">Part A</option><option value="B">Part B</option><option value="C">Part C</option><option value="D">Part D</option></select>
    </div>
    <div class="table-wrap" style="max-height:700px">
      <table id="det-table"><thead></thead><tbody></tbody></table>
    </div>
  </div>
</div>
</div>

<script>
const DATA = """ + all_json + r""";
const PHASE = """ + phase_json + r""";

const COLS = [
  {key:"实验组",label:"实验组",type:"string"},
  {key:"样本量",label:"样本量",type:"number"},
  {key:"胜率",label:"胜率%",type:"number",fmt:v=>v?.toFixed(1)+"%",cls:v=>v>=65?"val-positive":v>=50?"val-neutral":"val-negative"},
  {key:"平均涨幅",label:"平均涨幅%",type:"number",fmt:v=>v?.toFixed(2)+"%",cls:v=>v>=1?"val-positive":v>=0?"val-neutral":"val-negative"},
  {key:"中位涨幅",label:"中位涨幅%",type:"number",fmt:v=>v?.toFixed(2)+"%"},
  {key:"大胜率",label:"大胜率(>3%)%",type:"number",fmt:v=>v?.toFixed(1)+"%"},
  {key:"盈亏比",label:"盈亏比",type:"number",fmt:v=>v?.toFixed(2),cls:v=>v>=1.5?"val-positive":v>=1?"val-neutral":"val-negative"},
  {key:"最大涨幅",label:"最大涨幅%",type:"number",fmt:v=>v?.toFixed(2)+"%"},
  {key:"最大跌幅",label:"最大跌幅%",type:"number",fmt:v=>v?.toFixed(2)+"%"},
];

const PHASE_COLS = [
  {key:"实验组",label:"实验组",type:"string"},
  {key:"市场阶段",label:"市场阶段",type:"string"},
  {key:"阶段类型",label:"类型",type:"string"},
  {key:"样本量",label:"样本量",type:"number"},
  {key:"胜率",label:"胜率%",type:"number",fmt:v=>v?.toFixed(1)+"%"},
  {key:"平均涨幅",label:"平均涨幅%",type:"number",fmt:v=>v?.toFixed(2)+"%"},
  {key:"盈亏比",label:"盈亏比",type:"number",fmt:v=>v?.toFixed(2)},
];

function badge(part){ const m={A:"badge-a",B:"badge-b",C:"badge-c",D:"badge-d"}; return `<span class="badge ${m[part]||""}">${part}</span>`; }
function phaseBadge(t){ return t==="多头"?'<span class="badge badge-bull">多头</span>':'<span class="badge badge-bear">空头</span>'; }

function renderTable(tableId, cols, rows, sortCol, sortDir){
  const thead = document.querySelector(`#${tableId} thead`);
  const tbody = document.querySelector(`#${tableId} tbody`);
  let html = "<tr>";
  html += `<th>Part</th>`;
  cols.forEach(c => {
    const cls = sortCol===c.key ? (sortDir==="asc"?"sorted-asc":"sorted-desc") : "";
    const icon = sortCol===c.key ? (sortDir==="asc"?"▲":"▼") : "⇅";
    html += `<th class="${cls}" onclick="sortTable('${tableId}','${c.key}')">${c.label}<span class="sort-icon">${icon}</span></th>`;
  });
  html += "</tr>";
  thead.innerHTML = html;

  html = "";
  rows.forEach(r => {
    const isTop = sortCol==="胜率" && r["胜率"]>=60 || sortCol==="平均涨幅" && r["平均涨幅"]>=1;
    html += `<tr class="${isTop?"highlight-row":""}">`;
    html += `<td>${badge(r._part||"")}</td>`;
    cols.forEach(c => {
      let v = r[c.key];
      const fmt = c.fmt || (x=>x??"");
      const clsFn = c.cls || (()=>"");
      const cls = typeof clsFn === "function" ? clsFn(v) : "";
      html += `<td class="${cls}">${fmt(v)}</td>`;
    });
    html += "</tr>";
  });
  tbody.innerHTML = html;
}

const sortState = {};
function sortTable(tableId, col){
  if(!sortState[tableId]) sortState[tableId]={col:null,dir:"desc"};
  if(sortState[tableId].col===col) sortState[tableId].dir = sortState[tableId].dir==="asc"?"desc":"asc";
  else { sortState[tableId].col=col; sortState[tableId].dir="desc"; }
  refreshCurrentTab();
}

function getFilteredData(partFilter, minSample, minWin){
  return DATA.filter(r => {
    if(partFilter && r._part !== partFilter) return false;
    if(r["样本量"] < (minSample||0)) return false;
    if(r["胜率"] < (minWin||0)) return false;
    return true;
  });
}

function sortRows(rows, col, dir){
  if(!col) return rows;
  return [...rows].sort((a,b) => {
    let va = a[col], vb = b[col];
    if(typeof va === "string") return dir==="asc" ? va.localeCompare(vb) : vb.localeCompare(va);
    return dir==="asc" ? (va||0)-(vb||0) : (vb||0)-(va||0);
  });
}

function refreshCurrentTab(){
  const active = document.querySelector(".tab.active").dataset.tab;
  if(active==="overview") refreshOverview();
  else if(active==="ranking") refreshRanking();
  else if(active==="phase") refreshPhase();
  else if(active==="detail") refreshDetail();
}

function refreshOverview(){
  const part = document.getElementById("ov-part").value;
  const minS = parseInt(document.getElementById("ov-min-sample").value)||0;
  const minW = parseFloat(document.getElementById("ov-min-win").value)||0;
  let rows = getFilteredData(part, minS, minW);
  const s = sortState["ov-table"]||{col:"胜率",dir:"desc"};
  rows = sortRows(rows, s.col, s.dir);
  renderTable("ov-table", COLS, rows, s.col, s.dir);
  renderCharts(rows);
}

function refreshRanking(){
  const mode = document.getElementById("rank-mode").value;
  const minS = parseInt(document.getElementById("rank-min-sample").value)||0;
  let rows = DATA.filter(r => r["样本量"] >= minS);
  if(mode==="composite") rows.sort((a,b) => (0.4*(b["胜率"]||0)+10*(b["平均涨幅"]||0)+5*(b["盈亏比"]||0)) - (0.4*(a["胜率"]||0)+10*(a["平均涨幅"]||0)+5*(a["盈亏比"]||0)));
  else if(mode==="winrate") rows.sort((a,b)=>(b["胜率"]||0)-(a["胜率"]||0));
  else if(mode==="gain") rows.sort((a,b)=>(b["平均涨幅"]||0)-(a["平均涨幅"]||0));
  else if(mode==="plr") rows.sort((a,b)=>(b["盈亏比"]||0)-(a["盈亏比"]||0));
  else rows.sort((a,b)=>(b["样本量"]||0)-(a["样本量"]||0));
  renderTable("rank-table", COLS, rows, null, null);
  document.querySelectorAll("#rank-table thead th").forEach(th => th.onclick = null);
  document.querySelectorAll("#rank-table thead th").forEach((th,i) => {
    th.style.cursor = "default";
    th.querySelector(".sort-icon") && (th.querySelector(".sort-icon").textContent = "");
  });
}

function refreshPhase(){
  const pf = document.getElementById("phase-filter").value;
  const minS = parseInt(document.getElementById("phase-min-sample").value)||0;
  let rows = PHASE.filter(r => {
    if(pf && r["阶段类型"]!==pf) return false;
    if(r["样本量"] < minS) return false;
    return true;
  });
  const s = sortState["phase-table"]||{col:"胜率",dir:"desc"};
  rows = sortRows(rows, s.col, s.dir);
  renderTable("phase-table", PHASE_COLS, rows, s.col, s.dir);
  document.querySelectorAll("#phase-table tbody td").forEach(td => {
    if(td.textContent==="多头"||td.textContent==="空头") td.innerHTML = phaseBadge(td.textContent);
  });
}

function refreshDetail(){
  const part = document.getElementById("det-part").value;
  let rows = part ? DATA.filter(r=>r._part===part) : DATA;
  const s = sortState["det-table"]||{col:null,dir:"desc"};
  if(s.col) rows = sortRows(rows, s.col, s.dir);
  renderTable("det-table", COLS, rows, s.col, s.dir);
}

function renderCharts(rows){
  const top = rows.slice(0, 20);
  const maxWin = Math.max(...top.map(r=>r["胜率"]||0), 1);
  const maxGain = Math.max(...top.map(r=>Math.abs(r["平均涨幅"]||0)), 0.1);
  const maxSample = Math.max(...top.map(r=>r["样本量"]||0), 1);
  let html = `<div class="chart-card"><h3>TOP 20 胜率 & 平均涨幅</h3>`;
  top.forEach(r => {
    const w = r["胜率"]||0;
    const g = r["平均涨幅"]||0;
    const wPct = (w/maxWin*100);
    const gPct = (Math.abs(g)/maxGain*100);
    const gColor = g>=0?"var(--green)":"var(--red)";
    html += `<div class="bar-container"><span class="bar-label" title="${r["实验组"]}">${r["实验组"].replace(/^[A-D]\d+-/,"")}</span><div class="bar-track"><div class="bar-fill" style="width:${wPct}%;background:var(--accent)">${w.toFixed(1)}</div></div><div class="bar-track" style="width:80px"><div class="bar-fill" style="width:${gPct}%;background:${gColor}">${g.toFixed(2)}</div></div></div>`;
  });
  html += "</div>";

  html += `<div class="chart-card"><h3>TOP 20 样本量 & 盈亏比</h3>`;
  top.forEach(r => {
    const s = r["样本量"]||0;
    const p = r["盈亏比"]||0;
    const sPct = (s/maxSample*100);
    const pMax = Math.max(...top.map(r=>r["盈亏比"]||0), 0.1);
    const pPct = (p/pMax*100);
    const pColor = p>=1.5?"var(--green)":p>=1?"var(--orange)":"var(--red)";
    html += `<div class="bar-container"><span class="bar-label" title="${r["实验组"]}">${r["实验组"].replace(/^[A-D]\d+-/,"")}</span><div class="bar-track"><div class="bar-fill" style="width:${sPct}%;background:var(--blue)">${s}</div></div><div class="bar-track" style="width:80px"><div class="bar-fill" style="width:${pPct}%;background:${pColor}">${p.toFixed(2)}</div></div></div>`;
  });
  html += "</div>";

  document.getElementById("chart-row").innerHTML = html;
}

function renderSummary(){
  const cRows = DATA.filter(r=>r._part==="C");
  const best = cRows.reduce((a,b)=>(0.4*(b["胜率"]||0)+10*(b["平均涨幅"]||0)+5*(b["盈亏比"]||0))>(0.4*(a["胜率"]||0)+10*(a["平均涨幅"]||0)+5*(a["盈亏比"]||0))?b:a, cRows[0]||{});
  const bestWin = cRows.reduce((a,b)=>(b["胜率"]||0)>(a["胜率"]||0)?b:a, cRows[0]||{});
  const bestGain = cRows.reduce((a,b)=>(b["平均涨幅"]||0)>(a["平均涨幅"]||0)?b:a, cRows[0]||{});
  document.getElementById("summary-cards").innerHTML = `
    <div class="scard"><div class="label">总实验组数</div><div class="value">${DATA.length}</div></div>
    <div class="scard"><div class="label">最高胜率</div><div class="value" style="color:var(--green)">${(bestWin["胜率"]||0).toFixed(1)}%</div><div style="font-size:12px;color:var(--muted);margin-top:4px">${bestWin["实验组"]||""}</div></div>
    <div class="scard"><div class="label">最高平均涨幅</div><div class="value" style="color:var(--blue)">${(bestGain["平均涨幅"]||0).toFixed(2)}%</div><div style="font-size:12px;color:var(--muted);margin-top:4px">${bestGain["实验组"]||""}</div></div>
    <div class="scard"><div class="label">综合最优</div><div class="value" style="color:var(--accent)">${(best["胜率"]||0).toFixed(1)}%</div><div style="font-size:12px;color:var(--muted);margin-top:4px">${best["实验组"]||""} | 涨幅${(best["平均涨幅"]||0).toFixed(2)}%</div></div>
  `;
}

document.querySelectorAll(".tab").forEach(t => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    document.querySelectorAll("[id^=tab-]").forEach(p => p.style.display="none");
    document.getElementById("tab-"+t.dataset.tab).style.display="";
    refreshCurrentTab();
  });
});

["ov-part","ov-min-sample","ov-min-win"].forEach(id => document.getElementById(id).addEventListener("change", refreshOverview));
["rank-mode","rank-min-sample"].forEach(id => document.getElementById(id).addEventListener("change", refreshRanking));
["phase-filter","phase-min-sample"].forEach(id => document.getElementById(id).addEventListener("change", refreshPhase));
document.getElementById("det-part").addEventListener("change", refreshDetail);

renderSummary();
sortState["ov-table"] = {col:"胜率",dir:"desc"};
refreshOverview();
</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"\nHTML report generated: {output_path}")
