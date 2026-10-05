@echo off
cd /d "%~dp0"
"%~dp0vrec.exe" --display status --pause-on-exit %*
