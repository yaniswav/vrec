@echo off
cd /d "%~dp0"
"%~dp0vrec.exe" --display off --pause-on-exit %*
