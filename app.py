#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ===== ORDEM IMPORTANTE =====
import sys, platform, traceback
import streamlit as st
st.set_page_config(page_title="Importador de Orçamento – EBAFIN", layout="wide")

# Cabeçalho mínimo
st.title("Importador de Orçamento – EBAFIN (Senior ERP)")
st.caption(f"Python: {sys.version} | Plataforma: {platform.platform()}")

# --- Garantir dependências principais ---
try:
    import pandas as pd
    import numpy as np
    try:
        import openpyxl  # garante import
    except Exception:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl==3.1.5"])
        import openpyxl
except Exception:
    st.error("Falha ao importar dependências. Traceback abaixo:")
    st.code(traceback.format_exc())
    st.stop()

# ===== Imports restantes (ok após page_config) =====
import io, os, math, zipfile
from datetime import datetime
import xml.etree.ElementTree as ET
import requests

# ---------------- Garantia de ambiente extra (Cloud) ----------------
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
HAS_PANDAS = True  # já importado acima; se falhar, paramos no try/except

# =========================
# CONFIG FIXA (produção)
# =========================
ENDPOINT_SOAP = (
    "https://https://web36.seniorcloud.com.br:40301/"
    "g5-senior-services/sapiens_Synccom_senior_g5_co_mfi_prj_gerarorcamentofinanceirogrid"
)
ENCRYPTION = "0"
TIP_OPE = "0"
LCT_SUP = "1"
RECALCULA_TOTALIZADORES = "S"
TIMEOUT = 60
BATCH_SIZE = 50

REQUIRED_COLUMNS = ["numPrj", "mesAno", "codFpj", "ctaFin", "codCcu", "vlrCpf", "vlrCxf"]

# =========================
# Painel de Diagnóstico
# =========================
with st.expander("🔎 Painel de Diagnóstico", expanded=False):
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
    return (
        series.astype(str)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .astype(float)
    )

def read_table(uploaded_file):
    """Lê XLSX (se openpyxl disponível) ou CSV/TXT (auto-sep).
       Retorna DataFrame ou lista de dicts (fallback)."""
    if uploaded_file is None:
        raise ValueError("Nenhum arquivo enviado.")

    name = uploaded_file.name.lower()

    if HAS_PANDAS:
        if name.endswith((".xlsx", ".xls")):
            if not HAS_OPENPYXL:
                raise ValueError("Arquivo Excel enviado, mas openpyxl não está disponível. Envie CSV ou ajuste requirements.")
            df = pd.read_excel(uploaded_file)
        elif name.endswith((".csv", ".txt")):
            df = pd.read_csv(uploaded_file, sep=None, engine="python")
        else:
            raise ValueError("Formato não suportado. Envie XLSX ou CSV.")

        # valida colunas
        miss = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if miss:
            raise ValueError(f"Colunas obrigatórias ausentes: {', '.join(miss)}")

        for col in ("vlrCpf", "vlrCxf"):
            if col in df.columns:
                try: df[col] = normalize_number_series(df[col])
                except: pass
        return df

    # Fallback sem pandas – CSV/TXT simples
    if name.endswith((".csv", ".txt")):
        text = uploaded_file.read().decode("utf-8", errors="ignore")
        first = next((l for l in text.splitlines() if l.strip()), "")
        sep = ";" if ";" in first else ","
        lines = [l for l in text.splitlines() if l.strip()]
        header = [h.strip() for h in lines[0].split(sep)]
        rows = []
        for line in lines[1:]:
            parts = [p.strip() for p in line.split(sep)]
            rows.append(dict(zip(header, parts)))
        miss = [c for c in REQUIRED_COLUMNS if c not in header]
        if miss:
            raise ValueError(f"Colunas obrigatórias ausentes: {', '.join(miss)}")
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
    return ("" if pd.isna(x.get(k)) else str(x.get(k))) if HAS_PANDAS else str(x.get(k, ""))

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

    for tag, val in (
        ("user", cfg["user"]),
        ("password", cfg["password"]),
        ("encryption", cfg["encryption"]),
        ("tipOpe", cfg["tipOpe"]),
        ("codEmp", cfg["codEmp"]),
        ("lctSup", cfg["lctSup"]),
        ("recalculaTotalizadores", cfg["recalculaTotalizadores"]),
    ):
        ET.SubElement(req, tag).text = str(val)

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
    def all_local(tag): return [e for e in root.iter() if e.tag.endswith(tag)]
    return {
        "resultado":      next((e.text for e in all_local("resultado")), None),
        "erro_execucao":  next((e.text for e in all_local("erroExecucao")), None),
        "grid_erros":     [e.text for e in all_local("msgErr") if e.text],
        "mensagem":       next((e.text for e in all_local("mensagem")), None) or
                          next((e.text for e in all_local("faultstring")), None),
    }

def df_to_records(df):
    return df.to_dict("records") if HAS_PANDAS else df

def run_import(df_like, cfg, batch_size, simulate=False):
    endpoint = cfg["endpoint_soap"].strip()
    records = df_to_records(df_like)
    total = len(records)

    log_rows = [["timestamp", "lote", "status", "resultado", "erro_execucao", "msg", "grid_erros"]]
    ok_batches = 0
    progress = st.progress(0)
    status_box = st.empty()
    xml_outputs = []

    for i in range(0, total, batch_size):
        lote_idx = i // batch_size + 1
        chunk = records[i : i + batch_size]
        try:
            payload = build_envelope(cfg, chunk)
            if simulate:
                xml_outputs.append((lote_idx, payload))
                status = "OK"
                info = {"resultado":"OK","erro_execucao":None,"mensagem":"SIMULADO","grid_erros":[]}
            else:
                resp = post_batch(endpoint, payload, timeout=int(cfg["timeout"]))
                info = parse_response(resp.content)
                status = "OK" if (info.get("resultado") or "").upper() == "OK" and not info.get("erro_execucao") else "ERRO"

            if status == "OK":
                ok_batches += 1

            log_rows.append([
                datetime.now().isoformat(timespec="seconds"),
                lote_idx, status, info.get("resultado"),
                info.get("erro_execucao"), info.get("mensagem"),
                " | ".join(info.get("grid_erros") or []),
            ])
            status_box.info(f"Lote {lote_idx} {'simulado' if simulate else 'enviado'}.")
        except Exception as e:
            log_rows.append([datetime.now().isoformat(timespec="seconds"), lote_idx, "EXCEPTION", "", "", str(e), ""])
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
            {"numPrj":101,"mesAno":"07/2025","codFpj":1,"ctaFin":1002,"codCcu":"1002","vlrCpf":15000.00,"vlrCxf":0.00},
            {"numPrj":101,"mesAno":"08/2025","codFpj":1,"ctaFin":1002,"codCcu":"1002","vlrCpf":20000.00,"vlrCxf":0.00},
        ]
        if HAS_PANDAS and HAS_OPENPYXL:
            df_sample = pd.DataFrame(sample_rows)
            bio = io.BytesIO()
            with pd.ExcelWriter(bio, engine="openpyxl") as w:
                df_sample.to_excel(w, index=False)
            st.download_button("Download sample_orcamento.xlsx", data=bio.getvalue(), file_name="sample_orcamento.xlsx")
        else:
            if HAS_PANDAS:
                df_sample = pd.DataFrame(sample_rows)
                csv_bytes = df_sample.to_csv(index=False, sep=";").encode("utf-8")
            else:
                header = ";".join(REQUIRED_COLUMNS)
                lines = [header] + [";".join(str(r[c]) for c in REQUIRED_COLUMNS) for r in sample_rows]
                csv_bytes = ("\n".join(lines)).encode("utf-8")
            st.warning("openpyxl indisponível: gerando CSV como alternativa.")
            st.download_button("Download sample_orcamento.csv", data=csv_bytes, file_name="sample_orcamento.csv")

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

# --- Botão 1: Validar planilha (prévia) ---
if st.button("Validar planilha"):
    if not up:
        st.warning("Envie a planilha primeiro.")
    else:
        try:
            df_like = read_table(up)

            # --- Normaliza para DataFrame "puro" (evita Styler/_repr_html_) ---
            from pandas.io.formats.style import Styler
            if isinstance(df_like, Styler):
                df_preview = df_like.data
            else:
                df_preview = df_like

            # Garante DataFrame mesmo que venha lista de dicts (fallback)
            if not isinstance(df_preview, pd.DataFrame):
                df_preview = pd.DataFrame(df_preview)

            st.success(f"Planilha válida! Registros: {len(df_preview)}")
            st.dataframe(df_preview.head(10), use_container_width=True)

        except Exception as e:
            st.error(f"Erro ao carregar/validar planilha: {e}")

st.divider()

# --- Botão 2: Executar importação (WS ou simulado) ---
if st.button("Executar importação"):
    if not up:
        st.warning("Envie a planilha primeiro.")
    else:
        try:
            df_like = read_table(up)  # lê de novo para garantir o objeto em memória
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

        ok, log_rows, xml_outputs = run_import(
            df_like, cfg, batch_size=BATCH_SIZE, simulate=simulate
        )

        # Log CSV
        csv_buf = io.StringIO()
        for row in log_rows:
            csv_buf.write(";".join([str(x) if x is not None else "" for x in row]) + "\n")

        st.download_button(
            "Baixar envio_log.csv",
            data=csv_buf.getvalue().encode("utf-8"),
            file_name="envio_log.csv",
        )

        st.success(
            f"Concluído. Lotes {'simulados' if simulate else 'OK'}: "
            f"{ok}/{math.ceil(len(df_to_records(df_like)) / BATCH_SIZE)}"
        )

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
                "Se der erro de conexão no Cloud, teste o mesmo XML de dentro da rede Senior (porta 30401)."
            )
