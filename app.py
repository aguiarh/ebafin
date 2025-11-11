import re
from datetime import datetime
from decimal import Decimal
import streamlit as st

st.set_page_config(page_title="NFS-e Espelho (Hotel)", layout="wide")

st.title("📄 Espelho de NFS-e a partir de relatório de hospedagem")
st.caption("Gera um HTML bem parecido com o modelo da prefeitura, mas marcado como espelho.")

BRASAO_URL = "https://www.boaesperanca.mg.gov.br/Arquitetura/Imagens/prefeitura/brasao.png"

# =============== HELPERS ===================
def to_decimal(valor_str: str) -> Decimal:
    if not valor_str:
        return Decimal("0")
    valor_str = valor_str.strip()
    valor_str = valor_str.replace("R$", "").replace(".", "").replace(" ", "")
    valor_str = valor_str.replace(",", ".")
    try:
        return Decimal(valor_str)
    except Exception:
        return Decimal("0")

def extrair_campo(texto: str, rotulo: str, sep=":") -> str:
    padrao = rf"{re.escape(rotulo)}{sep}\s*(.*)"
    m = re.search(padrao, texto)
    return m.group(1).strip() if m else ""

def extrair_primeiro_valor(texto: str, rotulo: str) -> str:
    m = re.search(rf"{rotulo}.*?(R\$ [0-9\.,]+)", texto, re.DOTALL)
    return m.group(1) if m else "R$ 0,00"

def parse_relatorio(texto: str) -> dict:
    hotel = texto.splitlines()[0].strip() if texto.strip() else "HOTEL NÃO INFORMADO"
    cnpj_hotel = extrair_campo(texto, "CNPJ")
    checkin = extrair_campo(texto, "Check-in")
    checkout = extrair_campo(texto, "Check-out")
    categoria = extrair_campo(texto, "Categoria")
    tarifa = extrair_campo(texto, "Tarifa")
    dias = extrair_campo(texto, "Dias hospedagem")
    agenciador = extrair_campo(texto, "Agenciador")

    # hóspede
    hospede = "Hóspede não identificado"
    m_hosp = re.search(r"Hóspede\s+([\wÀ-ÿ\s]+)", texto)
    if m_hosp:
        hospede = m_hosp.group(1).strip()

    valor_diaria_str = extrair_primeiro_valor(texto, "Adulto")
    taxa_str = extrair_primeiro_valor(texto, "Total Taxa")
    total_conta_str = extrair_primeiro_valor(texto, "Total da conta")

    valor_diaria = to_decimal(valor_diaria_str)
    taxa = to_decimal(taxa_str)
    total = to_decimal(total_conta_str)

    return {
        "hotel": hotel,
        "cnpj_hotel": cnpj_hotel,
        "checkin": checkin,
        "checkout": checkout,
        "categoria": categoria,
        "tarifa": tarifa,
        "dias": dias,
        "agenciador": agenciador,
        "hospede": hospede,
        "valor_diaria": valor_diaria,
        "taxa": taxa,
        "total": total,
    }

def montar_nfse_fake(dados: dict, cod_servico: str, aliquota: float) -> dict:
    agora = datetime.now()
    numero_nfse = agora.strftime("%Y%m%d%H%M%S")
    valor_servico = float(dados["total"])
    iss = round(valor_servico * (aliquota / 100), 2)
    codigo_verificacao = "BESP-" + agora.strftime("%H%M%S")

    return {
        "cabecalho": {
            "prefeitura": "PREFEITURA MUNICIPAL DE BOA ESPERANÇA",
            "secretaria": "SECRETARIA MUNICIPAL DE FINANÇAS",
            "titulo": "NOTA FISCAL ELETRÔNICA DE SERVIÇO - NFS-e",
            "brasao": BRASAO_URL,
        },
        "numero_nfse": numero_nfse,
        "data_emissao": agora.strftime("%d/%m/%Y %H:%M:%S"),
        "competencia": agora.strftime("%m/%Y"),
        "codigo_verificacao": codigo_verificacao,
        "prestador": {
            "razao_social": dados["hotel"],
            "nome_fantasia": dados["hotel"],
            "cnpj": dados["cnpj_hotel"],
            "endereco": "Calcedônia, 220, Jardim Alvorada",
            "municipio": "Boa Esperança",
            "uf": "MG",
            "cep": "37170-000",
            "email": "atendimento@jhspalacehotel.com.br",
            "telefone": "(35) 3851-3379"
        },
        "tomador": {
            "razao_social": dados["hospede"],
            "cpf_cnpj": "",
            "endereco": "",
            "municipio": "Boa Esperança",
            "uf": "MG",
            "cep": "",
            "email": "",
            "telefone": ""
        },
        "servico": {
            "descricao": (
                f"HOSPEDAGEM - {dados['categoria']} - {dados['tarifa']} "
                f"({dados['checkin']} a {dados['checkout']}) "
                f"Agenciador: {dados['agenciador']}"
            ),
            "codigo_servico": cod_servico,
            "quantidade": 1,
            "valor_unitario": valor_servico,
            "valor_total": valor_servico,
            "aliquota": aliquota,
            "valor_iss": iss,
        },
        "tributos": {
            "ir": 0.00,
            "pis": 0.00,
            "cofins": 0.00,
            "inss": 0.00,
            "csll": 0.00
        },
        "valor_servico": valor_servico,
        "iss": iss
    }

def gerar_html_nfse(nf: dict) -> str:
    s = nf["servico"]
    trib = nf["tributos"]
    brasao = nf["cabecalho"]["brasao"]

    # deixei o cabeçalho bem diferente e os blocos com faixa cinza
    html = f"""
    <html>
    <head>
        <meta charset="utf-8" />
        <title>NFS-e (Espelho)</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #dfe3e6;
                padding: 12px;
            }}
            .nf-container {{
                background: #fff;
                max-width: 900px;
                margin: 0 auto;
                border: 1px solid #000;
                padding: 10px 12px 16px 12px;
            }}
            .header {{
                display: grid;
                grid-template-columns: 80px 1fr;
                gap: 10px;
                border-bottom: 2px solid #000;
                margin-bottom: 8px;
                align-items: center;
            }}
            .header img {{
                width: 70px;
            }}
            .header-text {{
                text-align: center;
                line-height: 1.15;
            }}
            .header-text .org {{
                font-size: 13px;
            }}
            .header-text .title {{
                font-size: 15px;
                font-weight: bold;
                margin-top: 3px;
            }}
            .alerta {{
                background: #ffe7e7;
                color: #a00000;
                border: 1px solid #c75555;
                padding: 4px 6px;
                font-size: 11px;
                margin-bottom: 6px;
                text-align: center;
                font-weight: bold;
            }}
            .block-title {{
                background: #e6e6e6;
                font-weight: bold;
                padding: 3px 4px;
                font-size: 12px;
                border: 1px solid #cfcfcf;
                margin-top: 6px;
            }}
            .block {{
                border: 1px solid #cfcfcf;
                padding: 4px 6px;
                font-size: 12px;
            }}
            .row-2 {{
                display: flex;
                gap: 6px;
            }}
            .col {{
                flex: 1;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 12px;
                margin-top: 4px;
            }}
            table th, table td {{
                border: 1px solid #ccc;
                padding: 3px;
            }}
            table th {{
                background: #f3f3f3;
            }}
            .footer {{
                margin-top: 10px;
                font-size: 11px;
            }}
        </style>
    </head>
    <body>
        <div class="nf-container">
            <div class="header">
                <img src="{brasao}" alt="Brasão">
                <div class="header-text">
                    <div class="org">{nf['cabecalho']['prefeitura']}</div>
                    <div class="org">{nf['cabecalho']['secretaria']}</div>
                    <div class="title">{nf['cabecalho']['titulo']}</div>
                </div>
            </div>

            <div class="alerta">ESPelho gerado pelo sistema do hotel. NÃO substitui a NFS-e oficial da Prefeitura.</div>

            <div class="block-title">Dados da NFS-e</div>
            <div class="block">
                <p><strong>Número da NFS-e:</strong> {nf['numero_nfse']}</p>
                <p><strong>Data e Hora da Emissão:</strong> {nf['data_emissao']}</p>
                <p><strong>Competência:</strong> {nf['competencia']}</p>
                <p><strong>Código de Verificação:</strong> {nf['codigo_verificacao']}</p>
            </div>

            <div class="row-2">
                <div class="col">
                    <div class="block-title">Dados do Prestador de Serviço</div>
                    <div class="block">
                        <p><strong>Razão Social/Nome:</strong> {nf['prestador']['razao_social']}</p>
                        <p><strong>CPF/CNPJ:</strong> {nf['prestador']['cnpj']}</p>
                        <p><strong>Endereço:</strong> {nf['prestador']['endereco']}</p>
                        <p><strong>Município:</strong> {nf['prestador']['municipio']} - {nf['prestador']['uf']}</p>
                        <p><strong>CEP:</strong> {nf['prestador']['cep']}</p>
                        <p><strong>Email:</strong> {nf['prestador']['email']}</p>
                        <p><strong>Telefone:</strong> {nf['prestador']['telefone']}</p>
                    </div>
                </div>
                <div class="col">
                    <div class="block-title">Dados do Tomador de Serviço</div>
                    <div class="block">
                        <p><strong>Razão Social/Nome:</strong> {nf['tomador']['razao_social']}</p>
                        <p><strong>CPF/CNPJ:</strong> {nf['tomador']['cpf_cnpj']}</p>
                        <p><strong>Município:</strong> {nf['tomador']['municipio']} - {nf['tomador']['uf']}</p>
                        <p><strong>CEP:</strong> {nf['tomador']['cep']}</p>
                        <p><strong>Email:</strong> {nf['tomador']['email']}</p>
                        <p><strong>Telefone:</strong> {nf['tomador']['telefone']}</p>
                    </div>
                </div>
            </div>

            <div class="block-title">Descrição dos Serviços</div>
            <div class="block">
                <table>
                    <thead>
                        <tr>
                            <th>Descrição</th>
                            <th>Cód.</th>
                            <th>Qtd</th>
                            <th>Valor Unitário</th>
                            <th>Valor do Serviço</th>
                            <th>Base de Cálculo</th>
                            <th>ISS (%)</th>
                            <th>ISS (R$)</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>{s['descricao']}</td>
                            <td>{s['codigo_servico']}</td>
                            <td style="text-align:center;">{s['quantidade']}</td>
                            <td style="text-align:right;">{s['valor_unitario']:.2f}</td>
                            <td style="text-align:right;">{s['valor_total']:.2f}</td>
                            <td style="text-align:right;">{s['valor_total']:.2f}</td>
                            <td style="text-align:center;">{s['aliquota']:.2f}</td>
                            <td style="text-align:right;">{s['valor_iss']:.2f}</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <div class="block-title">Tributos Federais</div>
            <div class="block">
                <table>
                    <tr>
                        <th>IR</th><th>PIS/PASEP</th><th>COFINS</th><th>INSS</th><th>CSLL</th><th>Outras retenções</th>
                    </tr>
                    <tr>
                        <td>R$ {trib['ir']:.2f}</td>
                        <td>R$ {trib['pis']:.2f}</td>
                        <td>R$ {trib['cofins']:.2f}</td>
                        <td>R$ {trib['inss']:.2f}</td>
                        <td>R$ {trib['csll']:.2f}</td>
                        <td>R$ 0,00</td>
                    </tr>
                </table>
            </div>

            <div class="block-title">Detalhamento de Valores - Prestador dos Serviços</div>
            <div class="block">
                <table>
                    <tr>
                        <td><strong>Valor dos Serviços R$</strong></td>
                        <td style="text-align:right;">{nf['valor_servico']:.2f}</td>
                    </tr>
                    <tr>
                        <td><strong>(-) ISS Retido / Substituído</strong></td>
                        <td style="text-align:right;">0,00</td>
                    </tr>
                    <tr>
                        <td><strong>(=) Valor Líquido R$</strong></td>
                        <td style="text-align:right;">{nf['valor_servico']:.2f}</td>
                    </tr>
                </table>
            </div>

            <div class="footer">
                <p>Natureza da operação: Tributação no Município • Situação tributária do ISSQN: Normal • Local da prestação: Boa Esperança</p>
                <p>Documento apenas para controle interno.</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

# =============== SIDEBAR =====================
with st.sidebar:
    st.subheader("⚙️ Parâmetros")
    cod_servico = st.text_input("Código do serviço", value="01.07")
    aliquota = st.number_input("Alíquota (%)", value=3.00, step=0.5)

texto_relatorio = st.text_area(
    "Cole aqui o relatório de hospedagem:",
    height=250,
    value="""JHS PALACE HOTEL
CNPJ: 04.608.009/0001-30
Check-in: 06/11/2025 13:50:14
Check-out: 07/11/2025 12:00:00
Dias hospedagem: 1
Agenciador: BOOKING
Hóspede
Adriano Lima

Adulto\t06/11/2025 13:50:14\t07/11/2025 13:05:00\tR$ 261,36\tR$ 0,00
Total Taxa: R$ 0,03
Total da conta\tR$ 261,39
"""
)

col1, col2 = st.columns(2)

if st.button("Gerar NFS-e (espelho)"):
    dados = parse_relatorio(texto_relatorio)
    nf = montar_nfse_fake(dados, cod_servico, aliquota)
    html = gerar_html_nfse(nf)

    with col1:
        st.subheader("Dados interpretados do relatório")
        st.json(dados)
        st.subheader("NFS-e (dados)")
        st.json(nf)

    with col2:
        st.subheader("Visual da NFS-e (espelho) v2")
        st.components.v1.html(html, height=780, scrolling=True)

        st.download_button(
            "⬇️ Baixar HTML da NFS-e",
            data=html,
            file_name=f"nfse_espelho_{nf['numero_nfse']}.html",
            mime="text/html",
        )
else:
    st.info("Cole o relatório e clique em **Gerar NFS-e (espelho)**.")
