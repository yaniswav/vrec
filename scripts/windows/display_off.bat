@echo off
setlocal
cd /d "%~dp0..\.."
py -m vrec --display off --pause-on-exit %*
