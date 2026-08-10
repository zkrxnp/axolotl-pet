@echo off
rem 아루 실행. pythonw 로 띄워야 검은 콘솔 창이 같이 안 뜬다.
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw axo.py) || (start "" python axo.py)
