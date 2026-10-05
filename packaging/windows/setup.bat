@echo off
cd /d "%~dp0"
"%~dp0vrec.exe" --setup --pause-on-exit %*
