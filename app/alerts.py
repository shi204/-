"""四级预警决策：规则阈值 + 统计异常标志 + 趋势外推。"""
from __future__ import annotations

from typing import Any

LEVEL_NAMES = ["正常", "蓝色预警", "黄色预警", "橙色预警", "红色预警"]
LEVEL_COLORS = ["#22d3ee", "#3b82f6", "#fbbf24", "#fb923c", "#ef4444"]
LEVEL_KEYS = ["blue", "yellow", "orange", "red"]


class AlertEngine:
    def __init__(self, thresholds: dict[str, Any]) -> None:
        self.cfg = thresholds
        self.level = 0
        self.up_count = 0
        self.down_count = 0
        self.last_level = 0

    def update_thresholds(self, thresholds: dict[str, Any]) -> None:
        self.cfg = thresholds

    def judge(self, f: dict[str, Any]) -> dict[str, Any]:
        cfg = self.cfg
        d = max(float(f.get("h_mm", 0.0)), abs(float(f.get("v_mm", 0.0))))
        v = max(0.0, float(f.get("speed_mm_min", 0.0)))

        # 速率只在位移达到蓝色阈值后才参与判级，避免静态观测噪声虚警。
        # 加速度由带噪位置差分估计，置信度不足，仅作趋势展示，不单独触发告警。
        blue_d = float(cfg.get("blue", {}).get("d", 10.0))
        if d < max(1.0, blue_d):
            v = 0.0

        raw_level = 0
        reasons: list[str] = []
        for level, key in enumerate(LEVEL_KEYS, start=1):
            th = cfg.get(key, {})
            hit_d = d >= float(th.get("d", 1e9))
            hit_v = v >= float(th.get("v", 1e9))
            if hit_d or hit_v:
                raw_level = level
                reasons = []
                if hit_d:
                    reasons.append(f"累计形变 {d:.1f} mm ≥ {th.get('d')} mm")
                if hit_v:
                    reasons.append(f"变形速率 {v:.2f} mm/min ≥ {th.get('v')} mm/min")

        up_need = int(cfg.get("up_samples", 3))
        down_need = int(cfg.get("down_samples", 20))
        if raw_level > self.level:
            self.up_count += 1
            self.down_count = 0
            if self.up_count >= up_need:
                self.level = raw_level
                self.up_count = 0
        elif raw_level < self.level:
            self.down_count += 1
            self.up_count = 0
            if self.down_count >= down_need:
                self.level = raw_level
                self.down_count = 0
        else:
            self.up_count = 0
            self.down_count = 0

        anomaly = bool(f.get("sigma_anomaly")) or bool(f.get("cusum_anomaly"))
        if anomaly and self.level == 0 and raw_level == 0:
            reasons.append("统计异常：3σ/CUSUM 检测到趋势漂移")

        # 趋势外推到下一级阈值
        next_level = min(4, self.level + 1)
        next_key = LEVEL_KEYS[next_level - 1] if next_level >= 1 else "blue"
        next_d = float(cfg.get(next_key, {}).get("d", 0.0))
        slope = float(f.get("slope_mm_min", 0.0))
        tta_min: float | None = None
        if next_d > 0 and d < next_d and slope > 0.02:
            tta_min = (next_d - d) / slope
        elif next_d > 0 and d >= next_d:
            tta_min = 0.0

        trend = "stable"
        if slope > 0.15:
            trend = "rising"
        elif slope < -0.15:
            trend = "falling"
        if float(f.get("accel_trend_mm_min2", 0.0)) > 0.02:
            trend = "accelerating"

        return {
            "level": int(self.level),
            "raw_level": int(raw_level),
            "name": LEVEL_NAMES[self.level],
            "color": LEVEL_COLORS[self.level],
            "reasons": reasons,
            "anomaly": anomaly,
            "trend": trend,
            "next_threshold_mm": next_d if next_d > 0 else None,
            "time_to_threshold_min": round(tta_min, 2) if tta_min is not None else None,
        }

    def level_changed(self) -> bool:
        changed = self.level != self.last_level
        self.last_level = self.level
        return changed
