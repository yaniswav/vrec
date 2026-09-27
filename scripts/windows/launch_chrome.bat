@echo off
setlocal
cd /d "%~dp0..\.."

if not defined VREC_CHROME_PORT set "VREC_CHROME_PORT=9222"
if not defined VREC_CHROME_PROFILE set "VREC_CHROME_PROFILE=%LocalAppData%\vrec\chrome-profile"

set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
    echo Chrome not found.
    pause
    exit /b 1
)

start "" "%CHROME%" --remote-debugging-port=%VREC_CHROME_PORT% --user-data-dir="%VREC_CHROME_PROFILE%" --autoplay-policy=no-user-gesture-required --disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows
