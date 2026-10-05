@echo off
cd /d "%~dp0"
"%~dp0vrec.exe" --features-menu --pause-on-exit %*
