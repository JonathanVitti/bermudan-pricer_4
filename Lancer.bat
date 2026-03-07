@echo off
chcp 65001 >nul 2>&1
title Portail Trésorerie Desjardins
cd /d "%~dp0"

:: Trouver Python
if exist "venv\Scripts\python.exe" (
    set PYTHON=venv\Scripts\python.exe
) else (
    :: Pas de venv — lancer l'installeur
    echo  L'application n'est pas encore installée.
    echo  Lancement de l'installeur...
    echo.
    call "%~dp0INSTALLER.bat"
    exit /b
)

:: Vérifier les dépendances
%PYTHON% -c "import flask" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  Dépendances manquantes. Lancement de l'installeur...
    call "%~dp0INSTALLER.bat"
    exit /b
)

:: Lancer
echo.
echo  ════════════════════════════════════════════════════════
echo   Portail Trésorerie Desjardins
echo   http://localhost:5050/cpg
echo   Ctrl+C pour arrêter
echo  ════════════════════════════════════════════════════════
echo.
%PYTHON% app.py
pause
