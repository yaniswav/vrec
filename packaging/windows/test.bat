@echo off
cd /d "%~dp0"
"%~dp0vrec.exe" --test --pause-on-exit %*
