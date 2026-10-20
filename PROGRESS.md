# 项目进度存档

> 存档时间：2026-10-20 12:50
> 项目目录：`C:\Users\Lenovo\Desktop\deform-warning`
> 晚上继续时，先读本文件，再按“恢复步骤”启动。

---

## 1. 当前结论

- 前端 + 后端已打通，**仿真演示版本可用**。
- 页面：<http://127.0.0.1:8000/>（当前服务已停止，启动命令见第 5 节）。
- 最新大屏截图：[docs/dashboard_2026-10-20.png](docs/dashboard_2026-10-20.png)。
- 真实移动站**尚未接入**，真实原始数据**尚未采集**，`$RUN` 协议**尚未用真实日志验证**。
- `acquisition.protocol_verified` 仍为 `false`；真实串口模式下不会计算或展示真实位移。
- 没有向任何设备发送过 `$WJG`、`FIX POSITION`、电台、波特率或坐标配置命令。

---

## 2. 今天完成的内容

### 2.1 前端

- Vue 3 + Vite + ECharts 大屏完成，构建输出到 `static/`。
- 页面包含：
  - BASE / R01 / R02 三台设备状态；
  - 当前监测站主指标、H/U/E/N 分量、速率、加速度、噪声；
  - 相对位移曲线（合成 / 水平 / 竖向，位移 / 速率切换）；
  - 四级预警、告警事件确认；
  - 观测质量、采样率、时延、基线长度、粗差剔除；
  - 三台设备采集状态；
  - 仿真场景控制（稳定、缓慢、加速、阶跃、振荡、回归、重置、CSV）。
- 前端不展示经纬度、不展示地图。
- 仿真数据全程带 `SIM` 标识。
- 协议待验证、非 RTK 固定解、设备离线、基准建立中时，位移值锁定，不显示伪造数据。
- 采集状态改为三列卡片，三台设备可同时看到，不再只露出 BASE。
- 曲线纵轴至少覆盖第一级阈值（蓝色 12mm），阈值线不会被稳定期曲线挤出画面。
- 基准站趋势显示为 `REF · 参考`，不再显示“加速”等误导性趋势。
- 基准建立期间不再写入零位移样本，曲线不会出现虚假的“实测零位移”平台。

### 2.2 后端

- FastAPI + WebSocket + SQLite WAL + pyserial 只读采集链路完成。
- BT800S `$RUN` 协议严格解析、NMEA 解析、差分状态识别完成。
- 卡尔曼滤波、特征提取、四级预警与迟滞完成。
- 会话隔离：每次启动重新建立相对位移零点，不展示上一次演示的历史曲线/告警。
- 移动站坐标全部相对基站：
  - 仿真站点使用 `relative_enu_m`：
    - BASE `[0, 0, 0]`
    - R01 `[78, -6, 0]`，基线约 78.23 m
    - R02 `[134, 24, 6]`，基线约 136.26 m
  - 真实 `$RUN` 直接使用帧内 E/N/U 相对基线分量；
  - NMEA 模式使用移动站与基准站同步观测做差。
- 基站坐标只作为后端内部 WGS84 -> ENU 原点，`/api/config` 与 `/api/snapshot` 均不下发经纬度。
- 仿真噪声已调低：`noise_mm=0.6`，`common_noise_mm=0.8`。

### 2.3 只读采集工具

- `tools/probe.py`：串口探测，支持 `--list`，默认只读；只有显式 `--send-run` 才发送一次 `$RUN,`。
- `tools/collect_raw.py`：按块读取并原样保存原始字节，兼容无换行的 `$RUN` 流。
- `tools/analyze_raw.py`：统计 `$RUN` 帧、差分状态分布、E/N/U 与基线。
- 桌面上的 `bd(3).py` 未原样集成：它用 `readline()`、不存原始日志、不校验协议，不适合作为验证工具。

---

## 3. 最近一次真实验证结果

### 后端测试

```text
Ran 29 tests in 0.106s
OK
```

原始日志：[docs/tests_2026-10-20.log](docs/tests_2026-10-20.log)

### 前端构建

```text
585 modules transformed
static/assets/index-B8kx-NcO.js
static/assets/index-BELupmTv.css
built in 6.70s
```

唯一提示：ECharts 单包约 1.15 MB，超过 Vite 500 kB 警告线，不影响运行。

原始日志：[docs/build_2026-10-20.log](docs/build_2026-10-20.log)

### 接口检查

```text
/api/health  -> ok=true, mode=simulation, online=3, stations=3
/api/snapshot -> R01 baseline_m=78.23, R02 baseline_m=136.264
             -> 三站响应中无 lat / lon 字段
/api/config  -> 仅返回 mode / stations / thresholds
```

---

## 4. 晚上继续时的下一步

### 4.1 接入设备

现场硬件：**基站 + 两个移动站均通过同一个 USB（集线器）接入电脑**。

1. 三台设备上电，USB 接电脑。
2. 如果系统没识别串口，安装资料包中的 CH340 驱动。
3. 关闭厂家上位机、串口助手等占用 COM 口的软件。
4. **不要动基站**，不要重新固定基站坐标。
5. 先确认插上这个 USB 后，Windows 出现的是 **3 个 COM 口**还是 **1 个 COM 口**：
   - 3 个 COM 口：一站一口，直接分别配置 BASE / R01 / R02；
   - 只有 1 个 COM 口：说明三台设备的数据可能复用在同一条流里，
     当前程序不能自动区分来源，必须先保存原始日志，确认帧内是否有设备标识，
     不能把三个站点都配成同一个 COM 口。
6. 如果是无源 USB 集线器且出现掉线，换用带电源的集线器。

### 4.2 确认 COM 口

```bash
cd C:\Users\Lenovo\Desktop\deform-warning
.venv\Scripts\python.exe tools\probe.py --list
```

拔插 USB，观察哪个 COM 口出现/消失。

### 4.3 只读探测

```bash
.venv\Scripts\python.exe tools\probe.py --port COM3 --seconds 15
```

如果设备没有主动输出，再显式启动数据流：

```bash
.venv\Scripts\python.exe tools\probe.py --port COM3 --send-run --seconds 20
```

`--send-run` 只发送一次 `$RUN,`，作用是让设备开始连续输出，不修改任何设备参数。

### 4.4 保存 60 秒原始日志

```bash
.venv\Scripts\python.exe tools\collect_raw.py --port COM3 --baud 115200 --send-run --seconds 60
```

不知道波特率时用 `--baud 0` 自动扫描。日志保存到 `logs/raw_COM3_mobile_*.bin`。

### 4.5 分析原始日志

```bash
.venv\Scripts\python.exe tools\analyze_raw.py logs\raw_COM3_mobile_*.bin
```

必须人工核对：

- [ ] 能稳定解析 `$RUN` 帧；
- [ ] 帧字段顺序与厂商资料一致；
- [ ] E/N/U 单位是米，方向是东/北/天；
- [ ] 差分状态 `4` = RTK 固定解；
- [ ] 基线长度与实际点位距离合理；
- [ ] 原始日志中无异常乱码或错位。

### 4.6 验证通过后才启用真实解算

编辑 `config.json`：

```json
"stations": [
  {
    "id": "rover1",
    "port": "COM3",
    "baud": 115200
  }
],
"acquisition": {
  "protocol_verified": true
}
```

然后启动实时模式：

```bash
.venv\Scripts\python.exe main.py --mode serial
```

注意：即使 `protocol_verified=true`，非 RTK 固定解仍会显示“定位精度不足”，不会输出毫米级位移。

---

## 5. 恢复步骤

启动仿真演示：

```bash
cd C:\Users\Lenovo\Desktop\deform-warning
.venv\Scripts\python.exe main.py --no-browser
```

浏览器打开 <http://127.0.0.1:8000/>，建议按 `F11` 全屏。

重新跑测试：

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

重新构建前端：

```bash
cd frontend
npm run build
```

---

## 6. 已知问题 / 注意事项

- 真实移动站数据未采集，真实协议未验证；这是当前最大的未完成项。
- 在约 928px 的浏览器内容高度下，右侧“预警态势”底部的告警事件列表需要滚动查看；全屏 1080p 下空间更充足。
- 构建有 ECharts 体积提示，仅影响打包体积，不影响演示。
- 前端不展示经纬度、地图或绝对坐标；只展示相对基线与相对位移。
- 严禁发送：
  - `$WJG,AUTO`
  - `$WJG,FIX POSITION ...`
  - `$WCH`、`$RCH`、`$QFR`、`$QFP`
  - 任何电台、波特率、坐标配置命令

---

## 7. 关键文件

| 文件 | 作用 |
| --- | --- |
| `frontend/src/App.vue` | 大屏主布局 |
| `frontend/src/components/AcquisitionPanel.vue` | 三台设备采集状态 |
| `frontend/src/components/DisplacementChart.vue` | ECharts 相对位移曲线 |
| `frontend/src/components/StationCard.vue` | 设备状态卡 |
| `app/simulator.py` | 仿真数据、相对基站坐标 |
| `app/engine.py` | 解算、预警、会话隔离 |
| `app/run_protocol.py` | BT800S `$RUN` 解析 |
| `tools/probe.py` | 串口只读探测 |
| `tools/collect_raw.py` | 原始字节保存 |
| `tools/analyze_raw.py` | 原始日志协议检查 |
| `config.json` | 串口、阈值、仿真与验证开关 |
| `README.md` | 使用说明与安全检查清单 |
