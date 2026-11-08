@echo off
rem Builds ChestCounter.exe (Windows). Output: dist\ChestCounter\ChestCounter.exe
cd /d "%~dp0"

echo Installing build requirements...
python -m pip install -r requirements.txt pyinstaller || goto :fail

echo Building...
python -m PyInstaller --noconfirm --clean --windowed --name ChestCounter --icon icon.ico ^
  --add-data "icon.ico;." --collect-data customtkinter ^
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 ^
  chest_counter_app.py || goto :fail

rem The .exe keeps its data in %LOCALAPPDATA%\ChestCounter. Copy existing data
rem from this folder there once, so your count carries over.
set "DATA=%LOCALAPPDATA%\ChestCounter"
if not exist "%DATA%\chest_count.json" if exist "chest_count.json" (
  echo Copying your existing counts, settings and templates to %DATA% ...
  if not exist "%DATA%" mkdir "%DATA%"
  for %%F in (chest_count.json chest_count.json.bak config.json) do if exist "%%F" copy /Y "%%F" "%DATA%\" >nul
  if exist "templates" xcopy /E /I /Y "templates" "%DATA%\templates" >nul
)

echo.
echo Done:  dist\ChestCounter\ChestCounter.exe
echo Your data is kept in %DATA%
pause
exit /b 0

:fail
echo.
echo Build failed. See the messages above.
pause
exit /b 1
