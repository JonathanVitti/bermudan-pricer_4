@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ================================================================
rem  Desjardins - Portail Tresorerie
rem  INSTALLER.bat - Installation complete en un clic (ASCII-safe)
rem ================================================================
title Desjardins - Installation du Portail Tresorerie
cd /d "%~dp0"

rem ---- Mise en page console (UTF-8) si dispo, sinon ignorer ----
chcp 65001 >nul 2>&1

echo(
echo  ==============================================================
echo   Desjardins - Portail Tresorerie - Installation automatique
echo  ==============================================================
echo(

rem ------------------------------------------------
rem  ETAPE 1 : Trouver Python
rem ------------------------------------------------
set "PYTHON="
set "PYTHON_VERSION="

rem 1a. venv local deja cree ?
if exist "venv\Scripts\python.exe" (
    set "PYTHON=venv\Scripts\python.exe"
    echo  [OK] Python trouve : venv local
    goto HAVE_PYTHON
)

rem 1b. py launcher Windows
where py >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=1,2*" %%a in ('py -3 --version 2^>nul') do set "PYTHON_VERSION=%%a %%b"
    if defined PYTHON_VERSION (
        set "PYTHON=py -3"
        echo  [OK] Python trouve : py launcher (!PYTHON_VERSION!)
        goto HAVE_PYTHON_SYS
    )
)

rem 1c. python dans le PATH
where python >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=1,2*" %%a in ('python --version 2^>nul') do set "PYTHON_VERSION=%%a %%b"
    echo(!PYTHON_VERSION!| findstr /C:"Python 3" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON=python"
        echo  [OK] Python trouve : PATH systeme (!PYTHON_VERSION!)
        goto HAVE_PYTHON_SYS
    )
)

rem 1d. python3 dans le PATH
where python3 >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python3"
    echo  [OK] Python trouve : python3
    goto HAVE_PYTHON_SYS
)

rem 1e. Emplacements courants Windows
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
    "%PROGRAMFILES%\Python313\python.exe"
    "%PROGRAMFILES%\Python312\python.exe"
    "%PROGRAMFILES%\Python311\python.exe"
) do (
    if exist "%%~P" (
        set "PYTHON=%%~P"
        echo  [OK] Python trouve : %%~P
        goto HAVE_PYTHON_SYS
    )
)

rem 1f. Proposer installation
echo(
echo  ==============================================================
echo   ATTENTION : Python n'est pas installe sur cet ordinateur
echo  ==============================================================
echo(
echo   L'application necessite Python 3.10 ou plus recent.
echo(
echo   Options :
echo     [1] Installer Python automatiquement (recommande)
echo         Telecharge depuis python.org et installe pour l'utilisateur courant
echo     [2] Quitter - je vais l'installer moi-meme
echo(
set /p CHOICE="  Votre choix [1/2] : "
if /i "%CHOICE%"=="2" goto EXIT_NO_PYTHON
if /i not "%CHOICE%"=="1" goto EXIT_NO_PYTHON

echo(
echo  [..] Telechargement de Python 3.12...
echo(

set "PY_URL=https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
set "PY_INSTALLER=%TEMP%\python-installer.exe"

rem Telechargement via PowerShell si dispo, sinon bitsadmin
where powershell >nul 2>&1
if not errorlevel 1 (
    powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
        "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_INSTALLER%'" 2>nul
) else (
    rem Fallback (obsolete mais utile sur postes lockes)
    bitsadmin /transfer getpy "%PY_URL%" "%PY_INSTALLER%" >nul 2>&1
)

if not exist "%PY_INSTALLER%" (
    echo  [X] Echec du telechargement. Installer manuellement depuis :
    echo      https://www.python.org/downloads/
    echo(
    echo  IMPORTANT : Cocher "Add Python to PATH" pendant l'installation.
    echo(
    pause
    exit /b 1
)

echo  [..] Installation de Python (utilisateur courant)...
"%PY_INSTALLER%" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
if errorlevel 1 (
    echo  [X] Erreur d'installation Python. Essayez de lancer :
    echo      "%PY_INSTALLER%"
    pause
    exit /b 1
)

rem Rafraichir PATH pour la session courante
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"

where py >nul 2>&1 && (set "PYTHON=py -3" & echo  [OK] Python installe !) && goto HAVE_PYTHON_SYS
where python >nul 2>&1 && (set "PYTHON=python" & echo  [OK] Python installe !) && goto HAVE_PYTHON_SYS
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    echo  [OK] Python installe !
    goto HAVE_PYTHON_SYS
)

echo  [X] Python installe mais introuvable dans le PATH.
echo      Fermez et rouvrez la fenetre, puis relancez INSTALLER.bat
pause
exit /b 1


:HAVE_PYTHON_SYS
rem ------------------------------------------------
rem  ETAPE 2 : Creer le venv
rem ------------------------------------------------
if exist "venv\Scripts\python.exe" (
    echo  [OK] Environnement virtuel existant
    set "PYTHON=venv\Scripts\python.exe"
    goto HAVE_PYTHON
)

echo  [..] Creation de l'environnement virtuel...
%PYTHON% -m venv venv
if errorlevel 1 (
    echo  [X] Erreur lors de la creation du venv.
    echo      Essayez : %PYTHON% -m venv venv
    pause
    exit /b 1
)
set "PYTHON=venv\Scripts\python.exe"
echo  [OK] Environnement virtuel cree


:HAVE_PYTHON
rem ------------------------------------------------
rem  ETAPE 3 : Installer les dependances
rem ------------------------------------------------
echo  [..] Verification des dependances...

%PYTHON% -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  [..] Installation des dependances (1-2 min)...
    %PYTHON% -m pip install --upgrade pip --quiet
    if exist "wheelhouse" (
        echo  [..] Mode hors-ligne detecte (wheelhouse/)
        %PYTHON% -m pip install --no-index --find-links=wheelhouse -r requirements.txt --quiet
    ) else (
        %PYTHON% -m pip install -r requirements.txt --quiet
    )
    if errorlevel 1 (
        echo  [X] Erreur d'installation des dependances.
        echo      Essayez : %PYTHON% -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo  [OK] Dependances installees
) else (
    echo  [OK] Dependances OK
)

rem ------------------------------------------------
rem  ETAPE 4 : Raccourci Bureau
rem   - PowerShell si dispo; sinon fallback VBS
rem ------------------------------------------------
echo  [..] Creation du raccourci Bureau...

set "SHORTCUT_NAME=Portail Tresorerie Desjardins"
set "APP_DIR=%~dp0"
set "ICON_PATH=%APP_DIR%static\alveole.ico"
set "LAUNCHER=%APP_DIR%Lancer.bat"

where powershell >nul 2>&1
if not errorlevel 1 (
    powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ws=New-Object -ComObject WScript.Shell; "^
      "$sc=$ws.CreateShortcut([System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), '%SHORTCUT_NAME%.lnk')); "^
      "$sc.TargetPath='%LAUNCHER%'; $sc.WorkingDirectory='%APP_DIR%'; $sc.IconLocation='%ICON_PATH%'; "^
      "$sc.Description='Portail Tresorerie Desjardins - Epargne a terme'; $sc.WindowStyle=7; $sc.Save();" 2>nul
    if errorlevel 1 (
        echo  [!] Echec PowerShell pour le raccourci, tentative VBS...
        goto MAKE_SHORTCUT_VBS
    )
    echo  [OK] Raccourci cree sur le Bureau
) else (
    goto MAKE_SHORTCUT_VBS
)
goto LAUNCH

:MAKE_SHORTCUT_VBS
> "%TEMP%\mkshortcut.vbs" (
    echo Set ws = CreateObject("WScript.Shell")
    echo desktop = ws.SpecialFolders("Desktop")
    echo Set sc = ws.CreateShortcut(desktop ^& "\%SHORTCUT_NAME%.lnk")
    echo sc.TargetPath = "%LAUNCHER%"
    echo sc.WorkingDirectory = "%APP_DIR%"
    echo sc.IconLocation = "%ICON_PATH%"
    echo sc.Description = "Portail Tresorerie Desjardins - Epargne a terme"
    echo sc.WindowStyle = 7
    echo sc.Save
)
cscript //nologo "%TEMP%\mkshortcut.vbs" >nul 2>&1
if errorlevel 1 (
    echo  [!] Raccourci non cree (utilisez Lancer.bat)
) else (
    echo  [OK] Raccourci cree sur le Bureau
)

:LAUNCH
rem ------------------------------------------------
rem  ETAPE 5 : Lancer
rem ------------------------------------------------
echo(
echo  ==============================================================
echo   Installation terminee !
echo   Raccourci : "Portail Tresorerie Desjardins" (Bureau)
echo   URL       : http://localhost:5050/cpg
echo  ==============================================================
echo(
echo  Lancement de l'application...
echo(

%PYTHON% app.py
pause
exit /b 0

:EXIT_NO_PYTHON
echo(
echo  Pour installer Python :
echo    1. Aller sur https://www.python.org/downloads/
echo    2. Telecharger Python 3.12 ou plus recent
echo    3. Cocher "Add Python to PATH" pendant l'installation
echo    4. Relancer INSTALLER.bat
echo(
pause
exit /b 1
