@echo off
setlocal
if not exist .venv (
  py -3.11 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
python -m streamlit run app.py
endlocal
