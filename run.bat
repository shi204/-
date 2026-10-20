@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
    echo [1/2] 正在创建 Python 虚拟环境...
    uv venv --python 3.12 .venv
    echo [2/2] 正在安装依赖...
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
)
echo 启动形变预警系统（仿真演示模式）...
.venv\Scripts\python.exe main.py --mode simulation
pause
