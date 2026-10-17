@echo off
setlocal
set "PYTHONPATH=%~dp0..\\..\\src;%~dp0..\\.."
python "%~dp0run_leader_momentum_evolving_top_gainers.py" %*
exit /b %ERRORLEVEL%
