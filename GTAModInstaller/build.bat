@echo off
setlocal
cd /d "%~dp0"

python -m pip install -r requirements.txt
if errorlevel 1 goto :error

rem OneDrive/Defender sometimes keep handles on the previous build output;
rem retry the cleanup instead of failing the whole build.
set RETRIES=0
:cleandist
if not exist "dist\GTAModInstaller" goto :build
rmdir /s /q "dist\GTAModInstaller" >nul 2>&1
if not exist "dist\GTAModInstaller" goto :build
set /a RETRIES+=1
if %RETRIES% GEQ 10 (
    echo Не удалось удалить dist\GTAModInstaller. Закройте программу и повторите.
    goto :error
)
timeout /t 2 /nobreak >nul
goto :cleandist

:build
python -m PyInstaller --noconfirm GTAModInstaller.spec
if errorlevel 1 goto :error

rem Требуется установленный 7-Zip (или переменная SEVENZIP с путём к 7z.exe)
python tools\make_sfx.py
if errorlevel 1 goto :error

echo.
echo Готово: %CD%\dist\GTAModInstaller.exe
exit /b 0

:error
echo.
echo Сборка не удалась.
exit /b 1
