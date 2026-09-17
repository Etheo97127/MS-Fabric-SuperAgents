@echo off
REM Convenience shim so `fabctl ...` works without setting PYTHONPATH each time.
setlocal
set "PYTHONPATH=%~dp0..;%PYTHONPATH%"
python -m fabctl %*
