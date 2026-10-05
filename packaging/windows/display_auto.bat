@echo off
cd /d "%~dp0"
"%~dp0vrec.exe" --display auto --pause-on-exit %*
