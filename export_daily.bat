@echo off
cd /d "%~dp0"
echo Exporting market intelligence for Gemini analysis...
.\venv\Scripts\python.exe export_for_gemini.py
echo.
echo Files saved:
echo   - gemini_daily_brief.txt (human readable)
echo   - gemini_daily_brief.json (structured for LLM)
pause

