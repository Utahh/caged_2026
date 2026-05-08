from pathlib import Path
import base64
import io
from datetime import date, timedelta

import fitz
import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st

st.set_page_config(layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
    <style>
        :root {
            --bg-color: #f1f5f9;
            --card-color: #ffffff;
            --text-color: #0f172a;
            --muted-color: #475569;
            --border-color: #e2e8f0;
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --bg-color: #0b1220;
                --card-color: #111827;
                --text-color: #e5e7eb;
                --muted-color: #cbd5e1;
                --border-color: #334155;
            }
        }
        .stApp { background-color: var(--bg-color); color: var(--text-color); }
        .block-container { padding-top: 1.2rem; max-width: 1400px; }
        h1, h2, h3, p, label, span, div { color: inherit; }
        div[data-testid="stMetric"] {
            background: var(--card-color);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 0.4rem 0.6rem 0.5rem 0.6rem;
        }
        div[data-testid="stMetricLabel"] p { color: var(--muted-color) !important; }
        div[data-testid="stMetricValue"] { color: var(--text-color) !important; }
        div[data-testid="stMetricDelta"] { color: inherit !important; }
        div[data-testid="stDataFrame"] {
            background: var(--card-color);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
        }
        @media (max-width: 768px) {
            div[data-testid="column"] {
                width: 48% !important;
                flex: 1 1 48% !important;
                display: inline-block !important;
                min-width: 48% !important;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

MESES = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}
POUPANCA_CODIGOS = ["111310100", "111310200"]


def br_int(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def br_money(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def mom(atual: float, anterior: float) -> float:
    if anterior == 0:
        return 0.0 if atual == 0 else 100.0
    return ((atual - anterior) / abs(anterior)) * 100


def path_exist(paths: list[Path]) -> Path | None:
    return next((p for p in paths if p.exists()), None)


def format_pct(v: float) -> str:
    return f"{v:+.1f}%".replace(".", ",")


def gerar_pdf_caged(df_caged_completo: pl.DataFrame, mes_selecionado: int, ano: str = "2026") -> io.BytesIO:
    meses = {
        1: "Janeiro",
        2: "Fevereiro",
        3: "Março",
        4: "Abril",
        5: "Maio",
        6: "Junho",
        7: "Julho",
        8: "Agosto",
        9: "Setembro",
        10: "Outubro",
        11: "Novembro",
        12: "Dezembro",
    }

    caged_all = df_caged_completo.filter(pl.col("mes_referencia").is_not_null())
    caged_mes = caged_all.filter(pl.col("mes_referencia") == mes_selecionado)
    caged_prev = caged_all.filter(pl.col("mes_referencia") == max(1, mes_selecionado - 1))

    admissoes = float(caged_mes.select(pl.col("admissao").sum()).item() or 0.0)
    desligamentos = float(caged_mes.select(pl.col("demissao").sum()).item() or 0.0)
    saldo = admissoes - desligamentos

    adm_prev = float(caged_prev.select(pl.col("admissao").sum()).item() or 0.0)
    des_prev = float(caged_prev.select(pl.col("demissao").sum()).item() or 0.0)
    saldo_prev = adm_prev - des_prev
    variacao_perc = mom(saldo, saldo_prev)

    setores = (
        caged_mes.group_by("Grande Grupo")
        .agg((pl.col("admissao").sum() - pl.col("demissao").sum()).alias("Saldo"))
        .sort("Saldo", descending=True)
    )
    maior_setor = setores.item(0, "Grande Grupo") if not setores.is_empty() else "Sem dados"
    menor_setor = setores.sort("Saldo").item(0, "Grande Grupo") if not setores.is_empty() else "Sem dados"

    estoque_ativo = float(caged_all.select(pl.col("saldomovimentacao").abs().sum()).item() or 0.0)

    if "ano_referencia" in caged_all.columns:
        caged_ano = caged_all.filter(
            (pl.col("ano_referencia").cast(pl.String) == str(ano)) & (pl.col("mes_referencia") <= mes_selecionado)
        )
    else:
        caged_ano = caged_all.filter(pl.col("mes_referencia") <= mes_selecionado)
    novos_empregos = float(caged_ano.select(pl.col("saldomovimentacao").abs().sum()).item() or 0.0)

    payload = {
        "mes_ano": f"{meses.get(mes_selecionado, str(mes_selecionado))} de {ano}",
        "admissoes": br_int(admissoes),
        "desligamentos": br_int(desligamentos),
        "saldo": f"{saldo:+.0f}",
        "variacao_perc": format_pct(variacao_perc),
        "maior_setor": str(maior_setor),
        "menor_setor": str(menor_setor),
        "estoque_ativo": br_int(estoque_ativo),
        "novos_empregos": br_int(novos_empregos),
    }

    caixas = {
        "mes_ano": fitz.Rect(373, 389 - 25, 600, 389 + 10),
        "admissoes": fitz.Rect(92, 484 - 25, 179, 484 + 10),
        "desligamentos": fitz.Rect(235, 484 - 25, 334, 484 + 10),
        "saldo": fitz.Rect(383, 484 - 25, 473, 484 + 10),
        "variacao_perc": fitz.Rect(527, 484 - 25, 724, 484 + 10),
        "maior_setor": fitz.Rect(92, 726 - 25, 179, 726 + 10),
        "menor_setor": fitz.Rect(235, 726 - 25, 334, 726 + 10),
        "estoque_ativo": fitz.Rect(382, 726 - 25, 473, 726 + 10),
        "novos_empregos": fitz.Rect(527, 726 - 25, 724, 726 + 10),
    }

    root = Path(__file__).resolve().parent
    template_path = root / "template_caged.pdf"
    template_b64_path = root / "template_caged.b64"
    if template_path.exists():
        doc = fitz.open(template_path)
    elif template_b64_path.exists():
        pdf_bytes = base64.b64decode(template_b64_path.read_text(encoding="utf-8"))
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    else:
        raise FileNotFoundError("template_caged.pdf não encontrado na raiz do projeto.")
    try:
        page = doc[0]
        for chave, rect in caixas.items():
            texto = str(payload.get(chave, ""))
            fontsize = 14
            if chave in {"maior_setor", "menor_setor"}:
                if len(texto) > 28:
                    fontsize = 9
                elif len(texto) > 20:
                    fontsize = 10
                else:
                    fontsize = 11

            page.insert_textbox(
                rect,
                texto,
                fontsize=fontsize,
                fontname="helv",
                color=(0.2, 0.2, 0.2),
                align=fitz.TEXT_ALIGN_CENTER,
            )

        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return buffer
    finally:
        doc.close()


def format_mi(v: float) -> str:
    return f"R$ {v / 1_000_000:.1f} M".replace(".", ",")


def format_compact_brl(v: float) -> str:
    abs_v = abs(v)
    if abs_v >= 1_000_000_000:
        return f"R$ {v / 1_000_000_000:.1f} bi".replace(".", ",")
    if abs_v >= 1_000_000:
        return f"R$ {v / 1_000_000:.1f} M".replace(".", ",")
    if abs_v >= 1_000:
        return f"R$ {v / 1_000:.1f} mil".replace(".", ",")
    return f"R$ {v:,.0f}".replace(",", ".")


def format_data_ref_extenso(data_ref: str) -> str:
    try:
        ano_txt, mes_txt = data_ref.split("-")
        mes_num = int(mes_txt)
        mes_nome = {
            1: "Janeiro",
            2: "Fevereiro",
            3: "Março",
            4: "Abril",
            5: "Maio",
            6: "Junho",
            7: "Julho",
            8: "Agosto",
            9: "Setembro",
            10: "Outubro",
            11: "Novembro",
            12: "Dezembro",
        }.get(mes_num, mes_txt)
        return f"{mes_nome} de {ano_txt}"
    except Exception:
        return data_ref


def format_data_ref_curta(data_ref: str) -> str:
    try:
        ano_txt, mes_txt = data_ref.split("-")
        mes_num = int(mes_txt)
        meses_curto = {
            1: "Jan",
            2: "Fev",
            3: "Mar",
            4: "Abr",
            5: "Mai",
            6: "Jun",
            7: "Jul",
            8: "Ago",
            9: "Set",
            10: "Out",
            11: "Nov",
            12: "Dez",
        }
        return f"{meses_curto.get(mes_num, mes_txt)}/{str(ano_txt)[-2:]}"
    except Exception:
        return data_ref


def format_brl_full(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def processar_kpis_financeiros(
    df_fin: pl.DataFrame, mes_atual: int, ano_atual: int
) -> tuple[dict, pl.DataFrame, pl.DataFrame]:
    if df_fin.is_empty():
        vazio = {
            "total_dinheiro": 0.0,
            "total_poupanca": 0.0,
            "instituicao_top_1": "Sem dados",
            "saldo_ano_anterior": None,
            "variacao_yoy": None,
            "mes_atual": mes_atual,
            "ano_atual": ano_atual,
        }
        return vazio, pl.DataFrame(), pl.DataFrame()

    base = df_fin.with_columns(
        [
            pl.col("Ano").cast(pl.Int64),
            pl.col("Mes").cast(pl.Int64),
            pl.col("Instituição Financeira").cast(pl.String).alias("Instituicao"),
            pl.col("Saldo").cast(pl.Float64).fill_null(0.0).alias("Saldo_em_Reais"),
            pl.when(pl.col("Codigo_Contabil").is_in(POUPANCA_CODIGOS))
            .then(pl.lit("Poupança"))
            .when(pl.col("Instituição Financeira").str.contains("Fundos", literal=True))
            .then(pl.lit("Fundo de Investimento"))
            .otherwise(pl.lit("Conta Corrente"))
            .alias("Tipo_Conta"),
        ]
    )

    tot_mes = (
        base.group_by(["Ano", "Mes"])
        .agg(pl.col("Saldo_em_Reais").sum().alias("Saldo_Total"))
        .sort(["Ano", "Mes"])
    )
    tot_mes_valid = tot_mes.filter(pl.col("Saldo_Total") > 0)
    if not tot_mes_valid.is_empty():
        ultimo = tot_mes_valid.tail(1)
        ano_atual = int(ultimo.select("Ano").item())
        mes_atual = int(ultimo.select("Mes").item())

    atual = base.filter((pl.col("Ano") == ano_atual) & (pl.col("Mes") == mes_atual))
    total_dinheiro = float(atual.select(pl.col("Saldo_em_Reais").sum()).item() or 0.0)
    total_poupanca = float(
        atual.filter(pl.col("Tipo_Conta") == "Poupança").select(pl.col("Saldo_em_Reais").sum()).item() or 0.0
    )

    distribuicao_bancos = (
        atual.group_by("Instituicao")
        .agg(pl.col("Saldo_em_Reais").sum().alias("Total"))
        .sort("Total", descending=True)
    )
    instituicao_top_1 = str(distribuicao_bancos.row(0)[0]) if not distribuicao_bancos.is_empty() else "Sem dados"

    ord_atual = ano_atual * 12 + mes_atual
    evolucao_12_meses = (
        base.with_columns((pl.col("Ano") * 12 + pl.col("Mes")).alias("ord_mes"))
        .filter((pl.col("ord_mes") >= ord_atual - 11) & (pl.col("ord_mes") <= ord_atual))
        .group_by(["Ano", "Mes"])
        .agg(pl.col("Saldo_em_Reais").sum().alias("Total"))
        .sort(["Ano", "Mes"])
    )

    saldo_ano_anterior = (
        base.filter((pl.col("Ano") == ano_atual - 1) & (pl.col("Mes") == mes_atual))
        .select(pl.col("Saldo_em_Reais").sum())
        .item()
    )
    saldo_ano_anterior = float(saldo_ano_anterior) if saldo_ano_anterior is not None else None
    variacao_yoy = None
    if saldo_ano_anterior is not None and saldo_ano_anterior != 0:
        variacao_yoy = mom(total_dinheiro, float(saldo_ano_anterior))

    kpis = {
        "total_dinheiro": total_dinheiro,
        "total_poupanca": total_poupanca,
        "instituicao_top_1": instituicao_top_1,
        "saldo_ano_anterior": saldo_ano_anterior,
        "variacao_yoy": variacao_yoy,
        "mes_atual": mes_atual,
        "ano_atual": ano_atual,
    }
    return kpis, evolucao_12_meses, distribuicao_bancos


def processar_dados_estban(df_estban: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    if df_estban.is_empty():
        vazio_kpi = pl.DataFrame({"valor_atual": [0.0], "delta_perc": [0.0], "data_ref": ["Sem dados"]})
        return vazio_kpi, pl.DataFrame(), pl.DataFrame()

    base = (
        df_estban.with_columns(
            [
                pl.col("valor_poupanca").cast(pl.Float64).fill_null(0.0),
                pl.col("instituicao").cast(pl.String),
                pl.col("data_ref").cast(pl.String),
            ]
        )
        .filter(pl.col("valor_poupanca").is_not_null())
    )

    # Considera defasagem operacional de 60 dias do ESTBAN.
    cutoff = (date.today() - timedelta(days=60)).strftime("%Y-%m")
    base = base.filter(pl.col("data_ref") <= cutoff)

    df_tendencia = (
        base.group_by("data_ref")
        .agg(pl.col("valor_poupanca").sum().alias("valor_total"))
        .sort("data_ref")
        .tail(12)
    )

    if df_tendencia.height == 0:
        vazio_kpi = pl.DataFrame({"valor_atual": [0.0], "delta_perc": [0.0], "data_ref": ["Sem dados"]})
        return vazio_kpi, pl.DataFrame(), pl.DataFrame()

    ref_atual = df_tendencia.select(pl.col("data_ref").max()).item()
    valor_atual = float(
        df_tendencia.filter(pl.col("data_ref") == ref_atual).select(pl.col("valor_total").sum()).item() or 0.0
    )

    datas = df_tendencia.select("data_ref").to_series().to_list()
    idx_atual = datas.index(ref_atual)
    valor_anterior = 0.0
    if idx_atual > 0:
        ref_ant = datas[idx_atual - 1]
        valor_anterior = float(
            df_tendencia.filter(pl.col("data_ref") == ref_ant).select(pl.col("valor_total").sum()).item() or 0.0
        )
    delta_perc = mom(valor_atual, valor_anterior)

    df_kpi_atual = pl.DataFrame(
        {"valor_atual": [valor_atual], "delta_perc": [delta_perc], "data_ref": [ref_atual]}
    )

    df_bancos_ranking = (
        base.filter(pl.col("data_ref") == ref_atual)
        .group_by("instituicao")
        .agg(pl.col("valor_poupanca").sum().alias("valor_total"))
        .filter(pl.col("valor_total") > 0)
        .sort("valor_total", descending=True)
    )

    return df_kpi_atual, df_bancos_ranking, df_tendencia


@st.cache_data
def load_data() -> tuple[pl.DataFrame, pl.DataFrame]:
    root = Path(__file__).resolve().parent
    data_dir = root / "data"
    caged_path = path_exist(
        [
            root / "relatorio_botucatu_q1_2026.csv",
            root / "caged_botucatu_q1_2026.csv",
            data_dir / "relatorio_botucatu_q1_2026.csv",
            data_dir / "caged_botucatu_q1_2026.csv",
        ]
    )
    fin_path = path_exist(
        [
            root / "investimentos_botucatu_2026.csv",
            root / "financas_botucatu_2026.csv",
            data_dir / "investimentos_botucatu_2026.csv",
            data_dir / "financas_botucatu_2026.csv",
        ]
    )
    caged = pl.read_csv(caged_path, separator=";") if caged_path else pl.DataFrame()
    fin = pl.read_csv(fin_path, separator=";") if fin_path else pl.DataFrame()
    return caged, fin


def normalize_caged(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df
    section = pl.col("secao").cast(pl.String).str.to_uppercase()
    grande = (
        pl.when(section == "C")
        .then(pl.lit("Indústria"))
        .when(section == "G")
        .then(pl.lit("Comércio"))
        .when(section == "F")
        .then(pl.lit("Construção"))
        .when(section == "A")
        .then(pl.lit("Agropecuária"))
        .when(section.is_in(["H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U"]))
        .then(pl.lit("Serviços"))
        .otherwise(pl.lit("Não Identificado"))
    )
    subclasse_desc = (
        pl.when(pl.col("subclasse_descricao").is_not_null())
        .then(pl.col("subclasse_descricao").cast(pl.String))
        .otherwise(pl.lit("Não Identificado"))
    )
    return df.with_columns(
        [
            (pl.col("ano_referencia").cast(pl.Int64) if "ano_referencia" in df.columns else pl.lit(2026)).alias(
                "ano_referencia"
            ),
            pl.col("mes_referencia").cast(pl.Int64),
            pl.col("admissao").cast(pl.Float64).fill_null(0.0),
            pl.col("demissao").cast(pl.Float64).fill_null(0.0),
            pl.col("saldomovimentacao").cast(pl.Float64).fill_null(0.0),
            pl.col("subclasse").cast(pl.String).str.zfill(7),
            grande.alias("Grande Grupo"),
            grande.alias("Atividade Econômica"),
            subclasse_desc.alias("CNAE 2.0 Subclasse"),
        ]
    )


def normalize_fin(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return df
    saldo_col = "Saldo_em_Reais" if "Saldo_em_Reais" in df.columns else "Saldo em Reais"
    cod_col = "Codigo_Contabil" if "Codigo_Contabil" in df.columns else "Código Contábil"
    mes_col = "Mes" if "Mes" in df.columns else "Mês"
    ano_col = "Ano" if "Ano" in df.columns else None
    mapa = pl.DataFrame(
        {
            "Codigo_Contabil": [
                "111110100",
                "111110200",
                "111110603",
                "111110604",
                "111111900",
                "111115000",
                "111310100",
                "111310200",
            ],
            "Instituição Financeira": [
                "Caixa da Prefeitura",
                "Banco do Brasil",
                "Caixa Econômica Federal",
                "Santander / Outros",
                "Fundos de Investimento",
                "Aplicações em Renda Fixa",
                "Poupança Municipal",
                "Poupança Vinculada",
            ],
        }
    )
    return (
        df.with_columns(
            [
                pl.col(cod_col).cast(pl.String).alias("Codigo_Contabil"),
                pl.col(mes_col).cast(pl.Int64).alias("Mes"),
                (pl.col(ano_col).cast(pl.Int64) if ano_col else pl.lit(2026)).alias("Ano"),
                pl.col(saldo_col)
                .cast(pl.String)
                .str.replace(",", ".")
                .cast(pl.Float64, strict=False)
                .fill_null(0.0)
                .alias("Saldo"),
            ]
        )
        .join(mapa, on="Codigo_Contabil", how="left")
        .with_columns(pl.col("Instituição Financeira").fill_null("Outros"))
    )


caged_raw, fin_raw = load_data()
if caged_raw.is_empty() and fin_raw.is_empty():
    st.warning("⚠️ Arquivos não encontrados na raiz do projeto.")
    st.stop()

caged = normalize_caged(caged_raw)
fin = normalize_fin(fin_raw)

st.title("📊 Dashboard Executivo Março 2026")

menu_l, menu_r = st.columns([9, 1])
with menu_l:
    with st.popover("☰ Filtros"):
        anos_disponiveis = (
            sorted(caged.select(pl.col("ano_referencia").unique()).to_series().drop_nulls().to_list())
            if not caged.is_empty() and "ano_referencia" in caged.columns
            else [2026]
        )
        ano = st.selectbox("Ano", anos_disponiveis, index=len(anos_disponiveis) - 1)
        month = st.selectbox("Mês", list(range(1, 13)), format_func=lambda m: MESES[m], index=2)
        grupos = (
            ["Todos"] + sorted(caged.select(pl.col("Grande Grupo").unique()).to_series().drop_nulls().to_list())
            if not caged.is_empty()
            else ["Todos"]
        )
        grupo = st.selectbox("Atividade Econômica", grupos, index=0)
with menu_r:
    st.empty()

if not caged.is_empty():
    st.markdown("## Emprego (CAGED)")
    c = caged.filter(pl.col("ano_referencia") == ano)
    if grupo != "Todos":
        c = c.filter(pl.col("Grande Grupo") == grupo)

    c_month = c.filter(pl.col("mes_referencia") == month)
    prev_year, prev_month = (ano, month - 1) if month > 1 else (ano - 1, 12)
    c_prev = caged.filter((pl.col("ano_referencia") == prev_year) & (pl.col("mes_referencia") == prev_month))
    if grupo != "Todos":
        c_prev = c_prev.filter(pl.col("Grande Grupo") == grupo)

    adm = float(c_month.select(pl.col("admissao").sum()).item())
    des = float(c_month.select(pl.col("demissao").sum()).item())
    saldo = adm - des
    estoque = float(c.select(pl.col("saldomovimentacao").sum()).item())
    adm_prev = float(c_prev.select(pl.col("admissao").sum()).item())
    des_prev = float(c_prev.select(pl.col("demissao").sum()).item())
    saldo_prev = adm_prev - des_prev

    k1, k2 = st.columns(2)
    k3, k4 = st.columns(2)
    k1.metric("Admissões", br_int(adm), f"{mom(adm, adm_prev):+.1f}%")
    k2.metric("Desligamentos", br_int(des), f"{mom(des, des_prev):+.1f}%", delta_color="inverse")
    k3.metric("Saldo", br_int(saldo), f"{mom(saldo, saldo_prev):+.1f}%")
    k4.metric("Estoque", br_int(estoque))

    c_12m = caged.with_columns((pl.col("ano_referencia") * 12 + pl.col("mes_referencia")).alias("ord_mes"))
    ord_atual = ano * 12 + month
    c_12m = c_12m.filter((pl.col("ord_mes") >= ord_atual - 11) & (pl.col("ord_mes") <= ord_atual))
    if grupo != "Todos":
        c_12m = c_12m.filter(pl.col("Grande Grupo") == grupo)

    monthly = (
        c_12m.group_by(["ano_referencia", "mes_referencia"])
        .agg(
            [
                pl.col("admissao").sum().alias("Admissões"),
                pl.col("demissao").sum().alias("Desligamentos"),
                (pl.col("admissao").sum() - pl.col("demissao").sum()).alias("Saldo"),
            ]
        )
        .sort(["ano_referencia", "mes_referencia"])
        .with_columns(
            pl.concat_str(
                [
                    pl.col("mes_referencia").replace_strict(MESES),
                    pl.lit("/"),
                    pl.col("ano_referencia").cast(pl.String),
                ]
            ).alias("Mês")
        )
    )
    monthly_pd = monthly.to_pandas()
    monthly_pd["Mês Curto"] = monthly_pd.apply(
        lambda r: format_data_ref_curta(f"{int(r['ano_referencia'])}-{int(r['mes_referencia']):02d}"),
        axis=1,
    )
    monthly_pd["Admissões_BR"] = monthly_pd["Admissões"].apply(lambda v: br_int(float(v)))
    monthly_pd["Desligamentos_BR"] = monthly_pd["Desligamentos"].apply(lambda v: br_int(float(v)))
    monthly_pd["Saldo_BR"] = monthly_pd["Saldo"].apply(lambda v: br_int(float(v)))

    st.markdown("## Evolução Mensal CAGED")
    fig_line = go.Figure()
    fig_line.add_trace(
        go.Scatter(
            x=monthly_pd["Mês Curto"],
            y=monthly_pd["Admissões"],
            mode="lines+markers",
            name="Admissões",
            line=dict(color="#2563eb", width=3),
            customdata=monthly_pd[["Mês", "Admissões_BR"]].values,
            hovertemplate="%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
        )
    )
    fig_line.add_trace(
        go.Scatter(
            x=monthly_pd["Mês Curto"],
            y=monthly_pd["Desligamentos"],
            mode="lines+markers",
            name="Desligamentos",
            line=dict(color="#1e3a8a", width=3),
            customdata=monthly_pd[["Mês", "Desligamentos_BR"]].values,
            hovertemplate="%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
        )
    )
    fig_line.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        title="Admissões x Desligamentos",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis=dict(nticks=6),
    )
    st.plotly_chart(fig_line, theme="streamlit", use_container_width=True)

    fig_bar = px.bar(monthly_pd, x="Mês Curto", y="Saldo", title="Evolução do Saldo Mensal")
    fig_bar.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    fig_bar.update_traces(
        customdata=monthly_pd[["Mês", "Saldo_BR"]].values,
        hovertemplate="%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
    )
    st.plotly_chart(fig_bar, theme="streamlit", use_container_width=True)

    rank_subclasse = (
        c.filter(pl.col("mes_referencia") == month)
        .group_by("CNAE 2.0 Subclasse")
        .agg(
            [
                pl.col("admissao").sum().alias("Admissões"),
                pl.col("demissao").sum().alias("Desligamentos"),
                (pl.col("admissao").sum() - pl.col("demissao").sum()).alias("Saldo"),
            ]
        )
        .sort("Saldo", descending=True)
    )
    total_saldo = float(rank_subclasse.select(pl.col("Saldo").sum()).item())
    rank_subclasse = rank_subclasse.with_columns(
        pl.when(pl.lit(total_saldo != 0))
        .then((pl.col("Saldo") / pl.lit(total_saldo)) * 100)
        .otherwise(pl.lit(0.0))
        .alias("% Impacto")
    ).with_columns(
        pl.col("CNAE 2.0 Subclasse").str.slice(0, 42).alias("CNAE 2.0 Subclasse")
    )
    top_maiores = rank_subclasse.head(5)
    top_menores = rank_subclasse.tail(5).sort("Saldo")

    saldo_atividade = (
        c.filter(pl.col("mes_referencia") == month)
        .group_by("Atividade Econômica")
        .agg((pl.col("admissao").sum() - pl.col("demissao").sum()).alias("Saldo"))
        .select(["Atividade Econômica", "Saldo"])
        .sort("Saldo", descending=True)
        .head(10)
    )
    saldo_atividade_pd = saldo_atividade.to_pandas()

    st.markdown("## Saldo por Atividade Econômica")
    fig_hbar = px.bar(
        saldo_atividade_pd,
        x="Saldo",
        y="Atividade Econômica",
        orientation="h",
        text="Saldo",
        title="Atividade Econômica com maior saldo (ordem decrescente)",
    )
    fig_hbar.update_traces(
        texttemplate="%{text:,.0f}",
        textposition="outside",
        hovertemplate="%{x:,.0f}<extra></extra>",
        cliponaxis=False,
    )
    fig_hbar.update_layout(
        yaxis=dict(categoryorder="total ascending"),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_hbar, theme="streamlit", use_container_width=True)

    st.markdown(f"## Top 5 Subclasses ({MESES[month]}/{ano})")
    a1, a2 = st.columns(2)
    with a1:
        st.markdown("### Maiores Saldos")
        st.dataframe(
            top_maiores.select(["CNAE 2.0 Subclasse", "Saldo", "Admissões", "Desligamentos", "% Impacto"]).to_pandas(),
            use_container_width=True,
            hide_index=True,
            height=220,
        )
    with a2:
        st.markdown("### Menores Saldos")
        st.dataframe(
            top_menores.select(["CNAE 2.0 Subclasse", "Saldo", "Admissões", "Desligamentos", "% Impacto"]).to_pandas(),
            use_container_width=True,
            hide_index=True,
            height=220,
        )

st.divider()
st.header("📊 Visão Financeira e Liquidez (Caixa e Aplicações)")

try:
    if fin.is_empty():
        st.warning("Base financeira indisponível para análise de liquidez.")
    else:
        kpis_fin, evolucao_12_meses, distribuicao_bancos = processar_kpis_financeiros(fin, month, ano)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total em Caixa/Aplicações", format_mi(kpis_fin["total_dinheiro"]))
        k2.metric("Total em Poupança", format_brl_full(kpis_fin["total_poupanca"]))
        k3.metric("Maior Concentração", kpis_fin["instituicao_top_1"])
        if kpis_fin["variacao_yoy"] is None:
            k4.metric("Comparativo Anual (YoY)", "Sem base", "N/D")
        else:
            k4.metric("Comparativo Anual (YoY)", format_brl_full(kpis_fin["total_dinheiro"]), format_pct(kpis_fin["variacao_yoy"]))

        st.caption(
            f"Dados de referência: {MESES.get(kpis_fin['mes_atual'], str(kpis_fin['mes_atual']))} de {kpis_fin['ano_atual']}"
        )

        c1, c2 = st.columns([2, 1])
        with c1:
            if evolucao_12_meses.is_empty():
                st.info("Sem dados para evolução financeira.")
            else:
                evo_pd = evolucao_12_meses.to_pandas()
                evo_pd["Mês"] = evo_pd.apply(
                    lambda r: format_data_ref_curta(f"{int(r['Ano'])}-{int(r['Mes']):02d}"),
                    axis=1,
                )
                evo_pd["Mês Extenso"] = evo_pd.apply(
                    lambda r: format_data_ref_extenso(f"{int(r['Ano'])}-{int(r['Mes']):02d}"),
                    axis=1,
                )
                evo_pd["Total BRL"] = evo_pd["Total"].apply(lambda x: format_brl_full(float(x)))
                fig_evo = px.line(
                    evo_pd,
                    x="Mês",
                    y="Total",
                    markers=True,
                    title="Evolução do Saldo (Últimos 12 Meses)",
                    labels={"Mês": "Mês", "Total": "Total"},
                )
                fig_evo.update_traces(
                    line=dict(color="#1e3a8a", width=3),
                    hovertemplate="%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
                    customdata=evo_pd[["Mês Extenso", "Total BRL"]].values,
                )
                fig_evo.update_layout(
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(showgrid=False, nticks=6),
                    yaxis=dict(showgrid=False, tickformat="~s", tickprefix="R$ "),
                )
                st.plotly_chart(fig_evo, theme="streamlit", use_container_width=True)

        with c2:
            if distribuicao_bancos.is_empty():
                st.info("Sem dados para distribuição por instituição.")
            else:
                dist_pd = distribuicao_bancos.to_pandas()
                dist_pd["Total BRL"] = dist_pd["Total"].apply(lambda x: format_brl_full(float(x)))
                fig_dist = px.bar(
                    dist_pd,
                    x="Total",
                    y="Instituicao",
                    orientation="h",
                    title="Saldo por Instituição",
                    text_auto=".2s",
                    labels={"Total": "Total", "Instituicao": "Instituição"},
                )
                fig_dist.update_traces(
                    hovertemplate="%{y}<br>%{customdata}<extra></extra>",
                    customdata=dist_pd["Total BRL"],
                )
                fig_dist.update_layout(
                    yaxis=dict(categoryorder="total ascending", showgrid=False),
                    xaxis=dict(visible=False, showgrid=False),
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_dist, theme="streamlit", use_container_width=True)

        st.caption(
            "Fonte: dados financeiros municipais (Siconfi/ESTBAN-like) consolidados para Botucatu. "
            "Os dados podem apresentar defasagem de até 60 dias em relação ao mês corrente."
        )
except Exception as exc:
    st.error(f"Não foi possível renderizar a Visão Financeira e Liquidez: {exc}")
