"""形变特征提取：位移、速率、加速度、方向角、噪声、3σ / CUSUM 异常、趋势外推。"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from .filters import quadratic_fit, rolling_fit


class FeatureExtractor:
    def __init__(
        self,
        calibration_samples: int = 30,
        velocity_window_s: float = 30.0,
        noise_window_s: float = 90.0,
        prediction_window_s: float = 120.0,
        max_samples: int = 7200,
    ) -> None:
        self.calibration_samples = max(5, int(calibration_samples))
        self.velocity_window_s = velocity_window_s
        self.noise_window_s = noise_window_s
        self.prediction_window_s = prediction_window_s
        self.samples: deque[tuple[float, float, float, float]] = deque(maxlen=max_samples)
        self.origin: tuple[float, float, float] | None = None
        self._calib: list[tuple[float, float, float]] = []
        self._acc_ema = 0.0
        self._cusum_pos = 0.0
        self._cusum_neg = 0.0
        self._cusum_flag_until = 0.0
        self._sigma_streak = 0
        self._prev_h: float | None = None

    # ------------------------------------------------------------------
    def reset(self) -> None:
        self.samples.clear()
        self.origin = None
        self._calib.clear()
        self._acc_ema = 0.0
        self._cusum_pos = self._cusum_neg = 0.0
        self._cusum_flag_until = 0.0
        self._sigma_streak = 0
        self._prev_h = None

    @property
    def calibrating(self) -> bool:
        return self.origin is None

    def add(
        self,
        t: float,
        pos: tuple[float, float, float],
        vel: tuple[float, float, float] | None = None,
        acc: tuple[float, float, float] | None = None,
    ) -> dict:
        self.samples.append((t, pos[0], pos[1], pos[2]))
        if self.origin is None:
            self._calib.append(pos)
            if len(self._calib) >= self.calibration_samples:
                arr = np.asarray(self._calib, dtype=float)
                med = np.median(arr, axis=0)
                self.origin = (float(med[0]), float(med[1]), float(med[2]))
            else:
                return self._empty(
                    t, calibrating=True, progress=len(self._calib) / self.calibration_samples
                )

        ox, oy, oz = self.origin
        de, dn, du = pos[0] - ox, pos[1] - oy, pos[2] - oz
        dh = math.hypot(de, dn)
        d_total = math.sqrt(de * de + dn * dn + du * du)

        # 滑动窗口内的速率：对 E/N/U 分别做线性回归
        t_arr = np.fromiter((s[0] for s in self.samples), dtype=float)
        e_arr = np.fromiter((s[1] for s in self.samples), dtype=float)
        n_arr = np.fromiter((s[2] for s in self.samples), dtype=float)
        u_arr = np.fromiter((s[3] for s in self.samples), dtype=float)
        now = t_arr[-1] if len(t_arr) else t
        vmask = t_arr >= now - self.velocity_window_s
        if int(vmask.sum()) >= 5:
            tv = t_arr[vmask] - now
            ve, _, _ = rolling_fit(tv, e_arr[vmask])
            vn, _, _ = rolling_fit(tv, n_arr[vmask])
            vu, _, _ = rolling_fit(tv, u_arr[vmask])
        else:
            ve, vn, vu = (vel or (0.0, 0.0, 0.0))
        speed = math.sqrt(ve * ve + vn * vn + vu * vu)
        speed_h = math.hypot(ve, vn)

        # 加速度：比较“最近一个速度窗口”与“上一个速度窗口”的速率变化。
        # 这比对带噪位置做二阶差分稳定得多。
        prev_mask = (
            (t_arr >= now - 2.0 * self.velocity_window_s)
            & (t_arr < now - self.velocity_window_s)
        )
        acc_raw = 0.0
        if int(vmask.sum()) >= 5 and int(prev_mask.sum()) >= 5:
            tp = t_arr[prev_mask] - now
            ve0, _, _ = rolling_fit(tp, e_arr[prev_mask])
            vn0, _, _ = rolling_fit(tp, n_arr[prev_mask])
            vu0, _, _ = rolling_fit(tp, u_arr[prev_mask])
            if d_total > 1e-6:
                ux, uy, uz = de / d_total, dn / d_total, du / d_total
            else:
                ux = uy = uz = 0.0
            acc_raw = (
                (ve - ve0) * ux + (vn - vn0) * uy + (vu - vu0) * uz
            ) / self.velocity_window_s * 3.6e6
            acc_raw = max(-200.0, min(200.0, acc_raw))
        self._acc_ema = 0.85 * self._acc_ema + 0.15 * acc_raw
        acc_proj = self._acc_ema

        # 噪声：去趋势后的水平残差标准差
        nmask = t_arr >= now - self.noise_window_s
        noise_m = 0.0
        zscore = 0.0
        if int(nmask.sum()) >= 12:
            tt = t_arr[nmask] - now
            hh = np.hypot(e_arr[nmask], n_arr[nmask])
            _, intercept, sigma = rolling_fit(tt, hh)
            noise_m = sigma
            if sigma > 1e-9:
                zscore = float((hh[-1] - intercept) / sigma)
        noise_mm = noise_m * 1000.0

        # 3σ 异常：连续 3 个点超限
        if abs(zscore) > 3.0:
            self._sigma_streak += 1
        else:
            self._sigma_streak = 0
        sigma_anomaly = self._sigma_streak >= 3

        # CUSUM：对水平位移增量做累积和
        cusum_anomaly = False
        if self._prev_h is not None and noise_mm > 0.01:
            delta = (dh - self._prev_h) * 1000.0 / max(noise_mm, 0.01)
            k = 0.5
            self._cusum_pos = max(0.0, self._cusum_pos + delta - k)
            self._cusum_neg = max(0.0, self._cusum_neg - delta - k)
            if self._cusum_pos > 5.0 or self._cusum_neg > 5.0:
                cusum_anomaly = True
                self._cusum_flag_until = now + 20.0
                self._cusum_pos = self._cusum_neg = 0.0
        self._prev_h = dh
        if now < self._cusum_flag_until:
            cusum_anomaly = True

        # 趋势外推（最近 2 分钟；与预警判级使用同一标量：
        # 取水平位移与竖向位移绝对值中的较大者，兼顾桥面挠度与边坡滑移）
        pmask = t_arr >= now - self.prediction_window_s
        slope_mm_min = 0.0
        accel_trend = 0.0
        if int(pmask.sum()) >= 10:
            tt = (t_arr[pmask] - now) / 60.0
            dd = np.maximum(
                np.hypot(e_arr[pmask], n_arr[pmask]), np.abs(u_arr[pmask])
            ) * 1000.0
            slope_mm_min, _, _ = rolling_fit(tt, dd)
            a2, _, _ = quadratic_fit(tt, dd)
            accel_trend = 2.0 * a2  # mm/min²

        direction = 0.0
        if dh > 2e-5:
            direction = math.degrees(math.atan2(de, dn)) % 360.0

        return {
            "ts": t,
            "calibrating": False,
            "calib_progress": 1.0,
            "de_m": de,
            "dn_m": dn,
            "du_m": du,
            "de_mm": de * 1000.0,
            "dn_mm": dn * 1000.0,
            "du_mm": du * 1000.0,
            "h_mm": dh * 1000.0,
            "v_mm": du * 1000.0,
            "total_mm": d_total * 1000.0,
            "speed_mm_s": speed * 1000.0,
            "speed_mm_min": speed * 60000.0,
            "speed_h_mm_min": speed_h * 60000.0,
            "accel_mm_min2": acc_proj,
            "accel_trend_mm_min2": acc_proj,
            "direction_deg": direction,
            "noise_mm": noise_mm,
            "zscore": zscore,
            "sigma_anomaly": sigma_anomaly,
            "cusum_anomaly": cusum_anomaly,
            "slope_mm_min": slope_mm_min,
            "origin": self.origin,
        }

    def _empty(self, t: float, calibrating: bool, progress: float) -> dict:
        return {
            "ts": t,
            "calibrating": calibrating,
            "calib_progress": progress,
            "de_m": 0.0,
            "dn_m": 0.0,
            "du_m": 0.0,
            "de_mm": 0.0,
            "dn_mm": 0.0,
            "du_mm": 0.0,
            "h_mm": 0.0,
            "v_mm": 0.0,
            "total_mm": 0.0,
            "speed_mm_s": 0.0,
            "speed_mm_min": 0.0,
            "speed_h_mm_min": 0.0,
            "accel_mm_min2": 0.0,
            "accel_trend_mm_min2": 0.0,
            "direction_deg": 0.0,
            "noise_mm": 0.0,
            "zscore": 0.0,
            "sigma_anomaly": False,
            "cusum_anomaly": False,
            "slope_mm_min": 0.0,
            "origin": None,
        }
