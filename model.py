"""
River 在线学习模型
使用延迟标签机制，10分钟后才知道预测是否正确
"""
from collections import deque
from dataclasses import dataclass
from typing import Optional
import pickle

from river import linear_model, preprocessing
from river.compose import Pipeline


@dataclass
class PendingSample:
    """待标注样本"""
    timestamp: int
    features: dict
    entry_price: float
    settle_timestamp: int
    direction: str  # "UP" or "DOWN"


class RiverModel:
    """River在线学习模型 - 分方向训练"""
    
    def __init__(self, horizon_minutes: int = 10):
        self.horizon = horizon_minutes
        self.horizon_ms = horizon_minutes * 60 * 1000
        
        # 做空模型：预测超买后下跌概率
        # 使用 L2=0.5 正则化，提高各级别胜率
        self.model_down = Pipeline(
            preprocessing.StandardScaler(),
            linear_model.LogisticRegression(l2=0.5)
        )
        
        # 做多模型：预测超卖后上涨概率
        self.model_up = Pipeline(
            preprocessing.StandardScaler(),
            linear_model.LogisticRegression(l2=0.5)
        )
        
        # 待标注样本队列
        self.pending: deque[PendingSample] = deque()
        
        # 统计
        self.stats = {
            "down": {"total": 0, "correct": 0},
            "up": {"total": 0, "correct": 0},
        }
    
    def predict_down(self, features: dict) -> float:
        """预测下跌概率（用于做空信号）"""
        return self._predict(self.model_down, features)
    
    def predict_up(self, features: dict) -> float:
        """预测上涨概率（用于做多信号）"""
        return self._predict(self.model_up, features)
    
    def _predict(self, model, features: dict) -> float:
        """通用预测"""
        if not features:
            return 0.5
        try:
            proba = model.predict_proba_one(features)
            if proba:
                p = proba.get(True, proba.get(1, 0.5))
                if p != p:  # NaN check
                    return 0.5
                return max(0.0, min(1.0, p))
            return 0.5
        except Exception:
            return 0.5

    def add_pending(self, timestamp: int, features: dict, 
                    entry_price: float, direction: str):
        """添加待标注样本"""
        self.pending.append(PendingSample(
            timestamp=timestamp,
            features=features,
            entry_price=entry_price,
            settle_timestamp=timestamp + self.horizon_ms,
            direction=direction
        ))
    
    def update_with_price(self, current_ts: int, current_price: float) -> list:
        """用当前价格更新已到期的样本，返回结算结果"""
        results = []
        
        while self.pending and self.pending[0].settle_timestamp <= current_ts:
            sample = self.pending.popleft()
            
            if sample.direction == "DOWN":
                # 做空：价格下跌为正确
                actual_correct = current_price <= sample.entry_price
                self.model_down.learn_one(sample.features, actual_correct)
                self.stats["down"]["total"] += 1
                if actual_correct:
                    self.stats["down"]["correct"] += 1
            else:
                # 做多：价格上涨为正确
                actual_correct = current_price >= sample.entry_price
                self.model_up.learn_one(sample.features, actual_correct)
                self.stats["up"]["total"] += 1
                if actual_correct:
                    self.stats["up"]["correct"] += 1
            
            results.append({
                "direction": sample.direction,
                "entry_price": sample.entry_price,
                "settle_price": current_price,
                "is_win": actual_correct,
            })
        
        return results
    
    @property
    def accuracy_down(self) -> float:
        """做空模型准确率"""
        s = self.stats["down"]
        return s["correct"] / s["total"] if s["total"] > 0 else 0.5
    
    @property
    def accuracy_up(self) -> float:
        """做多模型准确率"""
        s = self.stats["up"]
        return s["correct"] / s["total"] if s["total"] > 0 else 0.5
    
    @property
    def total_samples(self) -> int:
        return self.stats["down"]["total"] + self.stats["up"]["total"]
    
    @property
    def pending_count(self) -> int:
        return len(self.pending)
    
    def save(self, path: str):
        """保存模型"""
        with open(path, 'wb') as f:
            pickle.dump(self, f)
    
    @classmethod
    def load(cls, path: str) -> 'RiverModel':
        """加载模型"""
        with open(path, 'rb') as f:
            return pickle.load(f)
