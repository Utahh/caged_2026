from pathlib import Path
import base64
import io

import fitz
import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import requests
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

MESES = {1: "Janeiro", 2: "Fevereiro", 3: "Março"}
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


def montar_contexto_ia_caged(df_caged_completo: pl.DataFrame) -> list[dict]:
    """Consolida histórico CAGED para contexto de IA sem payload gigante."""
    if df_caged_completo.is_empty():
        return []

    if "ano_referencia" in df_caged_completo.columns:
        ano_expr = pl.col("ano_referencia").cast(pl.Int64).alias("ano")
    else:
        ano_expr = pl.lit(2026).cast(pl.Int64).alias("ano")

    df_ia_contexto = (
        df_caged_completo.with_columns(
            [
                ano_expr,
                pl.col("mes_referencia").cast(pl.Int64).alias("mes"),
                pl.col("Atividade Econômica").cast(pl.String).alias("atividade_economica"),
                pl.col("admissao").cast(pl.Float64).fill_null(0.0).alias("admissoes"),
                pl.col("demissao").cast(pl.Float64).fill_null(0.0).alias("desligamentos"),
            ]
        )
        .group_by(["ano", "mes", "atividade_economica"])
        .agg(
            [
                pl.col("admissoes").sum().alias("total_admissoes"),
                pl.col("desligamentos").sum().alias("total_desligamentos"),
                (pl.col("admissoes").sum() - pl.col("desligamentos").sum()).alias("saldo_liquido"),
            ]
        )
        .sort(["ano", "mes", "atividade_economica"])
    )
    return df_ia_contexto.to_dicts()


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
        month = st.selectbox("Mês", [1, 2, 3], format_func=lambda m: MESES[m], index=2)
        grupos = (
            ["Todos"] + sorted(caged.select(pl.col("Grande Grupo").unique()).to_series().drop_nulls().to_list())
            if not caged.is_empty()
            else ["Todos"]
        )
        grupo = st.selectbox("Atividade Econômica", grupos, index=0)
with menu_r:
    st.empty()

if not caged.is_empty():
    c = caged.filter(pl.col("mes_referencia").is_in([1, 2, 3]))
    if grupo != "Todos":
        c = c.filter(pl.col("Grande Grupo") == grupo)

    c_month = c.filter(pl.col("mes_referencia") == month)
    c_prev = c.filter(pl.col("mes_referencia") == max(1, month - 1))

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

    monthly = (
        c.group_by("mes_referencia")
        .agg(
            [
                pl.col("admissao").sum().alias("Admissões"),
                pl.col("demissao").sum().alias("Desligamentos"),
                (pl.col("admissao").sum() - pl.col("demissao").sum()).alias("Saldo"),
            ]
        )
        .sort("mes_referencia")
        .with_columns(pl.col("mes_referencia").replace_strict(MESES).alias("Mês"))
    )
    monthly_pd = monthly.to_pandas()

    st.markdown("## Evolução Mensal CAGED")
    fig_line = go.Figure()
    fig_line.add_trace(
        go.Scatter(
            x=monthly_pd["Mês"],
            y=monthly_pd["Admissões"],
            mode="lines+markers",
            name="Admissões",
            line=dict(color="#2563eb", width=3),
            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
        )
    )
    fig_line.add_trace(
        go.Scatter(
            x=monthly_pd["Mês"],
            y=monthly_pd["Desligamentos"],
            mode="lines+markers",
            name="Desligamentos",
            line=dict(color="#1e3a8a", width=3),
            hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
        )
    )
    fig_line.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", title="Admissões x Desligamentos")
    st.plotly_chart(fig_line, theme="streamlit", use_container_width=True)

    fig_bar = px.bar(monthly_pd, x="Mês", y="Saldo", title="Evolução do Saldo Mensal")
    fig_bar.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    fig_bar.update_traces(hovertemplate="%{y:,.0f}<extra></extra>")
    st.plotly_chart(fig_bar, theme="streamlit", use_container_width=True)

    rank_subclasse = (
        c.filter(pl.col("mes_referencia") == 3)
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
        c.filter(pl.col("mes_referencia") == 3)
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

    st.markdown("## Top 5 Subclasses (Março)")
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
