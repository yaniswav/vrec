@echo off
setlocal
cd /d "%~dp0..\.."
py -m vrec --pause-on-exit %*
