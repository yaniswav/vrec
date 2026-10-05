@echo off
setlocal
cd /d "%~dp0..\.."
py -m vrec --display status --pause-on-exit %*
