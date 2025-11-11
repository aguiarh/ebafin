
# Importador de Orçamento - Streamlit (Windows)

## Requisitos
- Windows
- Python 3.11 (recomendado)
- Pasta **SEM acentos** (ex.: `C:\Projetos\orc_import`)

## Passo a passo
```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate

python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

python -m streamlit run app.py
# abre em http://localhost:8501
```

Se `streamlit` não for reconhecido:
```powershell
python -m streamlit run app.py
```
