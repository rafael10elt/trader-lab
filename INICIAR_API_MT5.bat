@echo off
setlocal
cd /d "%~dp0"
title MNQ Market Lab - API MT5

echo ============================================================
echo   MNQ MARKET LAB - SERVICO PYTHON / METATRADER 5
echo ============================================================
echo.

set "PYTHON_CMD="
py -3.11 --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.11"
if not defined PYTHON_CMD (
  py --version >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=py"
)
if not defined PYTHON_CMD (
  python --version >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo ERRO: Python nao encontrado. Instale Python 3.11 ou superior de 64 bits e tente novamente.
  echo Download: https://www.python.org/downloads/
  pause
  exit /b 1
)

%PYTHON_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
  echo ERRO: Este projeto requer Python 3.11 ou superior, 64 bits.
  pause
  exit /b 1
)

if not exist "backend\.venv\Scripts\python.exe" (
  echo Preparando ambiente virtual Python...
  %PYTHON_CMD% -m venv "backend\.venv"
  if errorlevel 1 goto :error
)

if not exist "backend\.env" (
  copy "backend\.env.example" "backend\.env" >nul
  echo Criado backend\.env a partir do modelo.
  echo Edite MT5_SYMBOL com o nome exato do ativo no seu broker.
  echo.
)

echo Instalando ou atualizando dependencias...
"backend\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check --only-binary=:all: -r "backend\requirements.txt"
if errorlevel 1 goto :error

echo.
echo API: http://127.0.0.1:8000
echo Status JSON: http://127.0.0.1:8000/health
echo Documentacao interativa: http://127.0.0.1:8000/docs
echo.
echo Deixe esta janela aberta. Para encerrar o servidor, pressione Ctrl+C.
echo Para dados reais, abra o MT5 e conecte sua conta demo antes de testar.
echo.
start "" "http://127.0.0.1:8000/health"
cd /d "%~dp0backend"
"%~dp0backend\.venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Ocorreu um erro ao iniciar. Leia a mensagem acima.
echo Confirme Python 3.11 64 bits, MT5 instalado e backend\.env configurado.
pause
exit /b 1
