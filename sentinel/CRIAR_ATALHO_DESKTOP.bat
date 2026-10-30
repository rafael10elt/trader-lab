@echo off
set SCRIPT="%TEMP%\%RANDOM%-%RANDOM%.vbs"
echo Set oWS = WScript.CreateObject("WScript.Shell") >> %SCRIPT%
echo sLinkFile = oWS.SpecialFolders("Desktop") ^& "\Lumi Sentinel Vision.lnk" >> %SCRIPT%
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> %SCRIPT%
echo oLink.TargetPath = "%~dp0venv\Scripts\pythonw.exe" >> %SCRIPT%
echo oLink.Arguments = """%~dp0sentinel.py""" >> %SCRIPT%
echo oLink.WorkingDirectory = "%~dp0" >> %SCRIPT%
echo oLink.Description = "Lumi Sentinel Vision - vigilancia por visao computacional" >> %SCRIPT%
echo oLink.Save >> %SCRIPT%
cscript /nologo %SCRIPT%
del %SCRIPT%
echo.
echo ========================================================
echo [OK] Atalho 'Lumi Sentinel Vision' criado na Area de Trabalho!
echo (se o venv ainda nao existe, rode INICIAR_SENTINELA.bat antes)
echo ========================================================
pause
