@echo off
cd /d "%~dp0"
"%~dp0vrec.exe" --display on --pause-on-exit %*
