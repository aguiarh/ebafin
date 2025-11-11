#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import io
import math
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import xml.etree.ElementTree as ET

# =========================
# CONFIG FIXA (produção)
# =========================
# coloque aqui o endpoint REAL sem "..."
ENDPOINT_SOAP = (
    "https://web130.seniorcloud.com.br:30401/"
    "g5-senior-services/sapiens_Synccom_senior_g5_co_mfi_prj_gerarorcamentofinanceirogrid"
)
ENCRYPTION = "0"                 # manter 0
TIP_OPE = "0"                    # 0 = gera/acrescenta
LCT_SUP = "1"                    # 1 = lança nos superiores
RECALCULA_TOTALIZADORES = "S"    # "S" ou "N"
TIMEOUT = 60                     # segundos
BATCH_SIZE = 50                  # quantas linhas por chamada

REQUIRED_COLUMNS = ["numPrj", "mesAno", "codFpj", "ctaFin", "codCcu", "vlrCpf", "vlrCxf"]


# -------------------------
# Helpers
# -------------------------
def to_int(s):
    if s is None:
        return None
    s = str(s).strip()
    if s == "":
        return None
    return int(float(s))


def load_sheet(uploaded_file, normalize_numbers=True):
    if uploaded_file is None:
        raise ValueError("Nenhum arquivo enviado.")

    name = uploaded_file.name.lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        df = pd.read_excel(uploaded_file)
    elif name.endswith(".csv") or name.endswith(".txt"):
        df = pd.read_csv(uploaded_file, sep=None, engine="python")
    else:
        raise ValueError("Formato não suportado. Envie XLSX ou CSV.")

    # valida colunas
    miss = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if miss:
        raise ValueError(f"Colunas obrigatórias ausentes: {', '.join(miss)}")

    # normaliza números (virgula -> ponto)
    if normalize_numbers:
        for col in ["vlrCpf", "vlrCxf"]:
            if col in df.columns:
                df[col] = (
                    df[col]
                    .astype(str)
                    .str.replace(".", "", regex=False)
                    .str.replace(",", ".", regex=False)
                )
                df[col] = df[col].astype(float)

    return df


# -------------------------
# XML builders
# -------------------------
def build_item(row):
    item = ET.Element("orcamentoFinanceiroLista")

    fields = [
        ("numPrj", row.get("numPrj")),
        ("mesAno", row.get("mesAno")),
        ("codFpj", row.get("codFpj")),
        ("ctaFin", row.get("ctaFin")),
        ("codCcu", row.get("codCcu")),
        ("vlrCpf", row.get("vlrCpf")),
        ("vlrCxf", row.get("vlrCxf")),
    ]

    for tag, val in fields:
        el = ET.SubElement(item, tag)
        el.text = "" if val is None else str(val)

    return item


def build_envelope(cfg, rows):
    ns_soap = "http://schemas.xmlsoap.org/soap/envelope/"
    ns_ser = "http://services.senior.com.br"

    ET.register_namespace("soapenv", ns_soap)
    ET.register_namespace("ser", ns_ser)

    env = ET.Element(f"{{{ns_soap}}}Envelope")
    body = ET.SubElement(env, f"{{{ns_soap}}}Body")

    req = ET.SubElement(
        body,
        f"{{{ns_ser}}}gerarorcamentofinanceirogrid"
    )

    # header
    auth = ET.SubElement(req, "user")
    auth.text = cfg["user"]

    pwd = ET.SubElement(req, "password")
    pwd.text = cfg["password"]

    enc = ET.SubElement(req, "encryption")
    enc.text = cfg["encryption"]

    tip = ET.SubElement(req, "tipOpe")
    tip.text = cfg["tipOpe"]

    codemp = ET.SubElement(req, "codEmp")
    codemp.text = cfg["codEmp"]

    lctsup = ET.SubElement(req, "lctSup")
    lctsup.text = cfg["lctSup"]

    rec = ET.SubElement(req, "recalculaTotalizadores")
    rec.text = cfg["recalculaTotalizadores"]

    # lista
    lista = ET.SubElement(req, "orcamentoFinanceiroLista")
    for r in rows:
        lista.append(build_item(r))

    return ET.tostring(env, encoding="utf-8", xml_declaration=True)


def post_batch(endpoint, payload, timeout=60):
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
    }
    resp = requests.post(endpoint, data=payload, headers=headers, timeout=timeout, verify=True)
    resp.raise_for_status()
    return resp


# -------------------------
# Parse
# -------------------------
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


# -------------------------
# Execução em lotes
# -------------------------
def run_import(df, cfg, batch_size):
    endpoint = cfg["endpoint_soap"].strip()
    total = len(df)
    log_rows = [["timestamp", "lote", "status", "resultado", "erro_execucao", "msg", "grid_erros"]]
    ok_batches = 0

    progress = st.progress(0)
    status_box = st.empty()

    for i in range(0, total, batch_size):
        lote_idx = i // batch_size + 1
        chunk = df.iloc[i : i + batch_size].to_dict("records")

        try:
            payload = build_envelope(cfg, chunk)
            resp = post_batch(endpoint, payload, timeout=int(cfg["timeout"]))
            info = parse_response(resp.content)

            status = "OK"
            if (info.get("resultado") or "").upper() != "OK" or info.get("erro_execucao"):
                status = "ERRO"

            if status == "OK":
                ok_batches += 1

            log_rows.append(
                [
                    datetime.now().isoformat(timespec="seconds"),
                    lote_idx,
                    status,
                    info.get("resultado"),
                    info.get("erro_execucao"),
                    info.get("mensagem"),
                    " | ".join(info.get("grid_erros") or []),
                ]
            )
            status_box.info(f"Lote {lote_idx} enviado.")
        except Exception as e:
            # aqui a gente já mostra que o Cloud não conseguiu bater no endpoint
            log_rows.append(
                [
                    datetime.now().isoformat(timespec="seconds"),
                    lote_idx,
                    "EXCEPTION",
                    "",
                    "",
                    str(e),
                    "",
                ]
            )
            status_box.error(f"Erro no lote {lote_idx}: {e}")

        progress.progress(min(i + batch_size, total) / total)

    return ok_batches, log_rows


# =========================
# UI
# =========================
st.title("Importador de Orçamento - EBA - Senior ERP")
st.caption("Produção: informe apenas usuário, senha, empresa e a planilha.")

colA, colB = st.columns([2, 1])

with colA:
    up = st.file_uploader("Planilha (XLSX/CSV)", type=["xlsx", "xls", "csv", "txt"])
    if st.button("Baixar modelo de planilha"):
        sample = pd.DataFrame(
            [
                {
                    "numPrj": 101,
                    "mesAno": "07/2025",
                    "codFpj": 1,
                    "ctaFin": 1002,
                    "codCcu": "1002",
                    "vlrCpf": 15000.00,
                    "vlrCxf": 0.00,
                },
                {
                    "numPrj": 101,
                    "mesAno": "08/2025",
                    "codFpj": 1,
                    "ctaFin": 1002,
                    "codCcu": "1002",
                    "vlrCpf": 20000.00,
                    "vlrCxf": 0.00,
                },
            ]
        )
        bio = io.BytesIO()
        with pd.ExcelWriter(bio, engine="openpyxl") as w:
            sample.to_excel(w, index=False)
        st.download_button(
            "Download sample_orcamento.xlsx",
            data=bio.getvalue(),
            file_name="sample_orcamento.xlsx",
        )

with colB:
    st.subheader("Acesso")
    user = st.text_input("Usuário do WebService", "webservice")
    password = st.text_input("Senha", "Agro@2024", type="password")
    codEmp = st.text_input("Código da Empresa", "70")

normalize_numbers = st.checkbox("Normalizar números (trocar , por .)", value=True)

if st.button("Validar planilha"):
    if not up:
        st.warning("Envie a planilha primeiro.")
    else:
        try:
            df = load_sheet(up, normalize_numbers=normalize_numbers)
            st.success(f"Planilha válida! Qtd de Registros: {len(df)}")
            st.dataframe(df.head(10))
        except Exception as e:
            st.error(f"Erro ao carregar/validar planilha: {e}")

if st.button("Executar importação"):
    if not up:
        st.warning("Envie a planilha primeiro.")
    else:
        try:
            df = load_sheet(up, normalize_numbers=normalize_numbers)
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

        ok, log_rows = run_import(df, cfg, batch_size=BATCH_SIZE)

        # gera CSV do log
        csv_buf = io.StringIO()
        for row in log_rows:
            csv_buf.write(";".join([str(x) if x is not None else "" for x in row]) + "\n")

        st.download_button(
            "Baixar envio_log.csv",
            data=csv_buf.getvalue().encode("utf-8"),
            file_name="envio_log.csv",
        )

        st.success(f"Concluído. Lotes OK: {ok}/{math.ceil(len(df) / BATCH_SIZE)}")
        st.info(
            "Se aparecer erro de conexão aqui no Cloud, testa o mesmo XML dentro da sua rede Senior."
        )
