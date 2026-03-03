#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DataManager – 完整抽离版
与原脚本保持 100 % 一致，含所有 dead code / docstring / 注释
"""
import os
import sqlite3
import logging
import time
from datetime import datetime
import pandas as pd
import tushare as ts
import threading

# ✅ 新增配置导入
from config import DBConfig, APIConfig

# 如果外部已配置 logging，这里不再重复；否则兜底
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO,
                        format="[%(asctime)s] %(levelname)s | %(message)s")

# ---------- 0. 配置 ----------
ts.set_token(APIConfig.get_token())
pro = ts.pro_api()

# -------------------- DataManager 本体 --------------------
class DataManager:
    """数据管理器 - 整合数据存储功能（与原脚本 100% 一致，含 dead code）"""

    def __init__(self, db_path=None, cache_dir=None):
        # ✅ 使用配置默认值
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

    # -------------------- 因子数据（parquet 按日缓存） --------------------
    def get_stock_factors(self, trade_dates, fields):
        """获取股票因子数据：使用多线程并行获取，并显示进度条"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from tqdm import tqdm
        import time
        import queue
        
        os.makedirs(self.cache_dir, exist_ok=True)
        fields = list(fields)  # 确保有序
        
        # 确保 trade_dates 是排序的
        trade_dates = sorted(trade_dates)
        
        # 创建一个队列用于存储需要重试的日期
        retry_queue = queue.Queue()
        for date in trade_dates:
            retry_queue.put((date, 0))  # (date, retry_count)
        
        # 存放最终结果
        result_data = []
        lock = threading.Lock()  # 用于线程安全地添加数据到结果列表
        
        # 处理单个日期的函数
        def process_date(date, retry_count=0):
            max_retries = 3
            file_path = os.path.join(self.cache_dir, f"factors_{date}.parquet")
            need_cols = set(fields)
            
            # 检查缓存文件是否存在且字段完整
            if os.path.exists(file_path):
                try:
                    temp = pd.read_parquet(file_path)
                    missing_fields = need_cols - set(temp.columns)
                    if missing_fields:
                        logging.warning(f"{date} 缺失字段 {list(missing_fields)}，将重新拉取并补齐")
                        os.remove(file_path)  # 删除不完整的缓存文件
                    else:
                        # 缓存文件完整，直接读取所需字段
                        daily_data = pd.read_parquet(file_path, columns=fields)
                        return date, daily_data, True
                except Exception as e:
                    logging.warning(f"{date} parquet 读取失败，重新拉取: {e}")
                    os.remove(file_path)
            
            # 从API获取数据
            if retry_count < max_retries:
                try:
                    logging.info(f"从API获取 {date} 数据 (尝试 {retry_count + 1}/{max_retries})")
                    daily_data = pro.stk_factor_pro(trade_date=date, fields=','.join(fields))
                    
                    # 检查数据是否为空
                    if daily_data.empty:
                        logging.warning(f"获取 {date} 数据为空")
                        raise ValueError("Empty data")
                    
                    # 保存到缓存
                    daily_data.to_parquet(file_path, index=False)
                    return date, daily_data, True
                    
                except Exception as e:
                    logging.error(f"获取 {date} 数据失败: {e}")
                    # 将任务重新加入队列，等待重试
                    if retry_count + 1 < max_retries:
                        return date, None, False
                    else:
                        logging.error(f"{date} 数据获取失败，已达最大重试次数")
                        return date, pd.DataFrame(), True  # 返回空DataFrame
            
            return date, pd.DataFrame(), True  # 返回空DataFrame
        
        # 多线程处理函数
        def worker():
            while True:
                try:
                    date, retry_count = retry_queue.get_nowait()
                except queue.Empty:
                    break
                    
                try:
                    date, data, completed = process_date(date, retry_count)
                    
                    if completed:
                        with lock:
                            if not data.empty:
                                result_data.append(data)
                    else:
                        # 重新加入队列进行重试
                        retry_queue.put((date, retry_count + 1))
                        # 短暂休眠避免过于频繁的重试
                        time.sleep(1)
                        
                except Exception as e:
                    logging.error(f"处理 {date} 时发生异常: {e}")
                    # 重新加入队列进行重试
                    if retry_count + 1 < max_retries:
                        retry_queue.put((date, retry_count + 1))
                        time.sleep(1)
                    else:
                        logging.error(f"{date} 处理失败，已达最大重试次数")
                
                finally:
                    retry_queue.task_done()
                    pbar.update(1)
        
        # 创建进度条
        total_tasks = len(trade_dates)
        pbar = tqdm(total=total_tasks, desc="获取因子数据")
        
        # 创建并启动线程
        num_threads = min(10, len(trade_dates))  # 限制线程数量
        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=worker)
            t.daemon = True
            t.start()
            threads.append(t)
        
        # 等待所有任务完成
        retry_queue.join()
        
        # 关闭进度条
        pbar.close()
        
        # 合并所有数据
        if not result_data:
            return pd.DataFrame()
        
        result = pd.concat(result_data, ignore_index=True)
        
        # 检查数据完整性
        if not self._validate_and_fix_data(result, trade_dates, fields):
            # 如果数据有问题，重新获取
            logging.warning("数据验证失败，将重新获取异常日期的数据")
            # 找出缺失的日期并重新获取
            missing_dates = self._find_missing_dates(result, trade_dates)
            if missing_dates:
                missing_data = self.get_stock_factors(missing_dates, fields)
                result = pd.concat([result, missing_data], ignore_index=True)
        
        return result

    def _validate_and_fix_data(self, df, trade_dates, fields):
        """验证数据完整性并修复问题"""
        if df.empty:
            logging.error("数据为空")
            return False
        
        # 检查每日股票数量
        daily_counts = df.groupby('trade_date')['ts_code'].nunique().sort_index()
        
        # 计算平均股票数量
        avg_count = daily_counts.mean()
        
        # 检查异常日期（股票数量低于平均值的1%）
        abnormal_dates = []
        for date, count in daily_counts.items():
            if count < avg_count * 0.90:  # 1%阈值
                logging.error(f"发现异常日期 {date}: 股票数量 {count}，低于平均值 {avg_count:.0f} 的90%")
                abnormal_dates.append(date)
        
        # 如果有异常日期，删除对应的缓存文件
        if abnormal_dates:
            for date in abnormal_dates:
                file_path = os.path.join(self.cache_dir, f"factors_{date}.parquet")
                if os.path.exists(file_path):
                    logging.warning(f"删除异常日期的缓存文件: {file_path}")
                    os.remove(file_path)
            return False  # 返回False表示需要重新获取
        
        # 检查是否有重复记录
        duplicate_check = df.duplicated(subset=['ts_code', 'trade_date']).sum()
        if duplicate_check > 0:
            logging.warning(f"发现 {duplicate_check} 条重复记录")
            # 删除重复记录
            df = df.drop_duplicates(subset=['ts_code', 'trade_date'])
        
        # 检查字段完整性
        missing_fields = set(fields) - set(df.columns)
        if missing_fields:
            logging.error(f"缺失字段: {missing_fields}")
            return False
        
        # 检查空值情况
        null_counts = df.isnull().sum()
        high_null_cols = null_counts[null_counts > len(df) * 0.05]  # 超过5%为空
        if not high_null_cols.empty:
            logging.warning(f"以下字段空值较多: {high_null_cols.to_dict()}")
        
        logging.info("数据验证通过")
        return True

    # -------------------- 单日因子（parquet） --------------------
    def _get_factors_from_db(self, trade_date, fields):
        """从 parquet 读单日因子"""
        file = os.path.join(self.cache_dir, f"factors_{trade_date}.parquet")
        if not os.path.exists(file):
            return None
        df = pd.read_parquet(file, columns=fields)
        return df

    # -------------------- 保存因子到 parquet（dead code 也搬） --------------------
    def _save_factors_to_db(self, data):
        """保存因子到 parquet，按日分区"""
        for trade_date, day_df in data.groupby('trade_date'):
            file = os.path.join(self.cache_dir, f"factors_{trade_date}.parquet")
            day_df.to_parquet(file, index=False)

    # -------------------- 股票基本信息 --------------------
    def get_stock_basic_info(self):
        """获取股票基本信息"""
        cache_file = os.path.join(self.cache_dir, "stock_basic.pkl")

        # 首先尝试从数据库读取
        db_data = self._get_basic_info_from_db()
        if db_data is not None:
            return db_data

        # 然后尝试从缓存读取
        if os.path.exists(cache_file):
            logging.info("从缓存读取股票基本信息")
            return pd.read_pickle(cache_file)

        # 最后从API获取
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

    # -------------------- 内部 DB 辅助 --------------------
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