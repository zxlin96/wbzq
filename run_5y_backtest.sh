#!/usr/bin/env bash
# 后台运行：生成 5 年 hints + 策略C回测 + 年/月汇总
cd /home/zxlin/my_python/wbzq
source venv/bin/activate

LOG=/home/zxlin/my_python/wbzq/run_5y_backtest.log
echo "===== 开始 $(date) =====" >> "$LOG"

echo "===== [1/3] 生成 5 年缺失 hints (2021-08-28 ~ 2026-08-28) =====" >> "$LOG"
python backtest_industry_etf.py --start-date 20210828 --end-date 20260828 --generate-hints >> "$LOG" 2>&1
echo "[1/3] 完成 hints 生成 $(date)" >> "$LOG"

echo "===== [2/3] 策略C回测 (no-repeat + 知行多空过滤) =====" >> "$LOG"
python backtest_industry_etf.py --start-date 20210828 --end-date 20260828 --no-repeat --filter-zhixing --output-dir cmp_5y >> "$LOG" 2>&1
echo "[2/3] 完成回测 $(date)" >> "$LOG"

echo "===== [3/3] 年/月汇总 =====" >> "$LOG"
python summarize_backtest_years.py >> "$LOG" 2>&1
echo "[3/3] 完成汇总 $(date)" >> "$LOG"

echo "===== 全部完成 $(date) =====" >> "$LOG"
