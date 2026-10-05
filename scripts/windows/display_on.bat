@echo off
setlocal
cd /d "%~dp0..\.."
py -m vrec --display on --pause-on-exit %*
