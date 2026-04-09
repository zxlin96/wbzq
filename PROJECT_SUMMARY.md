# 股票量化选股系统 - 项目总结文档

## 一、项目概述

本项目是一个基于多因子策略的股票筛选与回测系统，使用 Python 开发，主要功能是通过技术分析指标筛选出符合特定交易策略的股票。

### 1.1 项目名称
**wbzq** (股票量化选股系统)

### 1.2 核心功能
- 多因子股票筛选（11项技术指标检查）
- 阶梯放量策略检测
- 底部暴力K线识别
- 异动信号检测
- 主力出货信号识别（3种版本）
- 回测验证功能
- 可视化报告生成

---

## 二、项目结构

```
wbzq/
├── main_par2.py              # 主程序：选股策略核心 (1795行)
├── config.py                 # 策略配置中心
├── data_manager.py           # 数据管理模块
├── batch_validate.py         # 批量验证工具
├── generate_stock_html.py    # 股票选股HTML报告生成
├── generate_trend_html.py    # 趋势图HTML生成
├── generate_reports_json.py  # JSON报告生成
├── migrate_to_new_repo.py    # 仓库迁移工具
├── requirements.txt          # 依赖列表
├── validate_list.csv         # 验证股票列表
├── README.md                 # 项目说明文档
├── .github/workflows/        # GitHub Actions 工作流
│   ├── deploy-pages.yml      # GitHub Pages 部署
│   └── stock-strategy.yml    # 股票策略自动运行
├── html/                     # 生成的HTML报告目录
│   └── YYYYMMDD/             # 按日期组织的报告
├── logs/                     # 验证日志目录
├── data_cache/               # 数据缓存目录
└── industry_cache.pkl        # 行业信息缓存
```

---

## 三、核心模块详解

### 3.1 main_par2.py (主程序)

**文件大小**: 1795行

**主要功能**:

#### 3.1.1 数据字段定义 (STOCK_FACTOR_FIELDS)
定义了股票分析所需的全部字段，包括：
- 基础价格数据（未复权/前复权/后复权）
- 换手率与量比
- 估值指标（PE、PB、PS等）
- 技术指标（ASI、ATR、BBI、BIAS、BOLL、CCI、CR等）
- MACD指标
- 均线系统（5/10/20/30/60/90/250日）
- 状态类指标（updays、downdays等）

#### 3.1.2 核心分析函数

| 函数名 | 功能说明 |
|--------|----------|
| `is_ignorable_gap()` | 判断跳空缺口是否可忽略 |
| `identify_candle_pattern()` | 识别三类K线形态（小阳线/十字星/带下影阴线） |
| `mark_step_vol_price()` | 标记阶梯放量+价升条件 |
| `mark_volume_surge()` | 标记成交额≥前5天均值3倍的交易日 |
| `mark_abnormal_movement()` | 标记异动信号（收集区/堆量/突破） |
| `mark_bottom_violent_k()` | 标记底部暴力K线 |
| `mark_distribution_signal()` | 标记主力出货信号V1 |
| `mark_distribution_signal_v2()` | 标记主力出货信号V2 |
| `mark_distribution_signal_v3()` | 标记主力出货信号V3 |
| `mark_zhixing_indicator()` | 标记知行多空线指标 |
| `mark_amplitude()` | 标记振幅限制 |
| `mark_relative_volume()` | 标记相对地量 |
| `mark_amount_rank()` | 标记成交额排名 |

#### 3.1.3 选股策略条件 (11项检查)

```python
# 最终筛选条件
cond = (
    df_filtered['first_j13_step'] &              # 1. 阶梯放量
    (df_filtered['macd_dif_qfq'] > 0) &          # 2. MACD>0
    df_filtered['shrink'] &                      # 3. 缩量
    ~df_filtered['gap_up'] &                     # 4. 无跳空
    (df_filtered['close_qfq'] > df_filtered['ma_qfq_60']) &  # 5. 收盘价>MA60
    df_filtered['ma60_upward'] &                 # 6. MA60向上
    df_filtered['is_acceptable_candle'] &        # 7. K线形态可接受
    df_filtered['is_amplitude_ok'] &             # 8. 振幅符合要求
    df_filtered['has_am_in_period'] &            # 9. 周期内有异动
    df_filtered['is_amount_top30'] &             # 10. 成交额前60%
    df_filtered['has_bottom_violent_k'] &        # 11. 有底部暴力K
    ~df_filtered['has_distribution_signal'] &    # 12. 无出货信号V1
    ~df_filtered['has_distribution_signal_v2'] & # 13. 无出货信号V2
    ~df_filtered['has_distribution_signal_v3'] & # 14. 无出货信号V3
    df_filtered.groupby('ts_code')['volume_surge'].transform('any') &  # 15. 有过放量
    (df_filtered['zhixing_mid_duokong'] > df_filtered['zhixing_duokong']) &  # 16. 知行多空
    (df_filtered['close_qfq'] >= df_filtered['zhixing_duokong'])  # 17. 收盘价≥多空线
)
```

#### 3.1.4 主力出货信号检测逻辑

**V1版本**: 最高点当天放天量大阴线
- 成交额 ≥ 前日 2倍
- 当日为阴线

**V2版本**: 最高点后两天成交额均值放大且累计跌幅达标
- 最高点后2天成交额均值 ≥ 最高点当天 1.5倍
- 累计跌幅 ≥ 5%

**V3版本**: 最高点后出现2次及以上放量长阴
- 需要至少2次放量长阴信号

#### 3.1.5 底部暴力K线条件

```python
# 主板（10%涨停）
min_body_pct = 0.03      # 实体涨幅≥3%
ma60_tolerance = 0.10    # ±10%范围内

# 创业板/科创板（20%涨停）
min_body_pct = 0.06      # 实体涨幅≥6%
ma60_tolerance = 0.20    # ±20%范围内

# 共同条件
volume_surge = 成交额 ≥ 前一日 × 2.0
near_ma60 = 收盘价接近MA60（在容差范围内）
```

### 3.2 config.py (配置中心)

**配置类别**:

| 配置类 | 说明 |
|--------|------|
| `APIConfig` | Tushare API Token 配置 |
| `StrategyThresholds` | 所有策略阈值参数 |
| `BacktestConfig` | 回测相关参数 |
| `ParallelConfig` | 并行执行参数 |
| `DBConfig` | 数据库和缓存配置 |

**关键阈值参数**:

```python
# 异动涨幅阈值
COLLECT_PCT_00_60: float = 3.8    # 主板
COLLECT_PCT_OTHER: float = 7.0    # 创业板/科创板

# 放量检测
VOLUME_SURGE_RATIO: float = 3.0   # 成交额 ≥ 前5天均值 × 3

# K线形态
BODY_RATIO_THRESHOLD: float = 0.2   # 十字星判定
MIN_SHADOW_RATIO: float = 0.4       # 下影线判定

# 振幅限制
AMPLITUDE_00_60: float = 4.0   # 主板≤4%
AMPLITUDE_OTHER: float = 7.0   # 其他≤7%
```

### 3.3 data_manager.py (数据管理)

**主要功能**:
- 从 Tushare API 获取股票数据
- 数据缓存管理
- 交易日历获取
- 股票基本信息获取

### 3.4 batch_validate.py (批量验证)

**功能**:
- 批量验证指定股票列表
- 生成详细验证日志
- 输出验证报告

---

## 四、使用方式

### 4.1 单只股票调试

```bash
python main_par2.py --date 20250620 --days 60 --debug 688321.SH
```

**参数说明**:
- `--date`: 回测日期 (YYYYMMDD)，默认今天
- `--days`: 回测历史天数，默认60天
- `--debug`: 调试模式，传入股票代码查看详细检查过程
- `--backtest`: 执行回测
- `--hold-days`: 回测持有天数，默认3天
- `--detailed`: 打印每只股票逐日持仓数据

### 4.2 批量选股

```bash
python main_par2.py --date 20250620 --days 60
```

### 4.3 批量验证

1. 编辑 `validate_list.csv`:
```csv
code,name,date_str,days,expect_strategy
688321.SH,微芯生物,20250620,60,策略1
688799.SH,华纳药厂,20250508,60,策略1
```

2. 运行验证:
```bash
python batch_validate.py
```

---

## 五、依赖库

```
numpy>=1.24.0          # 数值计算
pandas>=2.0.0          # 数据处理
pyarrow>=12.0.0        # Parquet文件支持
tushare>=1.2.0         # 股票数据接口
plotly>=5.0.0          # 可视化
scikit-learn>=1.3.0    # 机器学习（MinMaxScaler）
tabulate>=0.9.0        # 表格输出
tqdm>=4.65.0           # 进度条
```

---

## 六、输出文件

### 6.1 控制台输出
- 各阶段股票计数统计
- 最终筛选结果表格
- 行业分布统计
- 每日统计信息

### 6.2 HTML报告
- `stock_selection_YYYYMMDD.html`: 选股结果交互式报告
- `industry_total_amount_trend.html`: 行业成交额趋势图
- `first_j13_step_daily_count.html`: J<13每日趋势图

### 6.3 验证输出
- `logs/股票代码_日期.log`: 单只股票详细日志
- `result.csv`: 验证结果汇总
- `result_detail.csv`: 详细检查项结果
- `validation_report.txt`: 完整验证报告

---

## 七、GitHub Actions 自动化

### 7.1 工作流配置

**stock-strategy.yml**: 定时运行股票策略
- 定时触发（UTC 01:00，北京时间 09:00）
- 获取股票数据并运行选股策略
- 生成HTML报告

**deploy-pages.yml**: GitHub Pages 部署
- 自动部署生成的HTML报告

### 7.2 Secrets 配置
- `TUSHARE_TOKEN`: Tushare API Token

---

## 八、关键算法说明

### 8.1 阶梯放量策略

```
条件:
1. 股票上穿60日线且不跳空
2. 上穿后出现连续价随量升
3. 下跌日成交量不是最大量
4. J值<13时标记为买入信号
```

### 8.2 知行多空线

```
计算方式:
- 多空线 = EMA(收盘价, 26)
- 中期多空线 = EMA(多空线, 10)

买入条件:
- 中期多空线 > 多空线
- 收盘价 ≥ 多空线
```

### 8.3 相对地量判定

```
条件:
- 当日成交量 ≤ 周期最高量的30%
- 用于判断回调是否到位
```

---

## 九、注意事项

1. **数据依赖**: 需要有效的 Tushare API Token
2. **网络要求**: 首次运行需要联网获取股票数据
3. **缓存机制**: 行业信息缓存7天
4. **调试模式**: 建议只用于单只股票分析
5. **磁盘空间**: 批量验证会生成较多日志文件

---

## 十、代码统计

| 文件 | 行数 | 功能 |
|------|------|------|
| main_par2.py | 1795 | 主程序，选股策略核心 |
| config.py | 118 | 策略配置中心 |
| data_manager.py | ~200 | 数据管理模块 |
| batch_validate.py | ~300 | 批量验证工具 |
| generate_stock_html.py | ~400 | HTML报告生成 |
| generate_trend_html.py | ~200 | 趋势图生成 |

**总计**: 约 3000+ 行 Python 代码

---

## 十一、更新日志

- **2025-01**: 添加主力出货信号V3（需2次及以上放量长阴）
- **2025-01**: 添加知行多空线指标
- **2025-01**: 添加底部暴力K线检测
- **2025-01**: 添加异动检测（收集区/堆量/突破）
- **2024-12**: 初始版本发布

---

*文档生成时间: 2026-04-01*
*项目版本: v1.0*
