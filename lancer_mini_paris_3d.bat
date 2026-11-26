@echo off
rem Lance Mini Paris 3D en local : trouve Python, demande la cle PRIM au premier lancement,
rem ouvre la carte dans le navigateur et demarre le serveur temps reel.
rem accents corrects dans la console, et dossier de travail = dossier du fichier
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem --- Trouver Python (python, py, ou Miniconda/Anaconda) ---
set PY=
if exist "%USERPROFILE%\miniconda3\python.exe" set PY="%USERPROFILE%\miniconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set PY="%USERPROFILE%\anaconda3\python.exe"
if not defined PY where py >nul 2>nul && set PY=py
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python introuvable. Installe Python ou lance ce fichier depuis l'invite Anaconda.
  pause & exit /b 1
)

rem --- Cle PRIM : demandee une seule fois, gardee dans .env ---
if not exist ".env" (
  echo Premiere utilisation : colle ta cle API PRIM puis appuie sur Entree.
  set /p CLE=Cle PRIM : 
  call echo PRIM_API_KEY=%%CLE%%> .env
)

rem --- Ouvre la carte dans le navigateur 3 s plus tard, le temps que le serveur demarre ---
start "" /min cmd /c "timeout /t 3 >nul & start http://localhost:8765"
rem --- Demarre le serveur (la fenetre doit rester ouverte) ---
%PY% serveur_temps_reel.py
echo.
echo Le serveur s'est arrete. Lis le message ci-dessus.
pause
