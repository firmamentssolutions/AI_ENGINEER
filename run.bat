@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Environnement Python absent. Lancez d'abord INSTALLER.bat ou suivez README.md.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
streamlit run app.py
pause
