@echo off
setlocal
cd /d "%~dp0..\.."
rem First-run wizard: creates your files, checks OBS/Chrome/VB-CABLE, sets up the optional features.
py -m vrec --setup --pause-on-exit %*
