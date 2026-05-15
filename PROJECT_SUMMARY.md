# 股票量化选股系统 (wbzq) - 项目综合文档

## 一、项目概述

**wbzq** 是一个基于多因子策略的 A 股量化选股与回测系统，使用 Python 开发。系统通过 17 项技术指标联合筛选，识别出符合"阶梯放量+底部暴力K+异动"等特征的股票，同时集成了情绪反弹 ETF 交易策略，并通过 GitHub Actions 实现每日自动运行与报告部署。

### 核心能力

| 能力 | 说明 |
|------|------|
| 多因子选股 | 17 项技术指标联合筛选，覆盖趋势、量能、K线形态、出货信号等维度 |
| 情绪反弹策略 | 基于 J13 数量分位数 + 知行砖形图的 ETF 倍投策略 |
| 回测验证 | 支持单股调试、批量验证、3 日持有回测 |
| 可视化报告 | 交互式 HTML 报告（K线图、成交额、KDJ、多空指标） |
| 自动化运行 | GitHub Actions 定时执行 + GitHub Pages 在线查看 |

---

## 二、项目结构

```
wbzq/
├── main_par2.py                    # 主程序：选股策略核心（~1950行）
├── sentiment_rebound_strategy.py   # 情绪反弹策略模块
├── config.py                       # 策略配置中心
├── data_manager.py                 # 数据管理模块（Tushare + SQLite）
├── batch_validate.py               # 批量验证工具
├── generate_stock_html.py          # 选股结果 HTML 报告生成
├── generate_trend_html.py          # 趋势图 HTML 生成
├── generate_reports_json.py        # GitHub Pages 索引生成
├── migrate_to_new_repo.py          # 仓库迁移工具
├── requirements.txt                # Python 依赖
├── validate_list.csv               # 验证股票列表
├── strategy_state.json             # 情绪策略持久化状态
├── .env.example                    # 环境变量模板
├── .github/workflows/
│   ├── stock-strategy.yml          # 股票策略定时执行
│   └── deploy-pages.yml            # GitHub Pages 部署
├── html/YYYYMMDD/                  # 按日期组织的 HTML 报告
│   ├── stock_selection_YYYYMMDD.html
│   ├── industry_total_amount_trend.html
│   ├── first_j13_step_daily_count.html
│   └── sentiment_rebound_strategy.html
├── test/                           # 测试用例
│   ├── test_fund_563300.py
│   ├── test_multi_day_strategy.py
│   ├── test_sentiment_integration.py
│   ├── test_sentiment_strategy.py
│   └── test_zhixing_brick_real.py
└── logs/                           # 验证日志目录
```

---

## 三、核心模块详解

### 3.1 main_par2.py — 选股策略核心

主程序包含完整的数据获取、指标计算、策略筛选、回测、可视化和调试流程。

#### 主流程

```
1. 解析命令行参数
2. 准备交易日期范围
3. 获取全市场股票因子数据
4. 计算辅助字段（上穿60日线、缩量、跳空、K线形态、振幅等）
5. 应用策略标记（阶梯放量、放量、异动、底部暴力K、出货信号等）
6. 计算趋势指标（MA60方向）
7. 计算知行砖形图指标
8. 计算成交额排名
9. 应用最终筛选条件
10. 打印结果 & 生成 HTML 报告
11. 回测（可选）
12. 每日统计和可视化
13. 情绪反弹策略分析
14. 调试模式（可选）
```

#### 17 项最终筛选条件

| 序号 | 条件 | 字段 | 说明 |
|------|------|------|------|
| 1 | 阶梯放量 | `first_j13_step` | J<13 且满足阶梯放量条件 |
| 2 | MACD>0 | `macd_dif_qfq` | DIF 线在零轴上方 |
| 3 | 缩量 | `shrink` | 当日成交额低于前日或前2日 |
| 4 | 无跳空 | `gap_up` | 当日最低价不高于前日最高价 |
| 5 | 收盘价>MA60 | `close_qfq > ma_qfq_60` | 价格站上60日均线 |
| 6 | MA60向上 | `ma60_upward` | 3/8/13日趋势至少2个向上 |
| 7 | K线形态可接受 | `is_acceptable_candle` | 小阳线/十字星/带下影阴线 |
| 8 | 振幅符合 | `is_amplitude_ok` | 主板≤4%，创业板/科创板≤7% |
| 9 | 周期内有异动 | `has_am_in_period` | 收集区/堆量/突破任一信号 |
| 10 | 成交额前60% | `is_amount_top30` | 当日成交额在市场前40%分位 |
| 11 | 有底部暴力K | `has_bottom_violent_k` | 周期内出现过底部暴力K线 |
| 12 | 无出货信号V1 | `~has_distribution_signal` | 无最高点放天量大阴线 |
| 13 | 无出货信号V2 | `~has_distribution_signal_v2` | 无最高点后放量下跌 |
| 14 | 无出货信号V3 | `~has_distribution_signal_v3` | 无最高点后2次+放量长阴 |
| 15 | 周期内曾放量 | `volume_surge_any` | 成交额≥前5天均值3倍 |
| 16 | 知行多空 | `zhixing_mid_duokong > zhixing_duokong` | 中期多空线>多空线 |
| 17 | 收盘价≥多空线 | `close_qfq >= zhixing_duokong` | 价格不低于多空线 |

> 另外还有隐含条件：非次新股（上市≥180天）、非ST、非北交所。

#### 核心分析函数

| 函数 | 功能 |
|------|------|
| `mark_step_vol_price()` | 阶梯放量+价升条件标记（上穿60日线后连续价随量升，J<13时触发） |
| `mark_volume_surge()` | 放量标记（成交额≥前5天均值3倍） |
| `mark_abnormal_movement()` | 异动标记（收集区/堆量建仓/突破放量三种类型） |
| `mark_bottom_violent_k()` | 底部暴力K线标记（接近MA60的放量长阳） |
| `mark_distribution_signal()` | 主力出货V1（最高点放天量大阴线） |
| `mark_distribution_signal_v2()` | 主力出货V2（最高点后放量下跌） |
| `mark_distribution_signal_v3()` | 主力出货V3（最高点后2次+放量长阴） |
| `identify_candle_pattern()` | K线形态识别（小阳线/十字星/带下影阴线） |
| `calculate_zhixing_brick_indicator()` | 知行砖形图指标计算 |
| `debug_stock_strategy_detailed()` | 单股11项条件逐项调试输出 |

#### 知行多空线计算

```
多空线 = (MA14 + MA28 + MA57 + MA114) / 4
中期多空线 = EMA(多空线, 10)  即 EMA(EMA(close,10), 10)
```

#### 知行砖形图指标

```
VAR1A = (HHV(H,4) - C) / (HHV(H,4) - LLV(L,4)) * 100 - 90
VAR2A = SMA(VAR1A, 4, 1) + 100
VAR3A = (C - LLV(L,4)) / (HHV(H,4) - LLV(L,4)) * 100
VAR4A = SMA(VAR3A, 6, 1)
VAR5A = SMA(VAR4A, 6, 1) + 100
VAR6A = VAR5A - VAR2A
砖型图 = IF(VAR6A > 4, VAR6A - 4, 0)
```

---

### 3.2 sentiment_rebound_strategy.py — 情绪反弹策略

基于 J13 数量分位数和知行砖形图的 ETF 交易策略，默认交易标的为中证2000ETF（563300.SH）。

#### 买入逻辑

| 条件 | 说明 |
|------|------|
| J13数量 > 90%分位数 | 市场极度恐慌时触发 |
| 倍投阶梯 | 2000 → 4000 → 8000 → 16000（每级翻倍） |

#### 卖出逻辑（砖型图方案）

| 方案 | 条件 | 操作 |
|------|------|------|
| 方案1 | 连续红柱达到4根 | 卖出一半 |
| 方案1 | 红柱4根后红转绿 | 全部卖出 |
| 方案2 | 未满4根红柱即红转绿 | 全部卖出 |

#### 状态持久化

策略状态（持仓、投资级别、红柱计数等）保存至 `strategy_state.json`，支持跨日连续运行。

---

### 3.3 config.py — 策略配置中心

| 配置类 | 说明 | 关键参数 |
|--------|------|----------|
| `APIConfig` | Tushare API Token（从环境变量读取） | `TUSHARE_TOKEN`, 缓存7天 |
| `StrategyThresholds` | 所有策略阈值 | 异动涨幅3.8%/7%、放量3倍、振幅4%/7%等 |
| `BacktestConfig` | 回测参数 | 持有3天、回看60天、剔除180天内次新股 |
| `ParallelConfig` | 并行参数 | 12线程、50块大小 |
| `DBConfig` | 数据库和缓存 | SQLite、7天缓存过期 |

#### 关键阈值一览

```python
COLLECT_PCT_00_60 = 3.8      # 主板异动涨幅阈值
COLLECT_PCT_OTHER = 7.0      # 创业板/科创板异动涨幅阈值
VOLUME_SURGE_RATIO = 3.0     # 放量检测倍数（前5天均值）
BODY_RATIO_THRESHOLD = 0.2   # 十字星判定（实体/振幅）
MIN_SHADOW_RATIO = 0.4       # 下影线判定（下影/振幅）
AMPLITUDE_00_60 = 4.0        # 主板振幅上限
AMPLITUDE_OTHER = 7.0        # 其他板振幅上限
VOLUME_MULTIPLIER = 1.9      # 堆量判定量比
GAP_SIZE_RATIO = 0.025       # 大跳空判定（2.5%）
RELATIVE_VOLUME_RATIO = 0.30 # 相对地量（周期最高量30%）
AMOUNT_TOP_PERCENT = 0.40    # 成交额前40%分位
```

---

### 3.4 data_manager.py — 数据管理模块

基于 Tushare API + SQLite 缓存的数据管理器，替代已不可用的 `stk_factor_pro` 接口。

#### 核心功能

| 功能 | 说明 |
|------|------|
| 原始数据获取 | `daily` + `adj_factor` + `daily_basic` 三表联合 |
| 本地计算指标 | 前复权价格、均线(5~250日)、EMA、MACD、KDJ |
| SQLite 缓存 | `stock_factors` 表存储计算结果，`UNIQUE(ts_code, trade_date)` |
| 交易日历 | 带文件缓存的交易日获取 |
| 股票基本信息 | `stock_basic_info` 表含名称、行业、上市日期 |

#### 计算字段

```
前复权价格: open_qfq, high_qfq, low_qfq, close_qfq
均线: ma_qfq_5/10/20/30/60/90/250
EMA: ema_qfq_5/10/12/13/20/26/30/60/90/250
MACD: macd_dif_qfq, macd_dea_qfq, macd_qfq
KDJ: kdj_k_qfq, kdj_d_qfq, kdj_qfq
```

---

### 3.5 batch_validate.py — 批量验证工具

从 `validate_list.csv` 读取验证列表，逐只运行 `main_par2.py --debug`，解析输出日志提取每项检查结果。

#### 输入格式

```csv
code,name,date_str,days,expect_strategy
600366.SH,宁波韵升,20250804,60,策略1
```

#### 输出文件

| 文件 | 说明 |
|------|------|
| `logs/股票代码_日期.log` | 单只股票详细调试日志 |
| `result.csv` | 验证结果汇总（通过/失败） |
| `result_detail.csv` | 每项检查的详细结果 |
| `validation_report.txt` | 完整验证报告（含统计） |

#### 11 项检查项解析

基础技术指标 → K线形态 → 振幅 → 阶梯放量策略 → 放量 → 异动 → 成交额排名 → 底部暴力K → 派发信号 → 知行多空线 → 次新股

---

### 3.6 报告生成模块

| 模块 | 生成内容 | 特性 |
|------|----------|------|
| `generate_stock_html.py` | 选股结果 HTML | K线图、成交额柱状图（倍量黄/紫标识）、KDJ指标、多空线 |
| `generate_trend_html.py` | 行业趋势 + J13趋势 HTML | Top10 行业成交额折线图、J13 每日数量趋势 |
| `generate_reports_json.py` | `reports.json` 索引 | 供 GitHub Pages 首页读取历史报告列表 |

---

## 四、选股策略算法详解

### 4.1 阶梯放量策略

```
1. 股票上穿60日线且不跳空
2. 上穿后出现连续价随量升（≥2天）
3. 下跌日成交量不是最大量
4. 跳空缺口需回补（或满足忽略条件：量能放大>3x 且价格上涨>15%）
5. J值<13 时标记为买入信号
```

### 4.2 异动检测（3种类型）

| 类型 | 条件 |
|------|------|
| 收集区异动 | 上穿60日线后，放量上涨（主板≥3.8%，其他≥7%），量>5日均量×2.4 |
| 堆量建仓 | 上穿60日线后，≥2次放量上涨（主板≥2.5%，其他≥5%），量>5日均量×1.9 |
| 突破放量 | 突破60日线时放量≥2倍且涨幅≥3% |

### 4.3 底部暴力K线

| 板块 | 实体涨幅 | MA60容差 | 放量倍数 |
|------|----------|----------|----------|
| 主板(00/60) | ≥3% | ±10% | ≥前日×2.0 |
| 创业板/科创板(30/68) | ≥6% | ±20% | ≥前日×2.0 |

### 4.4 主力出货信号

| 版本 | 逻辑 | 阴线阈值 |
|------|------|----------|
| V1 | 最高点当天放天量大阴线（成交额≥前日2倍） | 主板≥3%，其他≥6% |
| V2 | 最高点后2天成交额均值放大且累计跌幅达标 | 主板≥8%，其他≥12% |
| V3 | 最高点后出现≥2次放量长阴 | 主板≥3%，其他≥6% |

### 4.5 K线形态识别

| 形态 | 条件 | 优先级 |
|------|------|--------|
| 小阳线 | 收盘>开盘，涨跌幅∈[-2%, 2%] | 1（最佳） |
| 十字星 | 实体/振幅 < 0.2，涨跌幅∈[-2%, 2%] | 2 |
| 带下影阴线 | 收盘<开盘，下影线/振幅≥0.4，涨跌幅∈[-2%, 2%] | 3 |

---

## 五、使用方式

### 5.1 环境配置

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 Tushare Token（三选一）
# 方式1: .env 文件
echo "TUSHARE_TOKEN=你的token" > .env

# 方式2: 系统环境变量
export TUSHARE_TOKEN="你的token"  # Linux/Mac
$env:TUSHARE_TOKEN="你的token"    # Windows PowerShell

# 方式3: GitHub Secrets（CI/CD）
```

### 5.2 命令行参数

```bash
python main_par2.py [选项]

选项:
  --date YYYYMMDD     回测日期，默认今天
  --days N            回测历史天数，默认60
  --debug CODE        调试模式，传入股票代码（逗号分隔）
  --backtest          执行回测
  --hold-days N       回测持有天数，默认3
  --detailed          打印逐日持仓数据
```

### 5.3 典型用法

```bash
# 批量选股（默认今天，60天历史）
python main_par2.py

# 指定日期选股 + 回测
python main_par2.py --date 20250620 --days 60 --backtest --hold-days 3

# 单只股票调试
python main_par2.py --date 20250620 --days 60 --debug 688321.SH

# 批量验证
python batch_validate.py
```

---

## 六、GitHub Actions 自动化

### 6.1 stock-strategy.yml — 策略定时执行

| 配置项 | 值 |
|--------|-----|
| 触发时间 | 周一至周五 UTC 12:00（北京时间约21:00） |
| Python版本 | 3.10 |
| 执行命令 | `python main_par2.py --date $TODAY --backtest --hold-days 3` |
| 结果保留 | Artifacts 30天，日志7天 |
| 通知 | 邮件通知（QQ邮箱SMTP） |

### 6.2 deploy-pages.yml — Pages 自动部署

在策略工作流完成后自动触发，将报告部署到 GitHub Pages。

### 6.3 必需的 Secrets

| Secret | 说明 |
|--------|------|
| `TUSHARE_TOKEN` | Tushare API Token（必需） |
| `EMAIL_USERNAME` | 邮件通知账号 |
| `EMAIL_PASSWORD` | 邮箱SMTP授权码 |

### 6.4 访问地址

```
https://zxlin96.github.io/wbzq/
```

---

## 七、依赖库

```
numpy>=1.24.0          # 数值计算
pandas>=2.0.0          # 数据处理
pyarrow>=12.0.0        # Parquet 文件支持
tushare>=1.2.0         # 股票数据接口
plotly>=5.0.0          # 可视化图表
scikit-learn>=1.3.0    # MinMaxScaler 归一化
tabulate>=0.9.0        # 表格格式化输出
tqdm>=4.65.0           # 进度条
```

Python 版本要求：**3.10+**

---

## 八、输出文件说明

### 8.1 HTML 报告（html/YYYYMMDD/）

| 文件 | 内容 |
|------|------|
| `stock_selection_YYYYMMDD.html` | 选股结果交互式报告（K线+成交额+KDJ+多空线） |
| `industry_total_amount_trend.html` | Top10 行业成交额趋势折线图 |
| `first_j13_step_daily_count.html` | J<13 每日数量趋势图 |
| `sentiment_rebound_strategy.html` | 情绪反弹策略分析报告 |

### 8.2 验证输出

| 文件 | 内容 |
|------|------|
| `result.csv` | 验证结果汇总 |
| `result_detail.csv` | 每项检查详细结果 |
| `validation_report.txt` | 完整验证报告 |
| `logs/*.log` | 单只股票调试日志 |

### 8.3 其他输出

| 文件 | 内容 |
|------|------|
| `reports.json` | GitHub Pages 报告索引 |
| `strategy_state.json` | 情绪策略持久化状态 |
| `industry_cache.pkl` | 行业信息缓存（7天过期） |
| `stock_strategy.db` | SQLite 数据库 |

---

## 九、安全机制

### 9.1 Token 安全

- **无硬编码 Token**：所有 Token 从环境变量或 `.env` 文件读取
- **`.gitignore` 保护**：`.env`、`*_token` 文件已加入忽略列表
- **启动校验**：未设置 Token 时给出明确错误提示
- **紧急处理**：提供 `TOKEN_SECURITY_EMERGENCY.md` 指南

### 9.2 仓库迁移

提供 `migrate_to_new_repo.py` 脚本，可一键创建无历史记录的干净仓库，避免 Token 泄露风险。

---

## 十、测试

项目包含以下测试用例：

| 测试文件 | 测试内容 |
|----------|----------|
| `test_fund_563300.py` | 中证2000ETF 数据获取与砖形图计算 |
| `test_multi_day_strategy.py` | 多日策略运行测试 |
| `test_sentiment_integration.py` | 情绪策略集成测试 |
| `test_sentiment_strategy.py` | 情绪策略单元测试 |
| `test_zhixing_brick_real.py` | 知行砖形图实盘数据验证 |

---

## 十一、代码统计

| 文件 | 行数 | 功能 |
|------|------|------|
| main_par2.py | ~1950 | 主程序，选股策略核心 |
| sentiment_rebound_strategy.py | ~300 | 情绪反弹策略 |
| config.py | 118 | 策略配置中心 |
| data_manager.py | ~200 | 数据管理模块 |
| batch_validate.py | ~300 | 批量验证工具 |
| generate_stock_html.py | ~400 | HTML 报告生成 |
| generate_trend_html.py | ~200 | 趋势图生成 |
| generate_reports_json.py | ~100 | 索引生成 |

**总计**：约 3500+ 行 Python 代码

---

## 十二、更新日志

| 时间 | 更新内容 |
|------|----------|
| 2024-12 | 初始版本发布 |
| 2025-01 | 添加主力出货信号 V1/V2/V3 |
| 2025-01 | 添加知行多空线指标 |
| 2025-01 | 添加底部暴力K线检测 |
| 2025-01 | 添加异动检测（收集区/堆量/突破） |
| 2025-01 | 优化批量验证工具，支持详细检查项解析 |
| 2026-03 | 添加情绪反弹策略（J13分位数+砖形图） |
| 2026-03 | 添加知行砖形图指标计算 |
| 2026-03 | GitHub Actions 邮件通知集成 |
| 2026-04 | Token 安全机制完善，仓库迁移工具 |

---

*文档生成时间: 2026-05-15*
