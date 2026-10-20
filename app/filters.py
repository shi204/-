"""卡尔曼滤波与稳健回归工具。"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class FilterOutput:
    pos: tuple[float, float, float]
    vel: tuple[float, float, float]
    acc: tuple[float, float, float]
    rejected: bool = False
    reset: bool = False


class KalmanCV:
    """三维匀速（CV）模型卡尔曼滤波。

    状态: [E,N,U, vE,vN,vU]
    选择 CV 而不是 CA：低成本接收机的加速度状态几乎不可观测，
    CA 模型容易在低测量噪声下发散，产生非物理的数百毫米漂移。
    加速度特征改由位移趋势（前后两个速度窗口之差）估计。

    - 对 GNSS 野值做新息门限抑制；
    - 连续多次被拒时认为发生真实阶跃，自动重置到新位置。
    """

    def __init__(
        self,
        dt: float = 1.0,
        meas_var: float = 0.25,
        process_noise: float = 0.008,
        gate_sigma: float = 5.0,
        max_reject: int = 5,
        max_gap: float = 15.0,
    ) -> None:
        self.dt = dt
        self.meas_var = max(meas_var, 1e-8)
        self.q = max(process_noise, 1e-10)
        self.gate2 = gate_sigma * gate_sigma
        self.max_reject = max_reject
        self.max_gap = max_gap
        self.x = np.zeros(6)
        self.P = np.eye(6)
        self.initialized = False
        self.last_t = 0.0
        self.reject_count = 0
        self.total_rejected = 0

    def reset(self, z: np.ndarray | tuple[float, float, float], t: float) -> None:
        self.x = np.zeros(6)
        self.x[:3] = np.asarray(z, dtype=float)
        self.P = np.eye(6) * 25.0
        self.P[3:6, 3:6] *= 0.01
        self.last_t = t
        self.initialized = True
        self.reject_count = 0

    def set_measurement_noise(self, sigma_m: float) -> None:
        self.meas_var = max(sigma_m * sigma_m, 1e-8)

    def update(self, z: tuple[float, float, float], t: float) -> FilterOutput:
        z_arr = np.asarray(z, dtype=float)
        if not self.initialized:
            self.reset(z_arr, t)
            return FilterOutput(tuple(z_arr), (0, 0, 0), (0, 0, 0), reset=True)

        dt = t - self.last_t
        if dt <= 0:
            return self._output(False, False)
        if dt > self.max_gap:
            self.reset(z_arr, t)
            return FilterOutput(tuple(z_arr), (0, 0, 0), (0, 0, 0), reset=True)
        dt = float(np.clip(dt, 0.05, self.max_gap))

        f = np.eye(6)
        for i in range(3):
            f[i, i + 3] = dt

        g = np.zeros((6, 3))
        for i in range(3):
            g[i, i] = 0.5 * dt * dt
            g[i + 3, i] = dt
        q = self.q * (g @ g.T)

        self.x = f @ self.x
        self.P = f @ self.P @ f.T + q
        self.P = 0.5 * (self.P + self.P.T)

        h = np.zeros((3, 6))
        h[0, 0] = h[1, 1] = h[2, 2] = 1.0
        r = np.eye(3) * self.meas_var
        innovation = z_arr - h @ self.x
        s = h @ self.P @ h.T + r
        try:
            d2 = float(innovation @ np.linalg.solve(s, innovation))
        except np.linalg.LinAlgError:
            d2 = 0.0

        if d2 > self.gate2 and self.reject_count < self.max_reject:
            self.reject_count += 1
            self.total_rejected += 1
            self.last_t = t
            return self._output(True, False)

        if d2 > self.gate2 and self.reject_count >= self.max_reject:
            # 持续偏离：接受为真实形变阶跃
            self.reset(z_arr, t)
            return FilterOutput(tuple(z_arr), (0, 0, 0), (0, 0, 0), reset=True)

        self.reject_count = 0
        k = self.P @ h.T @ np.linalg.inv(s)
        self.x = self.x + k @ innovation
        self.P = (np.eye(6) - k @ h) @ self.P
        self.P = 0.5 * (self.P + self.P.T)
        self.last_t = t
        return self._output(False, False)

    def _output(self, rejected: bool, reset: bool) -> FilterOutput:
        return FilterOutput(
            pos=tuple(float(v) for v in self.x[:3]),
            vel=tuple(float(v) for v in self.x[3:6]),
            acc=(0.0, 0.0, 0.0),
            rejected=rejected,
            reset=reset,
        )


# 兼容旧导入名
KalmanCA = KalmanCV


def rolling_fit(
    times: np.ndarray, values: np.ndarray
) -> tuple[float, float, float]:
    """一次线性回归，返回 (slope, intercept, residual_std)。"""
    n = len(times)
    if n < 2:
        return 0.0, float(values[-1]) if n else 0.0, 0.0
    t_mean = float(np.mean(times))
    v_mean = float(np.mean(values))
    dt = times - t_mean
    denom = float(np.dot(dt, dt))
    if denom <= 1e-12:
        return 0.0, v_mean, 0.0
    slope = float(np.dot(dt, values - v_mean) / denom)
    intercept = v_mean - slope * t_mean
    resid = values - (slope * times + intercept)
    return slope, intercept, float(np.sqrt(np.mean(resid * resid)))


def quadratic_fit(
    times: np.ndarray, values: np.ndarray
) -> tuple[float, float, float]:
    """二次拟合，返回 (a2, a1, a0)，用于加速趋势判断。"""
    if len(times) < 4:
        slope, intercept, _ = rolling_fit(times, values)
        return 0.0, slope, intercept
    t0 = float(np.mean(times))
    dt = times - t0
    try:
        coef = np.polyfit(dt, values, 2)
    except Exception:
        slope, intercept, _ = rolling_fit(times, values)
        return 0.0, slope, intercept
    return float(coef[0]), float(coef[1]), float(coef[2])


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def circular_mean_deg(angles: list[float]) -> float:
    if not angles:
        return 0.0
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(s, c)) % 360.0
