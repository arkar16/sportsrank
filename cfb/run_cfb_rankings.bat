@echo off
cd /d "%~dp0"
python main.py %*
cd ..
python mainpage.py
pause
