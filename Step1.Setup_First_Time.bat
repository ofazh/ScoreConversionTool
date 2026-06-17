@echo off

echo ======================================
echo Creating virtual environment...
echo ======================================

py -m venv .venv

echo ======================================
echo Activating virtual environment...
echo ======================================

call .venv\Scripts\activate.bat

echo ======================================
echo Installing packages...
echo ======================================

pip install -r requirements.txt

echo ======================================
echo Setup complete.
echo ======================================

pause