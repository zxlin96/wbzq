#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DataManager – 基于 daily + adj_factor + daily_basic 的本地化计算版
替代已不可用的 stk_factor_pro，保持外部接口 100% 兼容
"""
import os
import sqlite3
import logging
import time
import threading
from datetime import datetime

import numpy as np
import pandas as pd
import tushare as ts

from config import DBConfig, APIConfig

if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO,
                        format="[%(asctime)s] %(levelname)s | %(message)s")

# ---------- Tushare 初始化 ----------
ts.set_token(APIConfig.get_token())
pro = ts.pro_api()

# -------------------- DataManager 本体 --------------------
class DataManager:
    """数据管理器 - 基于 daily + adj_factor + daily_basic 本地计算"""

    # 需要从 API 获取的原始字段
    RAW_API_COLS = {
        'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
        'pre_close', 'change', 'pct_chg', 'vol', 'amount',
        'adj_factor',
        'turnover_rate', 'turnover_rate_f', 'volume_ratio',
        'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm',
        'dv_ratio', 'dv_ttm', 'total_share', 'float_share', 'free_share',
        'total_mv', 'circ_mv'
    }

    # 本地计算生成的字段
    COMPUTED_COLS = {
        'open_qfq', 'high_qfq', 'low_qfq', 'close_qfq',
        'ma_qfq_5', 'ma_qfq_10', 'ma_qfq_20', 'ma_qfq_30',
        'ma_qfq_60', 'ma_qfq_90', 'ma_qfq_250',
        'ema_qfq_5', 'ema_qfq_10', 'ema_qfq_12', 'ema_qfq_13',
        'ema_qfq_20', 'ema_qfq_26', 'ema_qfq_30', 'ema_qfq_60',
        'ema_qfq_90', 'ema_qfq_250',
        'macd_dif_qfq', 'macd_dea_qfq', 'macd_qfq',
        'kdj_k_qfq', 'kdj_d_qfq', 'kdj_qfq',
    }

    ALL_FACTOR_COLS = RAW_API_COLS | COMPUTED_COLS

    def __init__(self, db_path=None, cache_dir=None):
        self.db_path = db_path or DBConfig.DB_PATH
        self.cache_dir = cache_dir or DBConfig.CACHE_DIR
        self.conn = None
        self._init_database()

    # -------------------- 初始化数据库 --------------------
    def _init_database(self):
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            close_qfq REAL, high_qfq REAL, low_qfq REAL, ma_qfq_60 REAL,
            kdj_qfq REAL, macd_dif_qfq REAL, amount REAL,
            created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ts_code, trade_date)
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_basic_info (
            ts_code TEXT PRIMARY KEY,
            name TEXT,
            industry_name TEXT,
            list_date TEXT,
            updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        self.conn.commit()

    # -------------------- 交易日历 --------------------
    def get_trade_dates(self, start_date, end_date):
        cache_file = os.path.join(self.cache_dir,
                                  f"trade_dates_{start_date}_{end_date}.pkl")
        if os.path.exists(cache_file):
            logging.info("从缓存读取交易日历")
            return pd.read_pickle(cache_file).tolist()

        logging.info("从API获取交易日历")
        try:
            trade_cal = pro.trade_cal(exchange='',
                                      is_open=1,
                                      start_date=start_date,
                                      end_date=end_date)
            trade_dates = trade_cal['cal_date'].tolist()
            os.makedirs(self.cache_dir, exist_ok=True)
            pd.Series(trade_dates).to_pickle(cache_file)
            return trade_dates
        except Exception as e:
            logging.error(f"获取交易日历失败: {e}")
            return []

    # -------------------- 核心：因子数据 --------------------
    def get_stock_factors(self, trade_dates, fields):
        """获取股票因子数据（兼容旧接口，内部已切换为 daily+adj+basic）"""
        from tqdm import tqdm
        import queue

        os.makedirs(self.cache_dir, exist_ok=True)
        fields = list(fields)
        trade_dates = sorted(trade_dates)

        # 1. 检查缓存
        cached_frames = []
        missing_dates = []
        for date in trade_dates:
            file_path = os.path.join(self.cache_dir, f"factors_{date}.parquet")
            if os.path.exists(file_path):
                try:
                    temp = pd.read_parquet(file_path)
                    missing_cols = set(fields) - set(temp.columns)
                    if missing_cols:
                        logging.warning(f"{date} 缓存缺失字段 {list(missing_cols)}，将重新生成")
                        os.remove(file_path)
                        missing_dates.append(date)
                    else:
                        cached_frames.append(pd.read_parquet(file_path, columns=fields))
                except Exception as e:
                    logging.warning(f"{date} 缓存读取失败: {e}")
                    os.remove(file_path)
                    missing_dates.append(date)
            else:
                missing_dates.append(date)

        if not missing_dates:
            return pd.concat(cached_frames, ignore_index=True)

        # 2. 确定计算区间（向前扩展 150 天保证指标收敛）
        compute_start = pd.to_datetime(min(missing_dates), format='%Y%m%d') - pd.Timedelta(days=150)
        compute_end = pd.to_datetime(max(trade_dates), format='%Y%m%d')
        extended_dates = self.get_trade_dates(
            compute_start.strftime('%Y%m%d'),
            compute_end.strftime('%Y%m%d')
        )

        # 3. 读取扩展区间内已有缓存的数据作为历史铺垫
        history_frames = []
        for date in extended_dates:
            file_path = os.path.join(self.cache_dir, f"factors_{date}.parquet")
            if os.path.exists(file_path) and date not in missing_dates:
                try:
                    history_frames.append(pd.read_parquet(file_path))
                except Exception:
                    pass

        # 4. 多线程拉取 missing_dates 的原始数据
        raw_frames = self._fetch_raw_data_parallel(missing_dates)

        if not history_frames and not raw_frames:
            logging.error("无法获取任何原始数据")
            return pd.concat(cached_frames, ignore_index=True) if cached_frames else pd.DataFrame()

        # 5. 合并、去重、计算指标
        df = pd.concat(history_frames + raw_frames, ignore_index=True)
        df = df.sort_values(['ts_code', 'trade_date']).drop_duplicates(subset=['ts_code', 'trade_date']).reset_index(drop=True)
        df = self._calculate_indicators(df)

        # 6. 保存扩展区间内所有日期的缓存
        for date, day_df in df.groupby('trade_date'):
            if date in extended_dates:
                out_path = os.path.join(self.cache_dir, f"factors_{date}.parquet")
                day_df.to_parquet(out_path, index=False)

        # 7. 提取 missing_dates 的数据并与缓存合并
        new_frames = []
        for date in missing_dates:
            day_df = df[df['trade_date'] == date]
            if not day_df.empty:
                avail_cols = [c for c in fields if c in day_df.columns]
                if avail_cols:
                    new_frames.append(day_df[avail_cols])

        all_frames = cached_frames + new_frames
        if not all_frames:
            return pd.DataFrame()
        return pd.concat(all_frames, ignore_index=True)

    def _fetch_raw_data_parallel(self, dates):
        """多线程拉取每日原始数据（daily + adj_factor + daily_basic）"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from tqdm import tqdm

        results = {}
        max_workers = min(10, len(dates)) if dates else 1

        def fetch_one(date):
            try:
                # daily
                daily_fields = 'ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount'
                daily_df = pro.daily(trade_date=date, fields=daily_fields)
                if daily_df is None or daily_df.empty:
                    return date, None

                # adj_factor
                adj_df = pro.adj_factor(trade_date=date, fields='ts_code,adj_factor')
                if adj_df is not None and not adj_df.empty:
                    daily_df = daily_df.merge(adj_df[['ts_code', 'adj_factor']], on='ts_code', how='left')
                else:
                    daily_df['adj_factor'] = np.nan

                # daily_basic
                basic_fields = (
                    'ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,'
                    'pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,'
                    'total_share,float_share,free_share,total_mv,circ_mv'
                )
                basic_df = pro.daily_basic(trade_date=date, fields=basic_fields)
                if basic_df is not None and not basic_df.empty:
                    basic_df = basic_df.drop(columns=['trade_date'], errors='ignore')
                    daily_df = daily_df.merge(basic_df, on='ts_code', how='left')

                time.sleep(0.12)  # 频率保护
                return date, daily_df
            except Exception as e:
                logging.error(f"拉取 {date} 原始数据失败: {e}")
                return date, None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_date = {executor.submit(fetch_one, d): d for d in dates}
            with tqdm(total=len(dates), desc="拉取原始数据") as pbar:
                for future in as_completed(future_to_date):
                    date, df = future.result()
                    if df is not None:
                        results[date] = df
                    pbar.update(1)

        return [results[d] for d in dates if d in results]

    def _calculate_indicators(self, df):
        """基于原始数据本地计算所有技术指标"""
        if df.empty:
            return df

        df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
        grouped = df.groupby('ts_code')

        # ---------- 前复权价格 ----------
        latest_adj = grouped['adj_factor'].transform('last')
        for col in ['open', 'high', 'low', 'close']:
            df[f'{col}_qfq'] = df[col] * df['adj_factor'] / latest_adj

        # ---------- MA（简单均线）----------
        for period in [5, 10, 20, 30, 60, 90, 250]:
            df[f'ma_qfq_{period}'] = grouped['close_qfq'].transform(
                lambda x: x.rolling(window=period, min_periods=period).mean()
            )

        # ---------- EMA ----------
        for period in [5, 10, 12, 13, 20, 26, 30, 60, 90, 250]:
            df[f'ema_qfq_{period}'] = grouped['close_qfq'].transform(
                lambda x: x.ewm(span=period, adjust=False).mean()
            )

        # ---------- MACD ----------
        df['macd_dif_qfq'] = df['ema_qfq_12'] - df['ema_qfq_26']
        df['macd_dea_qfq'] = grouped['macd_dif_qfq'].transform(
            lambda x: x.ewm(span=9, adjust=False).mean()
        )
        df['macd_qfq'] = (df['macd_dif_qfq'] - df['macd_dea_qfq']) * 2

        # ---------- KDJ ----------
        low_min = grouped['low_qfq'].transform(lambda x: x.rolling(window=9, min_periods=9).min())
        high_max = grouped['high_qfq'].transform(lambda x: x.rolling(window=9, min_periods=9).max())
        rsv = (df['close_qfq'] - low_min) / (high_max - low_min) * 100
        rsv = rsv.replace([np.inf, -np.inf], 0).fillna(0)

        df['rsv_tmp'] = rsv
        df['kdj_k_qfq'] = df.groupby('ts_code')['rsv_tmp'].transform(lambda x: x.ewm(com=2, adjust=False).mean())
        df['kdj_d_qfq'] = df.groupby('ts_code')['kdj_k_qfq'].transform(lambda x: x.ewm(com=2, adjust=False).mean())
        df['kdj_qfq'] = 3 * df['kdj_k_qfq'] - 2 * df['kdj_d_qfq']
        df = df.drop(columns=['rsv_tmp'])

        return df

    def _validate_and_fix_data(self, df, trade_dates, fields):
        """验证数据完整性并修复问题（兼容旧逻辑）"""
        if df.empty:
            logging.error("数据为空")
            return False
        daily_counts = df.groupby('trade_date')['ts_code'].nunique().sort_index()
        avg_count = daily_counts.mean()
        abnormal_dates = []
        for date, count in daily_counts.items():
            if count < avg_count * 0.90:
                logging.error(f"发现异常日期 {date}: 股票数量 {count}，低于平均值 {avg_count:.0f} 的90%")
                abnormal_dates.append(date)
        if abnormal_dates:
            for date in abnormal_dates:
                file_path = os.path.join(self.cache_dir, f"factors_{date}.parquet")
                if os.path.exists(file_path):
                    logging.warning(f"删除异常日期的缓存文件: {file_path}")
                    os.remove(file_path)
            return False
        duplicate_check = df.duplicated(subset=['ts_code', 'trade_date']).sum()
        if duplicate_check > 0:
            logging.warning(f"发现 {duplicate_check} 条重复记录")
            df = df.drop_duplicates(subset=['ts_code', 'trade_date'])
        missing_fields = set(fields) - set(df.columns)
        if missing_fields:
            logging.error(f"缺失字段: {missing_fields}")
            return False
        null_counts = df.isnull().sum()
        high_null_cols = null_counts[null_counts > len(df) * 0.05]
        if not high_null_cols.empty:
            logging.warning(f"以下字段空值较多: {high_null_cols.to_dict()}")
        logging.info("数据验证通过")
        return True

    def _find_missing_dates(self, df, trade_dates):
        """找出缺失的日期（兼容旧逻辑）"""
        existing_dates = set(df['trade_date'].unique())
        return [d for d in trade_dates if d not in existing_dates]

    # -------------------- 单日因子（parquet） --------------------
    def _get_factors_from_db(self, trade_date, fields):
        """从 parquet 读单日因子"""
        file = os.path.join(self.cache_dir, f"factors_{trade_date}.parquet")
        if not os.path.exists(file):
            return None
        df = pd.read_parquet(file, columns=fields)
        return df

    def _save_factors_to_db(self, data):
        """保存因子到 parquet，按日分区"""
        for trade_date, day_df in data.groupby('trade_date'):
            file = os.path.join(self.cache_dir, f"factors_{trade_date}.parquet")
            day_df.to_parquet(file, index=False)

    # -------------------- 股票基本信息 --------------------
    def get_stock_basic_info(self):
        """获取股票基本信息"""
        cache_file = os.path.join(self.cache_dir, "stock_basic.pkl")
        db_data = self._get_basic_info_from_db()
        if db_data is not None:
            return db_data
        if os.path.exists(cache_file):
            logging.info("从缓存读取股票基本信息")
            return pd.read_pickle(cache_file)
        logging.info("从API获取股票基本信息")
        try:
            basic_info = pro.stock_basic(exchange='',
                                         list_status='L',
                                         fields='ts_code,name,industry,list_date')
            basic_info = basic_info.rename(columns={'industry': 'industry_name'})
            basic_info['industry_name'] = basic_info['industry_name'].fillna('未知行业')
            os.makedirs(self.cache_dir, exist_ok=True)
            basic_info.to_pickle(cache_file)
            self._save_basic_info_to_db(basic_info)
            return basic_info
        except Exception as e:
            logging.error(f"获取股票基本信息失败: {e}")
            return pd.DataFrame()

    def _get_basic_info_from_db(self):
        """从数据库读取基本信息"""
        try:
            query = "SELECT * FROM stock_basic_info"
            result = pd.read_sql_query(query, self.conn)
            return result if not result.empty else None
        except Exception:
            return None

    def _save_basic_info_to_db(self, data):
        """保存基本信息到数据库"""
        try:
            data['updated_time'] = datetime.now()
            data.to_sql('stock_basic_info', self.conn,
                        if_exists='replace', index=False)
            self.conn.commit()
        except Exception as e:
            logging.warning(f"保存基本信息到数据库失败: {e}")

    # -------------------- 关闭连接 --------------------
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
