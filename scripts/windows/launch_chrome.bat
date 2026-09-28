@echo off
setlocal
cd /d "%~dp0..\.."
rem Opens the recording Chrome on the virtual screen (maximized), e.g. to log in to the site.
py -m vrec --launch-chrome --pause-on-exit %*
