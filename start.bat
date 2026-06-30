@echo off
chcp 65001 >nul
title 悟道 (Wudao) v0.7.2-dev

echo =============================================
echo   悟道 (Wudao) - 曹峰的AI伙伴
echo   版本: v0.7.2-dev
echo =============================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.9+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] 检查依赖...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [警告] pip 安装失败，尝试手动安装：
    echo   pip install -r requirements.txt
)

:: 检查 .env 文件
if not exist .env (
    echo [提示] 未找到 .env 文件，正在从 .env.example 创建...
    copy .env.example .env >nul
    echo [!!!] 请编辑 .env 文件，填入你的 API Key！
    echo       推荐使用 DeepSeek: https://platform.deepseek.com/api_keys
    echo.
    echo       按任意键用记事本打开 .env 进行编辑...
    pause >nul
    start notepad .env
    echo 编辑完成后，重新运行本脚本即可启动。
    pause
    exit /b 0
)

echo [2/3] 创建数据目录...
if not exist data mkdir data
if not exist data\learned mkdir data\learned
if not exist data\scenes mkdir data\scenes

echo [3/3] 启动服务...
echo.
echo 服务启动后，请访问：
echo   http://localhost:8002
echo.
echo 按 Ctrl+C 停止服务
echo =============================================
echo.

set WUDAO_DATA=%cd%\data
set PORT=8002

python -m uvicorn main:app --host 0.0.0.0 --port %PORT% --reload

if %errorlevel% neq 0 (
    echo.
    echo [错误] 启动失败，请检查：
    echo   1. .env 文件中的 API Key 是否正确
    echo   2. 端口 8002 是否被占用
    echo   3. 运行 python main.py 查看详细错误
    pause
)
