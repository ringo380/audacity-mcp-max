@echo off
setlocal enabledelayedexpansion

echo.
echo  ============================================
echo   audacity-mcp-max - One-Click Installer
echo   AI-powered audio editing in Audacity
echo  ============================================
echo.

:: ── Check Python ──────────────────────────────────────────
echo [1/5] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  Python is not installed or not in PATH.
    echo.
    set /p INSTALL_PY="  Would you like to install Python via winget? (y/n): "
    if /i "!INSTALL_PY!"=="y" (
        echo.
        echo  Installing Python 3.12 via winget...
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        if !errorlevel! neq 0 (
            echo.
            echo  ERROR: winget install failed.
            echo  Download manually from: https://www.python.org/downloads/
            echo.
            pause
            exit /b 1
        )
        echo.
        echo  Python installed! You need to CLOSE and REOPEN this terminal,
        echo  then run install.bat again so Python is in your PATH.
        echo.
        pause
        exit /b 0
    ) else (
        echo.
        echo  audacity-mcp-max requires Python 3.10+ to run.
        echo  Install it and come back — we'll be here!
        echo.
        echo  Download from: https://www.python.org/downloads/
        echo  IMPORTANT: Check "Add Python to PATH" during installation!
        echo.
        pause
        exit /b 1
    )
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   Found Python %PYVER%

:: Verify Python >= 3.10
for /f %%m in ('python -c "import sys; print(sys.version_info.minor)"') do set PY_MINOR=%%m
for /f %%M in ('python -c "import sys; print(sys.version_info.major)"') do set PY_MAJOR=%%M
if !PY_MAJOR! lss 3 (
    echo.
    echo  ERROR: Python 3.10+ is required, but you have Python %PYVER%
    echo  Download from: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
if !PY_MAJOR! equ 3 if !PY_MINOR! lss 10 (
    echo.
    echo  ERROR: Python 3.10+ is required, but you have Python %PYVER%
    echo  Download from: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: ── Install audacity-mcp-max from GitHub ───────────────────────
:: Warn if running inside a virtual environment
if defined VIRTUAL_ENV (
    echo.
    echo  WARNING: You are inside a virtual environment.
    echo  audacity-mcp-max should be installed globally so Claude Desktop can find it.
    echo  Deactivate your venv first, or run: pip install git+https://github.com/ringo380/audacity-mcp-max.git outside of it.
    echo.
    pause
    exit /b 1
)

:: Check if pip is available
echo.
echo [2/5] Installing audacity-mcp-max from GitHub...
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   pip not found, installing pip...
    python -m ensurepip --upgrade >nul 2>&1
    if !errorlevel! neq 0 (
        echo.
        echo  ERROR: pip is not installed and ensurepip failed.
        echo  Try reinstalling Python with pip enabled.
        echo.
        pause
        exit /b 1
    )
)
python -m pip install --upgrade pip >nul 2>&1
python -m pip install git+https://github.com/ringo380/audacity-mcp-max.git
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: pip install failed. Try running as administrator,
    echo  or run manually: python -m pip install git+https://github.com/ringo380/audacity-mcp-max.git
    echo.
    pause
    exit /b 1
)
echo   audacity-mcp-max installed successfully!

:: ── Enable mod-script-pipe in Audacity ──────────────────────
echo.
echo [3/5] Enabling mod-script-pipe in Audacity...

set "AUD_CFG=%APPDATA%\audacity\audacity.cfg"

if not exist "%AUD_CFG%" (
    echo   Audacity config not found at: %AUD_CFG%
    echo   You may need to open Audacity once first to generate the config,
    echo   then run this installer again.
    echo.
    echo   Or enable it manually: Edit ^> Preferences ^> Modules ^> mod-script-pipe ^> Enabled
    goto :skip_audacity
)

:: Check if already enabled
findstr /c:"mod-script-pipe=1" "%AUD_CFG%" >nul 2>&1
if !errorlevel! equ 0 (
    echo   mod-script-pipe is already enabled - skipping.
    goto :skip_audacity
)

:: Audacity rewrites audacity.cfg when it quits, so editing it while the app is
:: open reports success on a setting that is reverted at the next quit. install.sh
:: refuses in the same two cases, and for the same reason: guessing "not running"
:: when the process table cannot be read silently restores the bug this guard
:: exists to prevent, and the manual path below always works.
set "AUD_TASKS=%TEMP%\audacity-mcp-max-tasklist.txt"
set "AUD_RUNNING="
tasklist /FI "IMAGENAME eq audacity.exe" /NH >"%AUD_TASKS%" 2>nul
set "TASKLIST_RC=!errorlevel!"
findstr /i /c:"audacity.exe" "%AUD_TASKS%" >nul 2>&1
if !errorlevel! equ 0 set "AUD_RUNNING=1"
del "%AUD_TASKS%" >nul 2>&1
if defined AUD_RUNNING (
    echo.
    echo   Audacity is running. It rewrites its config when it quits, so a change
    echo   made now would be undone the next time you close it.
    echo   Fully quit Audacity, then run this installer again - or set it by hand:
    echo   Edit ^> Preferences ^> Modules ^> mod-script-pipe ^> Enabled
    goto :skip_audacity
)
:: A non-zero code here means tasklist is missing, or the machine would not
:: report its process table at all: a filter that matches nothing still exits 0
:: and prints the "INFO: No tasks are running" banner. (Kept outside the block
:: below - cmd mis-parses a :: comment inside parentheses.)
if !TASKLIST_RC! neq 0 (
    echo.
    echo   Cannot tell whether Audacity is running ^(tasklist failed^), and editing
    echo   its config while it is open would be undone at the next quit.
    echo   Quit Audacity if it is open, then set it by hand:
    echo   Edit ^> Preferences ^> Modules ^> mod-script-pipe ^> Enabled
    goto :skip_audacity
)

:: Ask permission before modifying Audacity config
echo.
echo   audacity-mcp-max needs mod-script-pipe enabled to control Audacity.
set /p ENABLE_PIPE="  Would you like to modify the Audacity config to allow MCP access? (y/n): "
if /i not "!ENABLE_PIPE!"=="y" (
    echo.
    echo   Skipped. You can enable it manually:
    echo   Edit ^> Preferences ^> Modules ^> mod-script-pipe ^> Enabled
    goto :skip_audacity
)

:: Back up Audacity config
copy "%AUD_CFG%" "%AUD_CFG%.bak" >nul 2>&1

:: Check if the setting exists but is disabled (0 or 2)
findstr /c:"mod-script-pipe=" "%AUD_CFG%" >nul 2>&1
if !errorlevel! equ 0 (
    :: Replace existing setting with enabled
    python -c "p='%AUD_CFG%'.replace('\\','\\\\'); f=open(p,'r'); t=f.read(); f.close(); t=t.replace('mod-script-pipe=0','mod-script-pipe=1').replace('mod-script-pipe=2','mod-script-pipe=1'); f=open(p,'w'); f.write(t); f.close(); print('  mod-script-pipe enabled!')"
) else (
    :: Setting doesn't exist - need to add it in the right section
    python -c "import re; p='%AUD_CFG%'.replace('\\','\\\\'); f=open(p,'r'); t=f.read(); f.close(); t=re.sub(r'(\[ModulePath\])', r'mod-script-pipe=1\n\1', t, count=1) if '[ModulePath]' in t else t+'\nmod-script-pipe=1\n'; f=open(p,'w'); f.write(t); f.close(); print('  mod-script-pipe enabled!')"
)

echo   NOTE: Restart Audacity for this to take effect.

:skip_audacity

:: ── Configure Claude Desktop ──────────────────────────────
echo.
echo [4/5] Configuring Claude Desktop...

set "CONFIG_DIR=%APPDATA%\Claude"
set "CONFIG_FILE=%CONFIG_DIR%\claude_desktop_config.json"

:: Create directory if it doesn't exist
if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"

:: Check if config already exists
if exist "%CONFIG_FILE%" (
    :: Check if audacity is already configured
    findstr /c:"\"audacity\"" "%CONFIG_FILE%" >nul 2>&1
    if !errorlevel! equ 0 (
        echo   Claude Desktop config already has audacity entry - skipping.
        goto :skip_config
    )
    :: Back up existing config
    copy "%CONFIG_FILE%" "%CONFIG_FILE%.bak" >nul 2>&1
    echo   Backed up existing config to: %CONFIG_FILE%.bak
    echo.
    echo   Found existing Claude Desktop config at:
    echo   %CONFIG_FILE%
    echo.
    echo   You need to MANUALLY add this inside your "mcpServers" block:
    echo.
    echo     "audacity": {
    echo       "command": "audacity-mcp-max"
    echo     }
    echo.
    echo   Opening the config file for you...
    notepad "%CONFIG_FILE%"
    goto :skip_config
)

:: No config exists - create fresh one
(
echo {
echo   "mcpServers": {
echo     "audacity": {
echo       "command": "audacity-mcp-max"
echo     }
echo   }
echo }
) > "%CONFIG_FILE%"
echo   Created Claude Desktop config at:
echo   %CONFIG_FILE%

:skip_config

:: ── Done ──────────────────────────────────────────────────
echo.
echo [5/5] Done!
echo.
echo  ============================================
echo   SETUP COMPLETE!
echo  ============================================
echo.
echo  Next steps:
echo.
echo   1. Restart Audacity (if it's open)
echo   2. Restart Claude Desktop (if it's open)
echo   3. Ask Claude: "Get info about the current Audacity project"
echo.
echo  If you see project info, you're all set!
echo.
echo  Docs: https://github.com/ringo380/audacity-mcp-max
echo  ============================================
echo.
pause
