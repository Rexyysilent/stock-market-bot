@echo off
cd /d "%~dp0"
echo Exporting market intelligence daily brief...
.\.venv\Scripts\python.exe export_for_gemini.py
echo.
echo Files saved:
echo   - daily_brief.txt (human readable)
echo   - daily_brief.json (structured for LLM)
pause

