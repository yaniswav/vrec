@echo off
cd /d "%~dp0"
"%~dp0vrec.exe" --launch-chrome --pause-on-exit %*
