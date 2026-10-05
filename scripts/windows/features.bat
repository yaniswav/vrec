@echo off
setlocal
cd /d "%~dp0..\.."
rem Opens the features on/off screen.
py -m vrec --features-menu --pause-on-exit %*
