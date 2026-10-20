# 基于北斗/GNSS的山体桥梁形变智能预警系统

> 智守山河 · 中国国际大学生创新大赛校赛项目
> 后端 FastAPI + WebSocket + SQLite，前端 Vue 3 + ECharts。
>
> 当前进度与晚上继续的步骤见：[PROGRESS.md](PROGRESS.md)。

---

## 1. 当前状态（2026-10-20）

| 模块 | 状态 |
| --- | --- |
| 仿真数据 -> 后端解算 -> 前端大屏 | 已打通 |
| 后端单元测试 | **29/29 通过** |
| 前端生产构建 | **通过**（输出到 `static/`） |
| BT800S 厂商 `$RUN` 报文解析 | 已实现严格字段校验与差分状态识别 |
| 真实移动站数据 | **尚未采集**；首次接线后必须先用原始日志验证协议 |
| 基站坐标 | 老师给定值仅写入本地 `config.json` 参考坐标 |

> 重要：真实串口模式下，`acquisition.protocol_verified` 默认是 `false`。
> 此时界面只显示设备/只读采集状态，**不计算、不展示任何真实位移**。
> 只有确认原始日志字段、单位、差分状态后，才允许手动改为 `true`。

---

## 2. 快速启动（仿真演示）

Windows 下双击：

```text
run.bat
```

或手动：

```bash
.venv\Scripts\python.exe main.py --mode simulation
```

浏览器打开 <http://127.0.0.1:8000>。

当前大屏包含：

- 三台设备状态（BASE / R01 / R02）、在线状态、卫星数、解状态；
- 相对位移曲线（合成 / 水平 / 竖向）与速率切换；
- 四级预警、判断依据、告警事件确认；
- 观测质量、采样率、时延、基线、粗差剔除；
- 只读采集状态与原始日志信息；
- 仿真场景控制（稳定、缓慢滑动、加速、阶跃、振荡、回归）；
- 全程不展示经纬度、不展示地图；仿真数据带 `SIM` 标识。

---

## 3. 基站坐标（老师已配置）

本项目**不会**向设备发送任何坐标配置命令。老师给定的基站坐标仅作为软件内部
WGS84 -> ENU 解算参考，已写入 `config.json`：

```json
"reference": {
  "lat": 31.9831969,
  "lon": 119.6265216,
  "alt": 18.1001593,
  "auto": false
}
```

严禁发送或执行：

```text
$WJG,AUTO
$WJG,FIX POSITION ...
$WCH、$RCH、$QFR、$QFP ...
```

前端接口 `/api/snapshot`、`/api/config` 均不下发经纬度。

### 3.1 移动站坐标相对基站

移动站不保存、也不使用独立的绝对坐标。真实采集时优先直接使用 BT800S
`$RUN` 帧中的 `E/N/U`（相对基线分量，单位 m）；NMEA 模式下则用移动站与
基准站的同步观测做差，得到相对基线的 ENU 偏移。

仿真站点同样以基站为原点配置 `relative_enu_m`：

```json
{"id": "base",   "relative_enu_m": [0, 0, 0]}
{"id": "rover1", "relative_enu_m": [78, -6, 0]}
{"id": "rover2", "relative_enu_m": [134, 24, 6]}
```

`base` 的大地坐标只作为内部 WGS84 -> ECEF -> ENU 原点；界面展示的位移始终是
“相对本次会话初始基线”的变化量，不是经纬度，也不是绝对坐标。

---

## 4. 移动站第一次接线测试

### 4.1 准备

1. 移动站上电，USB 线接电脑；
2. 如系统未识别串口，安装资料包中的 CH340 驱动；
3. 关闭可能占用该 COM 口的厂家上位机、串口助手；
4. **不要动基站**，不要重新固定基站坐标。

### 4.2 列出串口

```bash
.venv\Scripts\python.exe tools\probe.py --list
```

拔插 USB，观察哪个 COM 口出现/消失。

### 4.3 只读探测（先不发送命令）

```bash
.venv\Scripts\python.exe tools\probe.py --port COM3 --seconds 15
```

如果设备没有主动输出，再显式启动数据流：

```bash
.venv\Scripts\python.exe tools\probe.py --port COM3 --send-run --seconds 20
```

`--send-run` 只发送一次 `$RUN,`，作用是让设备开始连续输出；
它**不修改**坐标、电台频率、波特率、基站模式等任何参数。

正常时会看到类似：

```text
$RUN  RTK固定解      卫星 22  基线 123.456m  E/N/U 0.010/0.005/-0.002m
```

如果看到的是 NMEA，则会显示 `GGA` 等语句与卫星数。

### 4.4 保存原始日志（协议验证的关键证据）

```bash
.venv\Scripts\python.exe tools\collect_raw.py --port COM3 --baud 115200 --send-run --seconds 60
```

不知道波特率时可用 `--baud 0`，工具会扫描 9600 / 115200 等候选值。
日志保存在 `logs/raw_COM3_mobile_*.bin`，原样保存全部字节。

### 4.5 分析日志

```bash
.venv\Scripts\python.exe tools\analyze_raw.py logs\raw_COM3_mobile_20261020_120000.bin
```

重点核对：

- 是否识别到 `$RUN` 帧；
- RUN 帧字段数、E/N/U、基线长度是否合理；
- 差分状态分布，`4` 才代表 **RTK 固定解**；
- 与老师提供的设备固件版本/资料是否一致。

### 4.6 启用真实串口采集

验证通过后，编辑 `config.json`：

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

```text
run_serial.bat
```

或：

```bash
.venv\Scripts\python.exe main.py --mode serial
```

注意：`protocol_verified` 必须是在**看过真实原始日志并核对字段后**才改为 `true`，
不要为了演示提前打开。即使已打开，非 RTK 固定解仍会显示“精度不足”，不会出数。

---

## 5. 关于桌面上的 `bd(3).py`

不需要原样写进项目。它只做“选串口 -> 发送 `$RUN,` -> `readline()` 打印”：

- `$RUN` 是连续文本流，不一定带换行，`readline()` 容易粘连或读不到；
- 不保存原始日志；
- 不解析差分状态、基线、E/N/U；
- 没有只读保护说明。

项目里的 `tools/probe.py`、`tools/collect_raw.py`、`tools/analyze_raw.py`
已经覆盖并增强了这些功能，且默认只读。

---

## 6. 测试与构建（真实结果）

后端：

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果：`Ran 29 tests in 0.108s / OK`。

前端：

```bash
cd frontend
npm install
npm run build
```

结果：Vite 构建成功，输出 `static/index.html` 与 `static/assets/*`。
构建有一条 ECharts 体积提示（单包约 1.15 MB），不影响运行。

---

## 7. 目录说明

```text
app/
  run_protocol.py    BT800S $RUN 严格解析（无设备写入）
  nmea.py            NMEA 解析 + Fix 数据结构
  serial_reader.py   串口只读采集、自动波特率、原始日志
  engine.py          基准建立、卡尔曼、位移、预警、会话隔离
  features.py        速率/噪声/趋势/外推
  alerts.py          四级预警与迟滞
  simulator.py       仿真场景（带 SIM 标识）
  server.py          REST + WebSocket + 静态大屏
frontend/            Vue 3 + Vite + ECharts 源码
static/              构建产物（由 Vite 输出）
tools/
  probe.py           串口探测（可选 --send-run）
  collect_raw.py     原始字节保存（可选 --send-run）
  analyze_raw.py     原始日志协议检查
tests/               29 项后端单元测试
logs/                原始串口日志
data/monitor.db      SQLite 历史数据（大屏只显示本次会话曲线）
```

---

## 8. 真实设备启用前的硬性检查

- [ ] 已保存至少 60 秒原始日志；
- [ ] `analyze_raw.py` 能稳定解析 `$RUN` 帧；
- [ ] 已人工核对 E/N/U 单位与方向（东/北/天）；
- [ ] 差分状态为 `4`（RTK 固定）时才允许毫米级形变；
- [ ] 未发送任何 `$WJG` / 电台 / 坐标配置命令；
- [ ] 前端仍不出现经纬度、地图或伪造的真实形变值。
