@echo off
cd /d "%~dp0"
"%~dp0vrec.exe" --doctor --pause-on-exit %*
