@echo off
setlocal
cd /d "%~dp0..\.."
py -m vrec --test --pause-on-exit %*
