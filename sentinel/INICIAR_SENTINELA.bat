@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================================
echo  LUMI SENTINEL VISION - inicializacao
echo ========================================================

if not exist "venv\Scripts\python.exe" (
    echo [1/3] Criando ambiente Python...
    py -3 -m venv venv
    if errorlevel 1 (
        echo [ERRO] Nao consegui criar o venv. Instale Python 3.11+ de 64 bits.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Ambiente Python ja existe.
)

echo [2/3] Verificando dependencias...
venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias (verifique a internet).
    pause
    exit /b 1
)

echo [3/3] Iniciando Sentinel...
echo.
venv\Scripts\pythonw.exe sentinel.py
if errorlevel 1 (
    rem se pythonw nao logar erro, mostra a saida para diagnosticar
    venv\Scripts\python.exe sentinel.py
)
