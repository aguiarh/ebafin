# EBAFIN – Importador de Orçamento Financeiro (Streamlit Cloud)

Abaixo estão **todos os arquivos** para rodar no Streamlit Cloud: `app.py`, `requirements.txt` e `runtime.txt`. Incluí um **Painel de Diagnóstico** embutido (toggle no topo) pra checar se `openpyxl`/`pandas` estão realmente disponíveis no container e para listar arquivos do diretório.

---

## app.py

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EBAFIN – Importador de Orçamento Financeiro (Senior ERP)
- Layout: Upload à esquerda, Acesso à direita
- Painel de diagnóstico embutido
- Fallback CSV quando openpyxl não está disponível
- "Modo simulado" para gerar/baixar os XMLs em vez de enviar (útil no Streamlit Cloud)
- Auto-instalação de openpyxl em runtime (quando possível)
"""
import io
import os
import sys
import platform
import math
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import streamlit as st
import requests

# ---------------- Garantia de ambiente (apenas p/ Cloud) ----------------
# Tenta instalar openpyxl em runtime caso não esteja presente

def _ensure(pkg, ver=None):
    try:
        __import__(pkg)
        return True
    except Exception:
        target = f"{pkg}=={ver}" if ver else pkg
        try:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", target])
            __import__(pkg)
            return True
        except Exception as e:
            st.warning(f"Não foi possível instalar {target} em runtime: {e}")
            return False

HAS_OPENPYXL = _ensure("openpyxl", "3.1.5")

# pandas costuma já estar, mas garantimos import amigável
try:
    import pandas as pd  # type: ignore
    HAS_PANDAS = True
except Exception as e:
    HAS_PANDAS = False
    st.error("Pandas não disponível no servidor. Envie CSV simples (.csv/.txt) ou ajuste requirements.txt")

# =========================
# CONFIG FIXA (produção)
# =========================
# ATENÇÃO: Use o endpoint REAL, sem "..." no meio da URL
ENDPOINT_SOAP = (
    "https://web130.seniorcloud.com.br:30401/"
    "g5-senior-services/sapiens_Synccom_senior_g5_co_mfi_prj_gerarorcamentofinanceirogrid"
)

ENCRYPTION = "0"                 # manter 0
TIP_OPE = "0"                    # 0 = gera/acrescenta
LCT_SUP = "1"                    # 1 = lança nos superiores
RECALCULA_TOTALIZADORES = "S"    # "S" ou "N"
TIMEOUT = 60                      # segundos
BATCH_SIZE = 50                   # quantas linhas por chamada

REQUIRED_COLUMNS = [
    "numPrj", "mesAno", "codFpj", "ctaFin", "codCcu", "vlrCpf", "vlrCxf"
]

# =========================
# UI – Cabeçalho
# =========================
st.set_page_config(page_title="Importador de Orçamento – EBAFIN", layout="wide")
st.title("Importador de Orçamento – EBAFIN (Senior ERP)")
st.caption("Produção: informe usuário, senha, empresa e a planilha. Upload à esquerda, acesso à direita.")

with st.expander("🔎 Painel de Diagnóstico", expanded=False):
    st.write("Python:", sys.version)
    st.write("Plataforma:", platform.platform())
    st.write("HAS_PANDAS:", HAS_PANDAS, "HAS_OPENPYXL:", HAS_OPENPYXL)
    st.write("Arquivos no diretório:", os.listdir("."))
    try:
        import importlib.metadata as im
        pkgs = {d.metadata["Name"]: d.version for d in im.distributions()}
        st.write("Pacotes instalados (amostra):", dict(list(sorted(pkgs.items()))[:40]))
    except Exception as e:
        st.warning(f"Falha ao listar pacotes: {e}")

# =========================
# Helpers
# =========================

def normalize_number_series(series):
    # Converte strings com separador de milhar/decimal BR para float
    return (
        series.astype(str)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .astype(float)
    )


def read_table(uploaded_file):
    """Lê arquivo enviado.
    Suporta: XLSX (se openpyxl disponível) e CSV/TXT (autodetect sep).
    Retorna DataFrame-like (pandas) ou lista de dicts (fallback sem pandas).
    """
    if uploaded_file is None:
        raise ValueError("Nenhum arquivo enviado.")

    name = uploaded_file.name.lower()

    if HAS_PANDAS:
        if name.endswith((".xlsx", ".xls")):
            if not HAS_OPENPYXL:
                raise ValueError(
                    "Arquivo Excel enviado, mas openpyxl não está disponível. "
                    "Instale openpyxl no requirements.txt ou envie CSV."
                )
            df = pd.read_excel(uploaded_file)
        elif name.endswith((".csv", ".txt")):
            df = pd.read_csv(uploaded_file, sep=None, engine="python")
        else:
            raise ValueError("Formato não suportado. Envie XLSX ou CSV.")

        # valida colunas
        miss = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if miss:
            raise ValueError(f"Colunas obrigatórias ausentes: {', '.join(miss)}")

        # normaliza valores numéricos
        for col in ("vlrCpf", "vlrCxf"):
            if col in df.columns:
                try:
                    df[col] = normalize_number_series(df[col])
                except Exception:
                    pass
        return df

    # Fallback sem pandas – lê como CSV simples (separador ; ou ,)
    if name.endswith((".csv", ".txt")):
        text = uploaded_file.read().decode("utf-8", errors="ignore")
        sep = ";" if ";" in text.splitlines()[0] else ","
        lines = [l for l in text.splitlines() if l.strip()]
        header = [h.strip() for h in lines[0].split(sep)]
        rows = []
        for line in lines[1:]:
            parts = [p.strip() for p in line.split(sep)]
            rows.append(dict(zip(header, parts)))
        # valida colunas
        miss = [c for c in REQUIRED_COLUMNS if c not in header]
        if miss:
            raise ValueError(f"Colunas obrigatórias ausentes: {', '.join(miss)}")
        # normaliza números
        for r in rows:
            for col in ("vlrCpf", "vlrCxf"):
                if col in r and r[col] not in (None, ""):
                    r[col] = float(str(r[col]).replace(".", "").replace(",", "."))
        return rows

    raise ValueError("Formato não suportado sem pandas. Envie CSV.")


# -------------------------
# XML builders
# -------------------------

def _val_from_row(x, k):
    if HAS_PANDAS:
        import pandas as pd  # local
        return ("" if pd.isna(x.get(k)) else str(x.get(k)))
    return str(x.get(k, ""))


def build_item(row):
    item = ET.Element("orcamentoFinanceiroLista")
    for tag in REQUIRED_COLUMNS:
        el = ET.SubElement(item, tag)
        el.text = _val_from_row(row, tag)
    return item


def build_envelope(cfg, rows):
    ns_soap = "http://schemas.xmlsoap.org/soap/envelope/"
    ns_ser = "http://services.senior.com.br"

    ET.register_namespace("soapenv", ns_soap)
    ET.register_namespace("ser", ns_ser)

    env = ET.Element(f"{{{ns_soap}}}Envelope")
    body = ET.SubElement(env, f"{{{ns_soap}}}Body")
    req = ET.SubElement(body, f"{{{ns_ser}}}gerarorcamentofinanceirogrid")

    # header
    for tag, val in (
        ("user", cfg["user"]),
        ("password", cfg["password"]),
        ("encryption", cfg["encryption"]),
        ("tipOpe", cfg["tipOpe"]),
        ("codEmp", cfg["codEmp"]),
        ("lctSup", cfg["lctSup"]),
        ("recalculaTotalizadores", cfg["recalculaTotalizadores"]),
    ):
        el = ET.SubElement(req, tag)
        el.text = str(val)

    # lista
    lista = ET.SubElement(req, "orcamentoFinanceiroLista")
    for r in rows:
        lista.append(build_item(r))

    return ET.tostring(env, encoding="utf-8", xml_declaration=True)


def post_batch(endpoint, payload, timeout=60):
    headers = {"Content-Type": "text/xml; charset=utf-8"}
    resp = requests.post(endpoint, data=payload, headers=headers, timeout=timeout, verify=True)
    resp.raise_for_status()
    return resp


def parse_response(content: bytes):
    root = ET.fromstring(content)

    def all_local(tag):
        return [e for e in root.iter() if e.tag.endswith(tag)]

    resultado = next((e.text for e in all_local("resultado")), None)
    erro_exec = next((e.text for e in all_local("erroExecucao")), None)
    erros = [e.text for e in all_local("msgErr") if e.text]
    mensagem = next((e.text for e in all_local("mensagem")), None)
    faultstring = next((e.text for e in all_local("faultstring")), None)

    return {
        "resultado": resultado,
        "erro_execucao": erro_exec,
        "grid_erros": erros,
        "mensagem": mensagem or faultstring,
    }


def df_to_records(df):
    if HAS_PANDAS:
        return df.to_dict("records")
    return df  # já é lista de dicts no fallback


def run_import(df_like, cfg, batch_size, simulate=False):
    endpoint = cfg["endpoint_soap"].strip()
    records = df_to_records(df_like)
    total = len(records)

    log_rows = [[
        "timestamp", "lote", "status", "resultado", "erro_execucao", "msg", "grid_erros"
    ]]
    ok_batches = 0

    progress = st.progress(0)
    status_box = st.empty()

    # buffer para XMLs (modo simulado)
    xml_outputs = []

    for i in range(0, total, batch_size):
        lote_idx = i // batch_size + 1
        chunk = records[i : i + batch_size]

        try:
            payload = build_envelope(cfg, chunk)

            if simulate:
                # guarda para download e considera OK
                xml_outputs.append((lote_idx, payload))
                status = "OK"
                info = {"resultado": "OK", "erro_execucao": None, "mensagem": "SIMULADO", "grid_erros": []}
            else:
                resp = post_batch(endpoint, payload, timeout=int(cfg["timeout"]))
                info = parse_response(resp.content)
                status = "OK"
                if (info.get("resultado") or "").upper() != "OK" or info.get("erro_execucao"):
                    status = "ERRO"

            if status == "OK":
                ok_batches += 1

            log_rows.append([
                datetime.now().isoformat(timespec="seconds"),
                lote_idx,
                status,
                info.get("resultado"),
                info.get("erro_execucao"),
                info.get("mensagem"),
                " | ".join(info.get("grid_erros") or []),
            ])
            status_box.info(f"Lote {lote_idx} {'simulado' if simulate else 'enviado'}.")
        except Exception as e:
            log_rows.append([
                datetime.now().isoformat(timespec="seconds"),
                lote_idx,
                "EXCEPTION",
                "",
                "",
                str(e),
                "",
            ])
            status_box.error(f"Erro no lote {lote_idx}: {e}")

        progress.progress(min(i + batch_size, total) / total)

    return ok_batches, log_rows, xml_outputs


# =========================
# UI – Colunas
# =========================
colA, colB = st.columns([2, 1])

with colA:
    st.subheader("Upload da Planilha")
    up = st.file_uploader("Planilha (XLSX/CSV)", type=["xlsx", "xls", "csv", "txt"])

    if st.button("Baixar modelo de planilha"):
        sample_rows = [
            {"numPrj": 101, "mesAno": "07/2025", "codFpj": 1, "ctaFin": 1002, "codCcu": "1002", "vlrCpf": 15000.00, "vlrCxf": 0.00},
            {"numPrj": 101, "mesAno": "08/2025", "codFpj": 1, "ctaFin": 1002, "codCcu": "1002", "vlrCpf": 20000.00, "vlrCxf": 0.00},
        ]

        if HAS_PANDAS and HAS_OPENPYXL:
            df_sample = pd.DataFrame(sample_rows)
            bio = io.BytesIO()
            with pd.ExcelWriter(bio, engine="openpyxl") as w:
                df_sample.to_excel(w, index=False)
            st.download_button(
                "Download sample_orcamento.xlsx",
                data=bio.getvalue(),
                file_name="sample_orcamento.xlsx",
            )
        else:
            # fallback CSV
            if HAS_PANDAS:
                df_sample = pd.DataFrame(sample_rows)
                csv_bytes = df_sample.to_csv(index=False, sep=";").encode("utf-8")
            else:
                header = ";".join(REQUIRED_COLUMNS)
                lines = [header]
                for r in sample_rows:
                    line = ";".join(str(r[c]) for c in REQUIRED_COLUMNS)
                    lines.append(line)
                csv_bytes = ("
".join(lines)).encode("utf-8")

            st.warning("openpyxl indisponível: gerando CSV como alternativa.")
            st.download_button(
                "Download sample_orcamento.csv",
                data=csv_bytes,
                file_name="sample_orcamento.csv",
            )

with colB:
    st.subheader("Acesso")
    user = st.text_input("Usuário do WebService", "webservice")
    password = st.text_input("Senha", "Agro@2024", type="password")
    codEmp = st.text_input("Código da Empresa", "70")
    simulate = st.checkbox("Modo simulado (não envia, gera XML)", value=True)
    st.caption("As demais configurações estão fixas no código.")

normalize_numbers = st.checkbox("Normalizar números (trocar , por .)", value=True)

# =========================
# Ações
# =========================
if st.button("Validar planilha"):
    if not up:
        st.warning("Envie a planilha primeiro.")
    else:
        try:
            df_like = read_table(up)
            if HAS_PANDAS:
                st.success(f"Planilha válida! Registros: {len(df_like)}")
                st.dataframe(df_like.head(10))
            else:
                st.success(f"CSV válido! Registros: {len(df_like)}")
                st.json(df_like[:5])
        except Exception as e:
            st.error(f"Erro ao carregar/validar planilha: {e}")

if st.button("Executar importação"):
    if not up:
        st.warning("Envie a planilha primeiro.")
    else:
        try:
            df_like = read_table(up)
        except Exception as e:
            st.error(f"Erro ao carregar/validar planilha: {e}")
            st.stop()

        cfg = {
            "endpoint_soap": ENDPOINT_SOAP,
            "user": user,
            "password": password,
            "encryption": ENCRYPTION,
            "tipOpe": TIP_OPE,
            "codEmp": codEmp,
            "lctSup": LCT_SUP,
            "recalculaTotalizadores": RECALCULA_TOTALIZADORES,
            "timeout": TIMEOUT,
        }

        ok, log_rows, xml_outputs = run_import(df_like, cfg, batch_size=BATCH_SIZE, simulate=simulate)

        # gera CSV do log
        csv_buf = io.StringIO()
        for row in log_rows:
            csv_buf.write(";".join([str(x) if x is not None else "" for x in row]) + "
")

        st.download_button(
            "Baixar envio_log.csv",
            data=csv_buf.getvalue().encode("utf-8"),
            file_name="envio_log.csv",
        )

        st.success(f"Concluído. Lotes {'simulados' if simulate else 'OK'}: {ok}/{math.ceil(len(df_to_records(df_like)) / BATCH_SIZE)}")

        # Se simulou, oferece ZIP com XMLs
        if simulate and xml_outputs:
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for num_lote, xml_bytes in xml_outputs:
                    zf.writestr(f"lote_{num_lote:03d}.xml", xml_bytes)
            st.download_button(
                "Baixar XMLs (ZIP)",
                data=zip_buf.getvalue(),
                file_name="lotes_xml.zip",
                mime="application/zip",
            )

        if not simulate:
            st.info(
                "Se aparecer erro de conexão aqui no Cloud, teste o mesmo XML dentro da sua rede Senior. "
                "Alguns ambientes não aceitam tráfego externo/porta 30401."
            )
```

text
streamlit==1.37.1
pandas==2.2.2
numpy==1.26.4
requests==2.32.3
PyYAML==6.0.2
openpyxl==3.1.5

````

> Incluí `numpy` explicitamente (o `pandas` puxa, mas ajuda o resolver do Cloud) e mantive `openpyxl`.

---

## runtime.txt (opcional)

```text
python-3.12.3
````

Se o Cloud continuar mostrando Python 3.13.9 no log, tudo bem – o `requirements.txt` acima já é compatível.

---

### Checklist rápido

* Commit e push destes **3 arquivos** na raiz do repo.
* *Manage app → Restart* no Streamlit Cloud.
* Ative o **“🔎 Mostrar Painel de Diagnóstico”** na página e confira se `openpyxl` aparece na lista de pacotes.
* Se não aparecer: o Cloud está usando outro `requirements.txt` (pasta errada, nome diferente) – verifique caminho/branch.
