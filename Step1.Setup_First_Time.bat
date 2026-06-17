@echo off

echo ======================================
echo Creating virtual environment...
echo ======================================

py -m venv .venv

echo ======================================
echo Activating virtual environment 1...
echo ======================================

call .venv\Scripts\activate.bat

echo ======================================
echo Bypass ExecutionPolicy...
echo ======================================

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

echo ======================================
echo Activating virtual environment 2...
echo ======================================

call .venv\Scripts\Activate.ps1

echo ======================================
echo Paste Zscaler into powershell:...
echo ======================================

setx AWS_CA_BUNDLE " C:\Users\fzhao\.aws\Zscaler-AWS.pem"

echo ======================================
echo Installing packages...
echo ======================================

pip install -r requirements.txt

echo ======================================
echo Setup complete.
echo ======================================

pause