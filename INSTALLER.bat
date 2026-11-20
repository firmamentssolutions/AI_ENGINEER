@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python n'a pas ete trouve. Installez Python 3.11+ et cochez Add Python to PATH.
  pause
  exit /b 1
)
python -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist ".env" copy ".env.example" ".env"
echo Installation terminee. Configurez .env puis lancez run.bat.
pause
