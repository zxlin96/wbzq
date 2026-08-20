# config.py
"""
策略配置中心 - 集中管理所有可调整参数
"""

import os
from dataclasses import dataclass
from typing import Dict, Tuple

# 尝试加载 .env 文件（本地开发时使用）
try:
    from pathlib import Path
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())
except Exception:
    pass  # 如果加载失败，继续使用系统环境变量

# ======== API 配置 ========
class APIConfig:
    """API 相关配置 - 必须从环境变量读取，无默认值防止泄露"""
    
    @classmethod
    def get_token(cls) -> str:
        """获取 Tushare Token，优先从环境变量读取"""
        token = os.environ.get('TUSHARE_TOKEN')
        if not token:
            raise ValueError(
                "错误：未设置 TUSHARE_TOKEN 环境变量！\n"
                "请通过以下方式设置：\n"
                "1. 本地运行：创建 .env 文件，添加 TUSHARE_TOKEN=你的token\n"
                "2. 本地运行：设置系统环境变量 TUSHARE_TOKEN=你的token\n"
                "3. GitHub Actions：在仓库 Settings -> Secrets 中添加 TUSHARE_TOKEN\n"
                "4. Linux/Mac: export TUSHARE_TOKEN='你的token'\n"
                "5. Windows: set TUSHARE_TOKEN=你的token"
            )
        return token
    
    # 为了兼容性，保留类属性（实际使用时调用 get_token()）
    @property
    def TUSHARE_TOKEN(self) -> str:
        return self.get_token()
    
    CACHE_EXPIRE_DAYS: int = 7  # 行业信息缓存天数

# ======== 策略参数配置 ========
@dataclass
class StrategyThresholds:
    """所有策略阈值参数"""
    
    # 异动 - 收集区涨幅阈值
    COLLECT_PCT_00_60: float = 3.8   # 00/60开头股票
    COLLECT_PCT_OTHER: float = 7.0   # 其他股票
    
    # 异动 - 收集区量能
    COLLECT_MA60_TOLERANCE: float = 1.15  # 收集区价格上限 = MA60 × 此值
    COLLECT_VOL_MULTIPLIER: float = 2.4   # 收集区量能倍数（量 > 5日均量 × 此值）
    
    # 异动 - 突破放量
    BREAKTHROUGH_VOL_MULTIPLIER: float = 2.0  # 突破放量量能倍数
    BREAKTHROUGH_PCT: float = 3.0             # 突破放量大涨阈值
    
    # 异动 - 堆量判定阈值
    VOLUME_MULTIPLIER: float = 1.9   # 量 > 5日均量 × 此值
    PILE_PCT_00_60: float = 2.5      # 堆量日涨幅阈值
    PILE_PCT_OTHER: float = 5.0
    
    # 跳空缺口判定
    GAP_SIZE_RATIO: float = 0.025    # 2.5% 为大跳空
    MIN_GAP_SIZE_RATIO: float = 0.005 # 0.5% 进入宽松模式
    GAP_IGNORE_VOL_RATE: float = 1.5     # 小跳空可忽略：量能放大倍数
    GAP_IGNORE_PRICE_RATE: float = 1.15  # 小跳空可忽略：价格上涨倍数
    GAP_LARGE_VOL_RATE: float = 3.0      # 大跳空回补：量能放大倍数
    GAP_LARGE_PRICE_RATE: float = 1.15   # 大跳空回补：价格上涨倍数
    
    # 阶梯放量
    PRICE_VOLUME_CONSECUTIVE: int = 2  # 连续价随量升天数
    VOLUME_DECLINE_PENALTY: float = 1.0  # 下跌日最大成交量惩罚（倍数）
    
    # K线形态
    BODY_RATIO_THRESHOLD: float = 0.2   # 十字星实体/振幅比
    MIN_SHADOW_RATIO: float = 0.4       # 下影线/振幅比
    KLINE_PCT_RANGE: Tuple[float, float] = (-2.0, 2.0)  # K线形态涨跌幅范围
    
    # 振幅限制
    AMPLITUDE_00_60: float = 4.0   # 主板振幅上限
    AMPLITUDE_OTHER: float = 7.0   # 其他板振幅上限
    
    # 放量检测
    VOLUME_SURGE_RATIO: float = 3.0   # 成交额 ≥ 前5天均值 × 此值
    
    # 趋势判定
    TREND_WINDOWS: Tuple[int, int, int] = (3, 8, 13)  # 趋势计算窗口
    TREND_CONFIRM_COUNT: int = 2  # 至少几个窗口确认为向上
    
    # 相对地量
    RELATIVE_VOLUME_RATIO: float = 0.30  # 周期最高量的30%以下视为地量
    
    # 成交额排名
    AMOUNT_TOP_PERCENT: float = 0.40  # 前40%
    
    # 底部暴力K
    BOTTOM_VK_VOL_MULTIPLIER: float = 2.0   # 底部暴力K放量倍数
    BOTTOM_VK_BODY_PCT_00_60: float = 0.03  # 主板实体涨幅阈值
    BOTTOM_VK_BODY_PCT_OTHER: float = 0.06  # 创业板/科创板实体涨幅阈值
    BOTTOM_VK_MA60_TOL_00_60: float = 0.10  # 主板MA60容忍度
    BOTTOM_VK_MA60_TOL_OTHER: float = 0.20  # 创业板/科创板MA60容忍度
    
    # 出货信号
    DISTRIBUTION_VOL_MULTIPLIER: float = 2.0  # 出货信号放量倍数
    DISTRIBUTION_YIN_PCT_00_60: float = 0.03  # 主板大阴线阈值
    DISTRIBUTION_YIN_PCT_OTHER: float = 0.06  # 创业板/科创板大阴线阈值
    DISTRIBUTION_V2_DROP_00_60: float = 0.08  # V2主板跌幅阈值
    DISTRIBUTION_V2_DROP_OTHER: float = 0.12  # V2创业板/科创板跌幅阈值
    DISTRIBUTION_V3_MIN_YIN_COUNT: int = 2    # V3最少放量长阴次数

    # 多指标联合选股（策略3）阈值 — spec 6.1
    MULTI_KDJ_J_MAX: float = 13.0                 # KDJ-J 上限，入选要求 J < 该值
    MULTI_MACD_DIF_MIN: float = 0.0               # MACD-DIF 下限，入选要求 DIF > 该值
    MULTI_MA_PERIOD: int = 60                     # 均线周期（对应 ma_qfq_60 字段）
    MULTI_PCT_CHG_MAX: float = 3.0                # 涨跌幅上限（%），入选要求涨跌幅 < 该值
    MULTI_PCT_CHG_MIN: float = -3.0               # 涨跌幅下限（%），入选要求涨跌幅 > 该值
    MULTI_AMOUNT_TOP_PERCENT: float = 0.60        # 成交额排名分位，前 60%
    MULTI_MV_MIN_BILLION: float = 50.0            # 总市值下限（亿元），入选要求总市值 > 该值
    MULTI_AMPLITUDE_MAX: float = 7.0              # 振幅上限（%），入选要求振幅 < 该值
    MULTI_INDUSTRY_COUNT_THRESHOLD: int = 10      # 行业入选数量阈值，超过该值触发 ETF 提示

# ======== 回测配置 ========
@dataclass
class BacktestConfig:
    """回测相关参数"""
    HOLD_DAYS: int = 3
    LOOKBACK_DAYS: int = 60
    MIN_STOCK_AGE_DAYS: int = 180  # 剔除次新股

# ======== 并行配置 ========
class ParallelConfig:
    """并行执行参数"""
    MAX_WORKERS: int = 12
    CHUNK_SIZE: int = 50

# ======== 数据库配置 ========
class DBConfig:
    """数据库和缓存配置 - 支持从环境变量读取"""
    DB_PATH: str = os.environ.get('DB_PATH', 'stock_strategy.db')
    CACHE_DIR: str = os.environ.get('CACHE_DIR', 'data_cache')
    CACHE_EXPIRE_SECONDS: int = 7 * 24 * 3600  # 7天

# ======== 全局配置实例 ========
STRATEGY_CONFIG = StrategyThresholds()
BACKTEST_CONFIG = BacktestConfig()