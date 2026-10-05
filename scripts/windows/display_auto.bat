@echo off
setlocal
cd /d "%~dp0..\.."
py -m vrec --display auto --pause-on-exit %*
