@echo off
setlocal
set "PYTHONPATH=%~dp0..\..\src;%~dp0..\.."
python "%~dp0run_leader_momentum_prospective_control.py" %*
exit /b %ERRORLEVEL%
