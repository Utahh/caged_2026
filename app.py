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
            min-height: 120px;
        }
        div[data-testid="stMetricLabel"] p { color: var(--muted-color) !important; }
        div[data-testid="stMetricValue"] {
            color: var(--text-color) !important;
            font-size: clamp(1.35rem, 2.1vw, 2.15rem) !important;
            line-height: 1.2 !important;
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            word-break: break-word !important;
        }
        div[data-testid="stMetricDelta"] { color: inherit !important; }
        div[data-testid="stDataFrame"] {
            background: var(--card-color);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
        }
        [data-testid="stPlotlyChart"] .js-plotly-plot,
        [data-testid="stPlotlyChart"] {
            touch-action: pan-y pinch-zoom;
        }
        button[data-testid="baseButton-secondary"],
        button[data-testid="baseButton-primary"] {
            min-height: 44px;
            border-radius: 10px;
        }
        @media (max-width: 768px) {
            .block-container {
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
                padding-top: 0.75rem !important;
            }
            div[data-testid="column"] {
                width: 100% !important;
                flex: 1 1 100% !important;
                display: block !important;
                min-width: 100% !important;
            }
            div[data-testid="stMetric"] {
                min-height: 104px;
            }
            div[data-testid="stMetricValue"] {
                font-size: clamp(1.2rem, 6.2vw, 1.9rem) !important;
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


def format_usd_milhoes(v: float | None) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    x = float(v)
    if abs(x) >= 1e9:
        return f"US$ {x / 1e9:.2f} bi"
    return f"US$ {x / 1e6:.2f} mi"


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


def format_usd_fob_en(v: float | None) -> str:
    """US$ no padrão internacional: milhar com vírgula e decimal com ponto (ex.: US$ 1,234,567.89)."""
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    s = f"{float(v):,.2f}"
    return f"US$ {s}"


def csv_bytes_from_pandas(df) -> bytes:
    return df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")


def aplicar_layout_clean(fig, unified_hover: bool = False):
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=16, r=56, t=56, b=36),
        xaxis=dict(automargin=True),
        yaxis=dict(automargin=True),
        hoverlabel=dict(font_size=13),
        dragmode=False,
    )
    if unified_hover:
        fig.update_layout(hovermode="x unified")
    return fig


PLOTLY_CONFIG: dict = {
    "scrollZoom": False,
    "doubleClick": False,
    "displayModeBar": "hover",
    "modeBarButtonsToRemove": [
        "lasso2d",
        "select2d",
        "zoom2d",
        "pan2d",
        "zoomIn2d",
        "zoomOut2d",
        "autoScale2d",
        "resetScale2d",
        "hoverCompareCartesian",
    ],
}


def plotly_mobile_friendly(fig, *, key: str, **kwargs) -> None:
    """Evita que o gráfico roube o scroll vertical no celular; hover continua disponível."""
    extra_cfg = kwargs.pop("config", {})
    kwargs.pop("use_container_width", None)
    width = kwargs.pop("width", "stretch")
    st.plotly_chart(
        fig,
        theme="streamlit",
        width=width,
        config={**PLOTLY_CONFIG, **extra_cfg},
        key=key,
        **kwargs,
    )


def processar_kpis_financeiros(
    df_fin: pl.DataFrame, mes_atual: int, ano_atual: int, instituicao_filtro: str = "Todas"
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

    # Evita referência futura: nunca passar do mês/ano corrente.
    hoje = date.today()
    ord_hoje = hoje.year * 12 + hoje.month
    base = base.with_columns((pl.col("Ano") * 12 + pl.col("Mes")).alias("ord_mes")).filter(pl.col("ord_mes") <= ord_hoje)

    tot_mes = (
        base.group_by(["Ano", "Mes"])
        .agg(pl.col("Saldo_em_Reais").sum().alias("Saldo_Total"))
        .sort(["Ano", "Mes"])
    )
    tot_mes_valid = tot_mes.filter(pl.col("Saldo_Total") > 0)
    if not tot_mes_valid.is_empty():
        ultimo = tot_mes_valid.tail(1)
        ano_atual = int(ultimo.row(0)[0])
        mes_atual = int(ultimo.row(0)[1])

    atual = base.filter((pl.col("Ano") == ano_atual) & (pl.col("Mes") == mes_atual))
    if instituicao_filtro != "Todas":
        atual = atual.filter(pl.col("Instituicao") == instituicao_filtro)
    total_dinheiro = float(atual.select(pl.col("Saldo_em_Reais").sum()).item() or 0.0)
    total_poupanca = float(
        atual.filter(pl.col("Tipo_Conta") == "Poupança").select(pl.col("Saldo_em_Reais").sum()).item() or 0.0
    )

    distribuicao_bancos = (
        base.filter((pl.col("Ano") == ano_atual) & (pl.col("Mes") == mes_atual))
        .group_by("Instituicao")
        .agg(pl.col("Saldo_em_Reais").sum().alias("Total"))
        .sort("Total", descending=True)
    )
    instituicao_top_1 = (
        instituicao_filtro
        if instituicao_filtro != "Todas"
        else (str(distribuicao_bancos.row(0)[0]) if not distribuicao_bancos.is_empty() else "Sem dados")
    )

    ord_atual = ano_atual * 12 + mes_atual
    evolucao_12_meses = (
        base
        .filter((pl.col("ord_mes") >= ord_atual - 11) & (pl.col("ord_mes") <= ord_atual))
        .filter(pl.lit(instituicao_filtro == "Todas") | (pl.col("Instituicao") == instituicao_filtro))
        .group_by(["Ano", "Mes"])
        .agg(pl.col("Saldo_em_Reais").sum().alias("Total"))
        .sort(["Ano", "Mes"])
    )

    kpis = {
        "total_dinheiro": total_dinheiro,
        "total_poupanca": total_poupanca,
        "instituicao_top_1": instituicao_top_1,
        "mes_atual": mes_atual,
        "ano_atual": ano_atual,
    }
    return kpis, evolucao_12_meses, distribuicao_bancos


COD_SICONFI_FUNDOS_PREFEITURA = "111111900"
COD_SICONFI_RENDA_FIXA_PREFEITURA = "111115000"


def sum_prefeitura_fundos_e_renda_fixa(df_fin: pl.DataFrame, mes: int, ano: int) -> tuple[float, float, float]:
    """Siconfi (Prefeitura): saldos das contas de fundos e renda fixa no mês/ano informados. Não representa famílias."""
    if df_fin.is_empty():
        return float("nan"), float("nan"), float("nan")
    req = {"Codigo_Contabil", "Ano", "Mes", "Saldo"}
    if not req <= set(df_fin.columns):
        return float("nan"), float("nan"), float("nan")
    b = df_fin.with_columns(
        [
            pl.col("Ano").cast(pl.Int64),
            pl.col("Mes").cast(pl.Int64),
            pl.col("Codigo_Contabil").cast(pl.String).str.strip_chars(),
            pl.col("Saldo").cast(pl.Float64).fill_null(0.0),
        ]
    ).filter((pl.col("Ano") == ano) & (pl.col("Mes") == mes))
    f = b.filter(pl.col("Codigo_Contabil") == COD_SICONFI_FUNDOS_PREFEITURA).select(pl.col("Saldo").sum()).item()
    rf = b.filter(pl.col("Codigo_Contabil") == COD_SICONFI_RENDA_FIXA_PREFEITURA).select(pl.col("Saldo").sum()).item()
    vf = float(f) if f is not None else float("nan")
    vrf = float(rf) if rf is not None else float("nan")
    if vf != vf and vrf != vrf:
        return float("nan"), float("nan"), float("nan")
    tot = (vf if vf == vf else 0.0) + (vrf if vrf == vrf else 0.0)
    return vf, vrf, tot


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
def load_data() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
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
    comp_path = path_exist(
        [
            root / "caged_comparativo_municipios.csv",
            data_dir / "caged_comparativo_municipios.csv",
        ]
    )
    caged = pl.read_csv(caged_path, separator=";") if caged_path else pl.DataFrame()
    fin = pl.read_csv(fin_path, separator=";") if fin_path else pl.DataFrame()
    comp = pl.read_csv(comp_path, separator=";") if comp_path else pl.DataFrame()
    return caged, fin, comp


@st.cache_data
def load_cnpj_botucatu() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    root = Path(__file__).resolve().parent
    data_dir = root / "data"
    resumo_p = path_exist(
        [root / "cnpj_botucatu_resumo.csv", data_dir / "cnpj_botucatu_resumo.csv"]
    )
    mei_p = path_exist([root / "cnpj_botucatu_mei_mensal.csv", data_dir / "cnpj_botucatu_mei_mensal.csv"])
    porte_p = path_exist([root / "cnpj_botucatu_porte_pct.csv", data_dir / "cnpj_botucatu_porte_pct.csv"])
    cnae_p = path_exist([root / "cnpj_botucatu_cnae_x_tipo.csv", data_dir / "cnpj_botucatu_cnae_x_tipo.csv"])
    join_p = path_exist([root / "cnpj_botucatu_join_empresas.csv", data_dir / "cnpj_botucatu_join_empresas.csv"])
    meis_p = path_exist([root / "cnpj_botucatu_meis.csv", data_dir / "cnpj_botucatu_meis.csv"])
    muni_p = path_exist([root / "cnpj_botucatu_municipio_fonte.csv", data_dir / "cnpj_botucatu_municipio_fonte.csv"])
    resumo = pl.read_csv(resumo_p, separator=";") if resumo_p else pl.DataFrame()
    mei = pl.read_csv(mei_p, separator=";") if mei_p else pl.DataFrame()
    porte = pl.read_csv(porte_p, separator=";") if porte_p else pl.DataFrame()
    cnae = pl.read_csv(cnae_p, separator=";") if cnae_p else pl.DataFrame()
    join_df = pl.read_csv(join_p, separator=";") if join_p else pl.DataFrame()
    meis = pl.read_csv(meis_p, separator=";") if meis_p else pl.DataFrame()
    muni = pl.read_csv(muni_p, separator=";") if muni_p else pl.DataFrame()
    return resumo, mei, porte, cnae, join_df, meis, muni


@st.cache_data
def load_comex_botucatu() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    root = Path(__file__).resolve().parent
    data_dir = root / "data"
    meta_p = path_exist([root / "comex_botucatu_meta.csv", data_dir / "comex_botucatu_meta.csv"])
    men_p = path_exist([root / "comex_botucatu_mensal.csv", data_dir / "comex_botucatu_mensal.csv"])
    ex_p = path_exist([root / "comex_botucatu_top_sh4_export.csv", data_dir / "comex_botucatu_top_sh4_export.csv"])
    im_p = path_exist([root / "comex_botucatu_top_sh4_import.csv", data_dir / "comex_botucatu_top_sh4_import.csv"])
    meta = pl.read_csv(meta_p, separator=";") if meta_p else pl.DataFrame()
    men = pl.read_csv(men_p, separator=";") if men_p else pl.DataFrame()
    top_e = pl.read_csv(ex_p, separator=";") if ex_p else pl.DataFrame()
    top_i = pl.read_csv(im_p, separator=";") if im_p else pl.DataFrame()
    if not men.is_empty():
        men = (
            men.with_columns(
                [
                    pl.col("ano").cast(pl.Int64),
                    pl.col("mes").cast(pl.Int64),
                    pl.col("fluxo").cast(pl.String),
                    pl.col("valor_usd_fob").cast(pl.Float64).fill_null(0.0),
                    pl.col("ptax_media").cast(pl.Float64).fill_null(0.0),
                    pl.col("valor_brl_estimado").cast(pl.Float64).fill_null(0.0),
                ]
            )
            .with_columns((pl.col("ano") * 12 + pl.col("mes")).alias("ord_mes"))
        )
    return meta, men, top_e, top_i


@st.cache_data
def load_comex_sh4_cnae_map() -> pl.DataFrame:
    """Mapeamento heurístico SH4 → prefixo(s) de CNAE fiscal (subclasse); arquivo editável em data/."""
    root = Path(__file__).resolve().parent
    data_dir = root / "data"
    p = path_exist(
        [
            root / "comex_sh4_cnae_aproximacao.csv",
            data_dir / "comex_sh4_cnae_aproximacao.csv",
        ]
    )
    if not p:
        return pl.DataFrame(schema={"sh4": pl.Utf8, "cnae_prefix": pl.Utf8, "nota": pl.Utf8})
    df = pl.read_csv(
        p,
        separator=";",
        truncate_ragged_lines=True,
        encoding="utf8",
    )
    if "nota" not in df.columns:
        df = df.with_columns(pl.lit("").alias("nota"))
    return df.with_columns(
        pl.col("sh4").cast(pl.Utf8).str.strip_chars().str.zfill(4),
        pl.col("cnae_prefix").cast(pl.Utf8).str.strip_chars(),
        pl.col("nota").cast(pl.Utf8).fill_null(""),
    )


def empresas_botucatu_por_sh4(join_df: pl.DataFrame, map_df: pl.DataFrame, sh4: str) -> tuple[pl.DataFrame, list[str]]:
    """Filtra empresas do join municipal cujo CNAE fiscal principal começa com algum prefixo mapeado ao SH4."""
    if join_df.is_empty() or map_df.is_empty():
        return pl.DataFrame(), []
    sh4z = str(sh4).strip().zfill(4)
    sub_m = map_df.filter(pl.col("sh4") == sh4z)
    prefs = sub_m["cnae_prefix"].unique().drop_nulls().to_list()
    notas = sub_m["nota"].unique().drop_nulls().to_list() if "nota" in sub_m.columns else []
    prefs = [str(p) for p in prefs if p]
    if not prefs:
        return pl.DataFrame(), notas
    if "cnae_fiscal_principal" not in join_df.columns:
        return pl.DataFrame(), notas
    ac = pl.col("cnae_fiscal_principal").cast(pl.Utf8).str.strip_chars().fill_null("")
    or_expr = pl.any_horizontal(*[ac.str.starts_with(p) for p in prefs])
    out = join_df.filter(or_expr)
    vis = [
        c
        for c in [
            "cnpj",
            "cnae_fiscal_principal",
            "cnae_subclasse",
            "divisao_cnae",
            "divisao_descricao",
            "tipo_empresa",
            "porte_cadastral_descricao",
            "situacao_cadastral_descricao",
        ]
        if c in out.columns
    ]
    return out.select(vis) if vis else out, notas


def comex_sh4_select_labels(exp: pl.DataFrame, imp: pl.DataFrame, map_df: pl.DataFrame) -> dict[str, str]:
    labels: dict[str, str] = {}
    for frame in (exp, imp):
        if frame.is_empty() or "sh4" not in frame.columns:
            continue
        for row in frame.select(["sh4", "descricao"]).iter_rows(named=False):
            sh4 = str(row[0]).strip().zfill(4)
            desc = str(row[1] or "").strip()
            if sh4 not in labels:
                labels[sh4] = desc
    if not map_df.is_empty() and "sh4" in map_df.columns:
        for s in map_df["sh4"].unique().to_list():
            k = str(s).strip().zfill(4)
            if k not in labels:
                labels[k] = "SH4 (catálogo de aproximação)"
    return {k: f"{k} — {v[:78]}{'…' if len(v) > 78 else ''}" for k, v in sorted(labels.items())}


@st.cache_data
def load_censo_botucatu() -> pl.DataFrame:
    """Indicadores demográficos do município (Censo/IBGE) via CSV opcional."""
    root = Path(__file__).resolve().parent
    data_dir = root / "data"
    p = path_exist(
        [
            root / "censo_botucatu_indicadores.csv",
            data_dir / "censo_botucatu_indicadores.csv",
        ]
    )
    empty = pl.DataFrame(
        schema={
            "indicador": pl.Utf8,
            "valor": pl.Float64,
            "unidade": pl.Utf8,
            "referencia": pl.Utf8,
            "fonte": pl.Utf8,
        }
    )
    if not p:
        return empty
    df = pl.read_csv(p, separator=";")
    ren = {c: c.strip().lower() for c in df.columns}
    df = df.rename(ren)
    if "indicador" not in df.columns or "valor" not in df.columns:
        return empty
    exprs = [
        pl.col("indicador").cast(pl.Utf8).str.strip_chars(),
        pl.col("valor")
        .cast(pl.String)
        .str.replace(",", ".", literal=True)
        .cast(pl.Float64, strict=False)
        .fill_null(0.0)
        .alias("valor"),
    ]
    for name in ("unidade", "referencia", "fonte"):
        if name in df.columns:
            exprs.append(pl.col(name).cast(pl.Utf8).fill_null("").alias(name))
        else:
            exprs.append(pl.lit("").alias(name))
    return df.select(exprs)


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


caged_raw, fin_raw, caged_comp_raw = load_data()
cnpj_resumo_raw, cnpj_mei_raw, cnpj_porte_raw, cnpj_cnae_raw, cnpj_join_raw, cnpj_meis_raw, cnpj_muni_fonte_raw = load_cnpj_botucatu()
comex_meta_raw, comex_mensal_raw, comex_top_exp_raw, comex_top_imp_raw = load_comex_botucatu()
comex_sh4_cnae_map = load_comex_sh4_cnae_map()
has_cnpj_export = not cnpj_resumo_raw.is_empty()
has_comex = not comex_mensal_raw.is_empty()
comex_meta_kv: dict[str, str] = (
    {str(r.get("chave", "")): str(r.get("valor", "")) for r in comex_meta_raw.to_dicts()} if not comex_meta_raw.is_empty() else {}
)
if caged_raw.is_empty() and fin_raw.is_empty() and caged_comp_raw.is_empty() and not has_cnpj_export and not has_comex:
    st.warning(
        "⚠️ Nenhum dataset encontrado (CAGED, finanças, comparativo, CNPJ/MEI ou Comex). Verifique os CSV na raiz ou em `data/`."
    )
    st.stop()

caged = normalize_caged(caged_raw)
fin = normalize_fin(fin_raw)
censo_ind_raw = load_censo_botucatu()
caged_comp = (
    caged_comp_raw.with_columns(
        [
            pl.col("ano_referencia").cast(pl.Int64),
            pl.col("mes_referencia").cast(pl.Int64),
            pl.col("Municipio").cast(pl.String),
            pl.col("Saldo").cast(pl.Float64).fill_null(0.0),
        ]
    )
    if not caged_comp_raw.is_empty()
    else caged_comp_raw
)

st.title("CAGED e finanças municipais")

menu_l, menu_r = st.columns([9, 1])
with menu_l:
    with st.popover("Filtros e período"):
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
        cidades_base = ["Botucatu", "Salto", "Jaú", "Sertãozinho", "Tatuí"]
        cidades_disponiveis = (
            sorted(caged_comp.select(pl.col("Municipio").unique()).to_series().drop_nulls().to_list())
            if not caged_comp.is_empty()
            else cidades_base
        )
        cidades_default = [c for c in cidades_base if c in cidades_disponiveis] or cidades_disponiveis
        cidades_selecionadas = st.multiselect(
            "Municípios (Comparativo Saldo CAGED)",
            options=cidades_disponiveis,
            default=cidades_default,
        )
with menu_r:
    st.empty()

st.caption(
    "Popover **Filtros e período**: aplica-se ao **Painel municipal** (CAGED e comparativo), à **aba Censo** "
    "(trecho Prefeitura — mesma competência mês/ano) e às seções que leem esse recorte. "
    "A aba **Balança comercial** usa só a série Comex do CSV."
)
st.caption(
    "Em telas pequenas, a página rola com prioridade; o menu de ferramentas dos gráficos fica discreto (canto, ao passar o dedo)."
)

nav_painel, nav_censo, nav_comex = st.tabs(["Painel municipal", "Censo / Finanças comparadas", "Balança comercial (Comex)"])

with nav_painel:
    st.caption(f"Painel — recorte selecionado: **{MESES[month]}/{ano}**.")
    if not caged.is_empty():
        st.markdown("## Emprego formal (CAGED)")
        st.caption("Admissões, desligamentos, saldo e estoque — com evolução em 12 meses e comparativo entre municípios.")
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

        st.markdown("### Evolução mensal — admissões e desligamentos")
        st.caption("Últimos 12 meses no período selecionado; valores no hover. Exporte a série em CSV abaixo.")
        fig_line = go.Figure()
        fig_line.add_trace(
            go.Scatter(
                x=monthly_pd["Mês Curto"],
                y=monthly_pd["Admissões"],
                mode="lines+markers+text",
                name="Admissões",
                line=dict(color="#2563eb", width=3),
                customdata=monthly_pd[["Mês", "Admissões_BR"]].values,
                hovertemplate="%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
                text=monthly_pd["Admissões_BR"],
                textposition="top center",
                textfont=dict(size=10),
            )
        )
        fig_line.add_trace(
            go.Scatter(
                x=monthly_pd["Mês Curto"],
                y=monthly_pd["Desligamentos"],
                mode="lines+markers+text",
                name="Desligamentos",
                line=dict(color="#1e3a8a", width=3),
                customdata=monthly_pd[["Mês", "Desligamentos_BR"]].values,
                hovertemplate="%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
                text=monthly_pd["Desligamentos_BR"],
                textposition="bottom center",
                textfont=dict(size=10),
            )
        )
        fig_line.update_layout(title="Admissões x Desligamentos", legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
        fig_line.update_xaxes(nticks=6)
        aplicar_layout_clean(fig_line, unified_hover=True)
        plotly_mobile_friendly(fig_line, key="pl_caged_line")
        st.download_button(
            "Baixar CSV — admissões e desligamentos (mensal)",
            data=csv_bytes_from_pandas(monthly_pd[["Mês", "Admissões", "Desligamentos"]]),
            file_name="caged_admissoes_desligamentos.csv",
            mime="text/csv",
            key="dl_caged_line",
            width="stretch",
        )

        fig_bar = px.bar(monthly_pd, x="Mês Curto", y="Saldo", title="Evolução do Saldo Mensal", text="Saldo_BR")
        fig_bar.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        fig_bar.update_traces(
            customdata=monthly_pd[["Mês", "Saldo_BR"]].values,
            hovertemplate="%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
            textposition="outside",
            cliponaxis=False,
        )
        aplicar_layout_clean(fig_bar)
        plotly_mobile_friendly(fig_bar, key="pl_caged_bar")
        st.download_button(
            "Baixar CSV — saldo mensal (CAGED)",
            data=csv_bytes_from_pandas(monthly_pd[["Mês", "Saldo"]]),
            file_name="caged_evolucao_saldo.csv",
            mime="text/csv",
            key="dl_caged_bar",
            width="stretch",
        )

        if not caged_comp.is_empty():
            st.markdown("### Comparativo de saldo entre municípios")
            st.caption(
                "Saldo mensal = admissões − desligamentos (CAGED), por município do **recorte** do pipeline. "
                "No gráfico do Plotly, evite o modo **“Compare data on hover”** (ícone de alinhamento no canto do gráfico): "
                "ele repete o mesmo valor para todas as linhas no eixo X. Aqui o botão foi removido da barra de ferramentas."
            )
            comp_12m = caged_comp.with_columns((pl.col("ano_referencia") * 12 + pl.col("mes_referencia")).alias("ord_mes"))
            comp_12m = comp_12m.filter((pl.col("ord_mes") >= ord_atual - 11) & (pl.col("ord_mes") <= ord_atual))
            if cidades_selecionadas:
                comp_12m = comp_12m.filter(pl.col("Municipio").is_in(cidades_selecionadas))
            comp_12m = comp_12m.with_columns(pl.col("Municipio").cast(pl.String).str.strip_chars())
            comp_12m = comp_12m.with_columns(
                pl.concat_str(
                    [
                        pl.col("mes_referencia").replace_strict(MESES),
                        pl.lit("/"),
                        pl.col("ano_referencia").cast(pl.String),
                    ]
                ).alias("Mês")
            )
            comp_12m = (
                comp_12m.group_by(["ord_mes", "ano_referencia", "mes_referencia", "Municipio", "Mês"])
                .agg(pl.col("Saldo").sum().alias("Saldo"))
                .sort(["ord_mes", "Municipio"])
            )
            comp_pd = comp_12m.to_pandas()
            if not comp_pd.empty:
                comp_pd["Saldo"] = comp_pd["Saldo"].astype("float64")
                tick_vals = sorted(comp_pd["ord_mes"].unique().tolist())
                ord_to_lbl = comp_pd.drop_duplicates(subset=["ord_mes"]).set_index("ord_mes")["Mês"].to_dict()
                tick_lbl = [str(ord_to_lbl.get(int(ov), ov)) for ov in tick_vals]
                fig_comp = go.Figure()
                palette = ["#636efa", "#ef553b", "#00cc96", "#ab63fa", "#ffa15a", "#19d3f3", "#ff6692", "#b6e880"]
                for i, mun in enumerate(sorted(comp_pd["Municipio"].astype(str).unique())):
                    sub = comp_pd[comp_pd["Municipio"].astype(str) == str(mun)].sort_values("ord_mes").copy()
                    xs = sub["ord_mes"].astype(int).tolist()
                    ys = sub["Saldo"].astype(float).tolist()
                    ms = sub["Mês"].astype(str).tolist()
                    trace_color = palette[i % len(palette)]
                    fig_comp.add_trace(
                        go.Scatter(
                            x=xs,
                            y=ys,
                            mode="lines+markers",
                            name=str(mun),
                            line=dict(width=2.5, color=trace_color),
                            marker=dict(size=8, color=trace_color, line=dict(width=1, color="rgba(255,255,255,0.6)")),
                            cliponaxis=False,
                            customdata=ms,
                            hovertemplate="<b>%{fullData.name}</b><br>%{customdata}<br>Saldo: %{y:.0f}<extra></extra>",
                        )
                    )
                fig_comp.update_layout(
                    title="Evolução de Saldo por Município (12 meses)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    xaxis=dict(
                        tickmode="array",
                        tickvals=tick_vals,
                        ticktext=tick_lbl,
                        title="Mês",
                    ),
                    yaxis=dict(title="Saldo (admissões − desligamentos)"),
                    hovermode="closest",
                )
                fig_comp.update_xaxes(nticks=min(12, len(tick_vals)))
                aplicar_layout_clean(fig_comp, unified_hover=False)
                plotly_mobile_friendly(fig_comp, key="pl_caged_comp")
                st.download_button(
                    "Baixar CSV — saldo por município e mês",
                    data=csv_bytes_from_pandas(comp_pd[["ano_referencia", "mes_referencia", "Municipio", "Saldo"]]),
                    file_name="caged_comparativo_municipios.csv",
                    mime="text/csv",
                    key="dl_caged_comp",
                    width="stretch",
                )
            else:
                st.info("Sem dados suficientes para o comparativo de municípios no período selecionado.")

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

        st.markdown("### Saldo por atividade econômica")
        st.caption("Dez atividades com maior saldo no mês filtrado (CNAE agregado).")
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
        fig_hbar.update_yaxes(categoryorder="total ascending")
        aplicar_layout_clean(fig_hbar)
        plotly_mobile_friendly(fig_hbar, key="pl_caged_hbar")
        st.download_button(
            "Baixar CSV — saldo por atividade",
            data=csv_bytes_from_pandas(saldo_atividade_pd),
            file_name="caged_saldo_por_atividade.csv",
            mime="text/csv",
            key="dl_caged_hbar",
            width="stretch",
        )

        st.markdown(f"### Ranking CNAE — subclasses ({MESES[month]}/{ano})")
        st.caption("Cinco maiores e cinco menores saldos no mês; tabelas com exportação única abaixo.")
        a1, a2 = st.columns(2)
        with a1:
            st.markdown("### Maiores Saldos")
            st.dataframe(
                top_maiores.select(["CNAE 2.0 Subclasse", "Saldo", "Admissões", "Desligamentos", "% Impacto"]).to_pandas(),
                width="stretch",
                hide_index=True,
                height=220,
            )
        with a2:
            st.markdown("### Menores Saldos")
            st.dataframe(
                top_menores.select(["CNAE 2.0 Subclasse", "Saldo", "Admissões", "Desligamentos", "% Impacto"]).to_pandas(),
                width="stretch",
                hide_index=True,
                height=220,
            )
        top_m_export = pl.concat(
            [
                top_maiores.select(["CNAE 2.0 Subclasse", "Saldo", "Admissões", "Desligamentos", "% Impacto"]).with_columns(
                    pl.lit("Entre os 5 maiores saldos").alias("Grupo")
                ),
                top_menores.select(["CNAE 2.0 Subclasse", "Saldo", "Admissões", "Desligamentos", "% Impacto"]).with_columns(
                    pl.lit("Entre os 5 menores saldos").alias("Grupo")
                ),
            ]
        )
        st.download_button(
            "Baixar CSV — ranking CNAE (maiores e menores)",
            data=csv_bytes_from_pandas(top_m_export.to_pandas()),
            file_name="caged_ranking_cnae_top5.csv",
            mime="text/csv",
            key="dl_caged_cnae_rank",
            width="stretch",
        )

    if has_cnpj_export:
        st.divider()
        st.header("Cadastro CNPJ e MEI (Botucatu)")
        st.caption(
            "Empresas com ao menos um estabelecimento no município (IBGE 3507506). "
            "MEI: opção pelo Simples sem data de exclusão; ativo/inativo conforme situação cadastral do estabelecimento representativo (matriz no município, se houver). "
            "Fonte: dados abertos da Receita Federal em `https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj/{{AAAA-MM}}/` "
            "(Estabelecimentos + Empresas + Simples; `Municipios.zip` só para o código interno do município no filtro)."
        )
        rs = cnpj_resumo_raw.to_dicts()[0]
        fu = str(rs.get("fonte_url", "") or "")
        fu_disp = (fu[:80] + "…") if len(fu) > 80 else fu
        st.caption(f"Referência da base: **{rs.get('ref_data_extracao', '')}** · Arquivo-fonte: `{fu_disp}`")

        tot_e = float(rs.get("total_empresas", 0) or 0)
        mei_any = float(rs.get("mei_opcao_sem_exclusao", 0) or 0)
        if tot_e > 0 and mei_any == 0:
            st.warning(
                "MEI zerado com empresas encontradas: em geral a pasta de dados (`CNPJ_DADOS_ABERTOS_REF` / `CNPJ_BASE_URL`) "
                "não bate com o layout do `Simples.zip` (colunas) ou o mês ainda não foi publicado no portal da RFB. "
                "Ajuste a competência e rode de novo o pipeline com `PIPELINE_INCLUDE_CNPJ=1`."
            )

        tab_vis, tab_join = st.tabs(
            ["Visão geral (município)", "Base JOIN (por empresa)"]
        )

        with tab_vis:
            with st.expander("Como o município alimenta o filtro"):
                st.markdown(
                    "O **município** usado na extração vem da coluna `municipio` dos arquivos **Estabelecimentos*.zip**. "
                    "Esse valor costuma ser o **código interno da RFB** (tabela **Municipios.zip**), não sempre o IBGE de 7 dígitos. "
                    "Buscamos *Botucatu* em `Municipios.zip` e montamos o conjunto de códigos aceitos no filtro."
                )
                if not cnpj_muni_fonte_raw.is_empty():
                    st.dataframe(cnpj_muni_fonte_raw, width="stretch", hide_index=True)
                    st.download_button(
                        "Baixar CSV — rastreio município / join",
                        data=csv_bytes_from_pandas(cnpj_muni_fonte_raw.to_pandas()),
                        file_name="cnpj_botucatu_municipio_fonte.csv",
                        mime="text/csv",
                        key="dl_cnpj_muni_fonte",
                        width="stretch",
                    )
                else:
                    st.info("Arquivo `cnpj_botucatu_municipio_fonte.csv` não encontrado (rode o ETL CNPJ atualizado).")

            with st.expander("Glossário rápido"):
                st.markdown(
                    """
- **Empresa (raiz)**: um CNPJ básico (8 dígitos) pode ter vários estabelecimentos; contamos uma vez se qualquer unidade estiver em Botucatu.
- **Estabelecimentos**: unidades locais (matriz/filial) com endereço no município.
- **MEI ativo**: opção pelo MEI vigente (`Simples`) **e** situação cadastral **ativa** no estabelecimento usado como referência.
- **MEI inativos (CNPJ)**: ainda com opção MEI no Simples, mas situação cadastral diferente de ativa (ex.: baixada/suspensa).
- **Aberturas / exclusões MEI (mensal)**: datas de opção e de exclusão do MEI no `Simples` (não confundir com abertura de empresa no município).
"""
                )

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Empresas (raiz CNPJ)", br_int(float(rs.get("total_empresas", 0) or 0)))
            m2.metric("Estabelecimentos no município", br_int(float(rs.get("total_estabelecimentos", 0) or 0)))
            m3.metric("MEI ativos", br_int(float(rs.get("mei_ativos", 0) or 0)))
            m4.metric("MEI inativos (CNPJ)", br_int(float(rs.get("mei_inativos_cnpj", 0) or 0)))
            m5.metric("MEI no Simples (sem exclusão)", br_int(float(rs.get("mei_opcao_sem_exclusao", 0) or 0)))

            if not cnpj_mei_raw.is_empty():
                st.markdown("### Movimento MEI (opção e exclusão no Simples)")
                mei_pd = cnpj_mei_raw.to_pandas()
                mei_pd = mei_pd.tail(36)
                fig_mei = go.Figure()
                fig_mei.add_trace(
                    go.Bar(x=mei_pd["ano_mes"], y=mei_pd["aberturas_mei"], name="Opção pelo MEI", marker_color="#2563eb")
                )
                fig_mei.add_trace(
                    go.Bar(x=mei_pd["ano_mes"], y=mei_pd["exclusoes_mei"], name="Exclusão do MEI", marker_color="#94a3b8")
                )
                fig_mei.update_layout(barmode="group", title="Mensal — últimos períodos com registro")
                aplicar_layout_clean(fig_mei)
                plotly_mobile_friendly(fig_mei, key="pl_cnpj_mei_mensal")
                st.download_button(
                    "Baixar CSV — movimento MEI mensal",
                    data=csv_bytes_from_pandas(mei_pd),
                    file_name="cnpj_botucatu_mei_mensal.csv",
                    mime="text/csv",
                    key="dl_cnpj_mei",
                    width="stretch",
                )
            else:
                st.info("Sem série mensal de MEI (arquivo vazio ou não gerado).")

            if not cnpj_porte_raw.is_empty():
                st.markdown("### Participação por tipo (porte / MEI)")
                st.caption("Percentual sobre o total de empresas (raiz) com estabelecimento em Botucatu.")
                pp = cnpj_porte_raw.to_pandas()
                pp["pct_label"] = pp["percentual"].map(lambda x: f"{float(x):.1f}%".replace(".", ","))
                fig_pie = px.bar(
                    pp.sort_values("quantidade", ascending=True),
                    x="quantidade",
                    y="tipo_empresa",
                    orientation="h",
                    text="pct_label",
                    title="Quantidade e % do total municipal",
                )
                fig_pie.update_traces(textposition="outside")
                aplicar_layout_clean(fig_pie)
                plotly_mobile_friendly(fig_pie, key="pl_cnpj_porte")
                st.download_button(
                    "Baixar CSV — distribuição por tipo",
                    data=csv_bytes_from_pandas(pp.drop(columns=["pct_label"], errors="ignore")),
                    file_name="cnpj_botucatu_porte_pct.csv",
                    mime="text/csv",
                    key="dl_cnpj_porte",
                    width="stretch",
                )

            if not cnpj_cnae_raw.is_empty():
                st.markdown("### CNAE (divisão) por tipo de empresa")
                st.caption(
                    "Para cada tipo (MEI, EPP, etc.), mostramos onde a massa se concentra na classificação CNAE 2.0 (divisão)."
                )
                tipos = sorted(cnpj_cnae_raw["tipo_empresa"].unique().to_list())
                tipo_sel = st.selectbox("Tipo de empresa", tipos, index=0, key="cnpj_tipo_cnae")
                sub = cnpj_cnae_raw.filter(pl.col("tipo_empresa") == tipo_sel).head(15)
                if not sub.is_empty():
                    cnae_pd = sub.to_pandas()
                    cnae_pd["pct_txt"] = cnae_pd["percentual_no_tipo"].map(lambda x: f"{float(x):.1f}%".replace(".", ","))
                    fig_c = px.bar(
                        cnae_pd,
                        x="quantidade",
                        y="divisao_descricao",
                        orientation="h",
                        text="pct_txt",
                        title=f"Top divisões — {tipo_sel} (% dentro do tipo)",
                        labels={"divisao_descricao": "Divisão CNAE", "quantidade": "Empresas"},
                    )
                    fig_c.update_traces(texttemplate="%{text}", textposition="outside")
                    aplicar_layout_clean(fig_c)
                    plotly_mobile_friendly(fig_c, key="pl_cnpj_cnae")
                st.download_button(
                    "Baixar CSV — CNAE × tipo (completo)",
                    data=csv_bytes_from_pandas(cnpj_cnae_raw.to_pandas()),
                    file_name="cnpj_botucatu_cnae_x_tipo.csv",
                    mime="text/csv",
                    key="dl_cnpj_cnae",
                    width="stretch",
                )

            st.download_button(
                "Baixar CSV — resumo CNPJ/MEI (metadados + totais)",
                data=csv_bytes_from_pandas(cnpj_resumo_raw.to_pandas()),
                file_name="cnpj_botucatu_resumo.csv",
                mime="text/csv",
                key="dl_cnpj_resumo",
                width="stretch",
            )

        with tab_join:
            st.markdown("### Join Estabelecimentos + Empresas + Simples (uma linha por empresa)")
            st.caption(
                "Representante: estabelecimento no município escolhido por `cnpj_basico` (prioriza matriz). "
                "Inclui porte, CNAE, situação cadastral, quantidade de estabelecimentos no município e flags MEI."
            )
            if not cnpj_join_raw.is_empty():
                njoin = cnpj_join_raw.height
                prev_n = min(2000, njoin)
                st.caption(f"Pré-visualização: **{prev_n}** de **{njoin}** linhas (o CSV completo está no botão de download).")
                st.dataframe(cnpj_join_raw.head(prev_n), width="stretch", height=420)
                st.download_button(
                    "Baixar CSV — join por empresa (`cnpj_botucatu_join_empresas.csv`)",
                    data=csv_bytes_from_pandas(cnpj_join_raw.to_pandas()),
                    file_name="cnpj_botucatu_join_empresas.csv",
                    mime="text/csv",
                    key="dl_cnpj_join",
                    width="stretch",
                )
            else:
                st.info("Sem arquivo de join (`cnpj_botucatu_join_empresas.csv`). Rode o ETL CNPJ atualizado com `PIPELINE_INCLUDE_CNPJ=1`.")


with nav_censo:
    st.header("Censo e finanças comparadas")
    st.caption(
        f"Recorte do popover (Prefeitura): **{MESES[month]}/{ano}**. "
        "Separamos o que é **conta pública** (Siconfi) do que é **população/território** (Censo/IBGE via CSV opcional)."
    )

    st.subheader("Prefeitura — tesouraria, caixa e aplicações contábeis (Siconfi)")
    st.caption(
        "Valores consolidados do **ente municipal** (Prefeitura de Botucatu). "
        "Não representam poupança, CDB ou investimentos das famílias."
    )
    vf, vrf, vtot = sum_prefeitura_fundos_e_renda_fixa(fin, month, ano)
    pf1, pf2, pf3 = st.columns(3)
    pf1.metric(
        "Fundos de investimento (conta pública)",
        format_brl_full(vf) if vf == vf else "—",
        help=f"Conta Siconfi {COD_SICONFI_FUNDOS_PREFEITURA} — saldo no mês selecionado.",
    )
    pf2.metric(
        "Aplicações em renda fixa (conta pública)",
        format_brl_full(vrf) if vrf == vrf else "—",
        help=f"Conta Siconfi {COD_SICONFI_RENDA_FIXA_PREFEITURA} — saldo no mês selecionado.",
    )
    pf3.metric(
        "Soma fundos + renda fixa (Prefeitura)",
        format_brl_full(vtot) if vtot == vtot else "—",
        help="Indicador derivado das duas contas acima (tesouraria).",
    )

    st.markdown("#### Visão geral de liquidez (todas as rubricas mapeadas)")
    st.caption(
        "Liquidez municipal (valores em reais). Filtre por instituição no seletor abaixo do gráfico de barras "
        "(em telas largas, ele fica à direita da evolução)."
    )

    try:
        if fin.is_empty():
            st.warning("Base financeira indisponível para análise de liquidez.")
        else:
            instituicoes_disponiveis = (
                ["Todas"]
                + sorted(fin.select(pl.col("Instituição Financeira").unique()).to_series().drop_nulls().to_list())
            )
            instituicao_filtro = st.session_state.get("instituicao_filtro", "Todas")
            if instituicao_filtro not in instituicoes_disponiveis:
                instituicao_filtro = "Todas"

            kpis_fin, evolucao_12_meses, distribuicao_bancos = processar_kpis_financeiros(
                fin, month, ano, instituicao_filtro
            )
            k1, k2, k3 = st.columns([1.2, 1.2, 2.2])
            k1.metric("Total em Caixa/Aplicações", format_brl_full(kpis_fin["total_dinheiro"]))
            k2.metric("Total em Poupança", format_brl_full(kpis_fin["total_poupanca"]))
            k3.metric("Maior Concentração", kpis_fin["instituicao_top_1"])

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
                        text=evo_pd["Total BRL"],
                        mode="lines+markers+text",
                        textposition="top center",
                        textfont=dict(size=9),
                        cliponaxis=False,
                    )
                    fig_evo.update_xaxes(showgrid=False, nticks=6)
                    fig_evo.update_yaxes(showgrid=False)
                    aplicar_layout_clean(fig_evo, unified_hover=True)
                    st.markdown("#### Evolução do saldo (12 meses)")
                    plotly_mobile_friendly(fig_evo, key="pl_fin_evo")
                    st.download_button(
                        "Baixar CSV — evolução financeira (mensal)",
                        data=csv_bytes_from_pandas(evo_pd[["Ano", "Mes", "Total"]]),
                        file_name="financeiro_evolucao_12_meses.csv",
                        mime="text/csv",
                        key="dl_fin_evo",
                        width="stretch",
                    )

            with c2:
                if distribuicao_bancos.is_empty():
                    st.info("Sem dados para distribuição por instituição.")
                else:
                    dist_pd = distribuicao_bancos.to_pandas()
                    dist_pd["Total BRL"] = dist_pd["Total"].apply(lambda x: format_brl_full(float(x)))
                    dist_pd["Total Label"] = dist_pd["Total BRL"]
                    fig_dist = px.bar(
                        dist_pd,
                        x="Total",
                        y="Instituicao",
                        orientation="h",
                        title="Saldo por Instituição",
                        text="Total Label",
                        labels={"Total": "Total", "Instituicao": "Instituição"},
                    )
                    fig_dist.update_traces(
                        textposition="outside",
                        hovertemplate="%{y}<br>%{customdata}<extra></extra>",
                        customdata=dist_pd["Total BRL"],
                        cliponaxis=False,
                    )
                    fig_dist.update_layout(
                        yaxis=dict(categoryorder="total ascending", showgrid=False),
                        xaxis=dict(visible=False, showgrid=False),
                        margin=dict(r=110),
                    )
                    aplicar_layout_clean(fig_dist)
                    st.markdown("#### Saldo por instituição")
                    st.caption(
                        "O gráfico é só leitura no celular (não filtra ao tocar), para a rolagem da página fluir. "
                        "Use a lista abaixo para recalcular os indicadores."
                    )
                    plotly_mobile_friendly(fig_dist, key="pl_fin_dist")
                    st.selectbox(
                        "Filtrar indicadores por instituição",
                        instituicoes_disponiveis,
                        key="instituicao_filtro",
                        help="Apenas esta lista altera os totais acima; tocar nas barras não dispara ação.",
                    )
                    st.download_button(
                        "Baixar CSV — saldo por instituição",
                        data=csv_bytes_from_pandas(dist_pd[["Instituicao", "Total"]]),
                        file_name="financeiro_saldo_por_instituicao.csv",
                        mime="text/csv",
                        key="dl_fin_dist",
                        width="stretch",
                    )

            st.caption(
                "Fonte: dados financeiros municipais (Siconfi/ESTBAN-like) consolidados para Botucatu. "
                "Os dados podem apresentar defasagem de até 60 dias em relação ao mês corrente."
            )
    except Exception as exc:
        st.error(f"Não foi possível renderizar a Visão Financeira e Liquidez: {exc}")

    st.divider()
    st.subheader("População e território (Censo / IBGE)")
    st.caption(
        "Indicadores **demográficos e socioeconômicos** do município (ex.: população residente, domicílios, renda). "
        "Carregue `censo_botucatu_indicadores.csv` na raiz ou em `data/` (separador `;`, colunas: indicador, valor, "
        "unidade, referencia, fonte). **Patrimônio financeiro das famílias** (aplicações das pessoas) não consta do Censo "
        "como saldo bancário municipal; exija fonte explícita (ex. estudos setoriais) se for incluir no CSV."
    )
    if censo_ind_raw.is_empty():
        st.info(
            "Nenhum `censo_botucatu_indicadores.csv` encontrado. Exporte tabelas do [SIDRA/IBGE](https://sidra.ibge.gov.br/) "
            "para Botucatu e monte o CSV conforme o cabeçalho acima."
        )
    else:
        censo_view = censo_ind_raw.select(
            [
                pl.col("indicador").alias("Indicador"),
                pl.col("valor").alias("Valor"),
                pl.col("unidade").alias("Unidade"),
                pl.col("referencia").alias("Referência"),
                pl.col("fonte").alias("Fonte"),
            ]
        )
        st.dataframe(censo_view, width="stretch", height=min(520, 80 + censo_view.height * 36))
        st.download_button(
            "Baixar CSV — indicadores Censo (atual)",
            data=csv_bytes_from_pandas(censo_ind_raw.to_pandas()),
            file_name="censo_botucatu_indicadores.csv",
            mime="text/csv",
            key="dl_censo_ind",
            width="stretch",
        )


with nav_comex:
    st.header("Balança comercial (Comex Stat / MDIC)")
    st.caption(
        "Série mensal municipal (declarante), FOB em US$; R$ estimado = US$ × PTAX (BCB). "
        "Indicadores do topo = último mês da série (independente do filtro do Painel). "
        "[Comex Stat](https://comexstat.mdic.gov.br/)."
    )
    if not has_comex:
        st.info(
            "Nenhum `comex_botucatu_mensal.csv` na raiz ou em `data/`. Gere com `python comexstat_botucatu_etl.py` "
            "ou `PIPELINE_INCLUDE_COMEX=1` + `python pipeline_botucatu.py`. A API do MDIC pode responder **429**: "
            "aumente `COMEX_REQUEST_PAUSE_SEC` (padrão 12s) se precisar."
        )
    else:

        def _comex_sum(cm: pl.DataFrame, ord_m: int, fluxo: str, col: str) -> float:
            r = cm.filter((pl.col("ord_mes") == ord_m) & (pl.col("fluxo") == fluxo))
            if r.is_empty():
                return float("nan")
            return float(r.select(pl.col(col).sum()).item())

        def _comex_yoy_pct(cur: float, prev: float) -> str | None:
            if cur != cur or prev != prev or prev == 0:
                return None
            return format_pct((cur - prev) / prev * 100.0)

        cm0 = comex_mensal_raw.sort("ord_mes")
        max_ord = int(cm0.select(pl.max("ord_mes")).item())
        last_ym = cm0.filter(pl.col("ord_mes") == max_ord).select(["ano", "mes"]).unique()
        ano_lm = int(last_ym["ano"][0])
        mes_lm = int(last_ym["mes"][0])
        ref_lm = f"{mes_lm:02d}/{ano_lm}"
        prev_ord = max_ord - 12

        nota_emp = comex_meta_kv.get("nota_empresa", "")
        metod_brl = comex_meta_kv.get("metodologia_brl", "")
        if nota_emp.strip() or metod_brl.strip():
            with st.expander("Notas técnicas (empresa declarante e metodologia R$)", expanded=False):
                if nota_emp.strip():
                    st.markdown(nota_emp)
                if metod_brl.strip():
                    st.caption(metod_brl)

        exp_usd = _comex_sum(cm0, max_ord, "exportacao", "valor_usd_fob")
        imp_usd = _comex_sum(cm0, max_ord, "importacao", "valor_usd_fob")
        saldo_usd = exp_usd - imp_usd if exp_usd == exp_usd and imp_usd == imp_usd else float("nan")
        exp_brl = _comex_sum(cm0, max_ord, "exportacao", "valor_brl_estimado")
        imp_brl = _comex_sum(cm0, max_ord, "importacao", "valor_brl_estimado")
        saldo_brl = exp_brl - imp_brl if exp_brl == exp_brl and imp_brl == imp_brl else float("nan")
        rpt = cm0.filter((pl.col("ord_mes") == max_ord) & (pl.col("fluxo") == "exportacao"))
        ptax_lm = float(rpt.select(pl.col("ptax_media").mean()).item()) if not rpt.is_empty() else float("nan")

        exp_usd_y = _comex_sum(cm0, prev_ord, "exportacao", "valor_usd_fob")
        imp_usd_y = _comex_sum(cm0, prev_ord, "importacao", "valor_usd_fob")

        rk_ano = comex_meta_kv.get("ranking_sh4_ano", "—")
        upd = comex_meta_kv.get("comex_ultima_atualizacao", "—")
        st.caption(f"Referência na API: **{upd}** · ranking SH4: **{rk_ano}**.")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            f"Exportação (US$) — {ref_lm}",
            format_usd_milhoes(exp_usd),
            delta=_comex_yoy_pct(exp_usd, exp_usd_y),
            help="FOB exportação do município no mês. Variação vs mesmo mês do ano anterior (US$).",
        )
        m2.metric(
            f"Importação (US$) — {ref_lm}",
            format_usd_milhoes(imp_usd),
            delta=_comex_yoy_pct(imp_usd, imp_usd_y),
            help="FOB importação do município no mês. Variação vs mesmo mês do ano anterior (US$).",
        )
        m3.metric(
            "Saldo (US$)",
            format_usd_milhoes(saldo_usd),
            help="Exportação − importação no mês (US$).",
        )
        m4.metric(
            "PTAX média (mês)",
            f"R$ {ptax_lm:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".") if ptax_lm == ptax_lm else "—",
            help="Média mensal dólar venda (BCB), usada na conversão estimada para R$.",
        )

        m5, m6, m7 = st.columns(3)
        m5.metric("Exportação (R$ est.)", format_brl_full(exp_brl) if exp_brl == exp_brl else "—")
        m6.metric("Importação (R$ est.)", format_brl_full(imp_brl) if imp_brl == imp_brl else "—")
        m7.metric("Saldo (R$ est.)", format_brl_full(saldo_brl) if saldo_brl == saldo_brl else "—")

        exp_side = cm0.filter(pl.col("fluxo") == "exportacao").select(
            "ord_mes",
            "ano",
            "mes",
            pl.col("valor_usd_fob").alias("export_usd"),
            pl.col("valor_brl_estimado").alias("export_brl"),
            pl.col("ptax_media").alias("ptax"),
        )
        imp_side = cm0.filter(pl.col("fluxo") == "importacao").select(
            "ord_mes",
            pl.col("valor_usd_fob").alias("import_usd"),
            pl.col("valor_brl_estimado").alias("import_brl"),
        )
        chart_df = exp_side.join(imp_side, on="ord_mes", how="inner").sort("ord_mes")
        chart_df = chart_df.with_columns(
            (pl.col("ano").cast(pl.Utf8) + "-" + pl.col("mes").cast(pl.Utf8).str.zfill(2)).alias("periodo")
        )
        ch_pd = chart_df.to_pandas()

        def _fmt_usd_fob_cell(v: object) -> str:
            try:
                x = float(v)
            except (TypeError, ValueError):
                return "—"
            if x != x:
                return "—"
            return format_usd_fob_en(x)

        def _fmt_ptax_cell(v: object) -> str:
            try:
                x = float(v)
            except (TypeError, ValueError):
                return "—"
            if x != x:
                return "—"
            s = f"{x:.4f}".replace(".", ",")
            return f"PTAX média R$ {s} / US$"

        def _fmt_brl_cell(v: object) -> str:
            try:
                x = float(v)
            except (TypeError, ValueError):
                return "—"
            if x != x:
                return "—"
            return format_brl_full(x)

        ch_pd["ht_exp_usd"] = ch_pd["export_usd"].map(_fmt_usd_fob_cell)
        ch_pd["ht_imp_usd"] = ch_pd["import_usd"].map(_fmt_usd_fob_cell)
        ch_pd["ht_ptax"] = ch_pd["ptax"].map(_fmt_ptax_cell)
        ch_pd["ht_exp_brl"] = ch_pd["export_brl"].map(_fmt_brl_cell)
        ch_pd["ht_imp_brl"] = ch_pd["import_brl"].map(_fmt_brl_cell)

        fig_comex = go.Figure()
        fig_comex.add_trace(
            go.Scatter(
                x=ch_pd["periodo"],
                y=ch_pd["export_usd"],
                name="Exportação (US$)",
                mode="lines+markers",
                line=dict(width=2.2, color="#2563eb"),
                marker=dict(size=7, color="#2563eb"),
                text=ch_pd["ht_exp_usd"],
                hovertemplate="<b>%{fullData.name}</b><br>%{x}<br>%{text}<extra></extra>",
            )
        )
        fig_comex.add_trace(
            go.Scatter(
                x=ch_pd["periodo"],
                y=ch_pd["import_usd"],
                name="Importação (US$)",
                mode="lines+markers",
                line=dict(width=2.2, color="#ea580c"),
                marker=dict(size=7, color="#ea580c"),
                text=ch_pd["ht_imp_usd"],
                hovertemplate="<b>%{fullData.name}</b><br>%{x}<br>%{text}<extra></extra>",
            )
        )
        fig_comex.add_trace(
            go.Scatter(
                x=ch_pd["periodo"],
                y=ch_pd["ptax"],
                name="PTAX (R$/US$)",
                mode="lines",
                line=dict(width=2, dash="dot", color="#64748b"),
                text=ch_pd["ht_ptax"],
                yaxis="y2",
                hovertemplate="<b>%{fullData.name}</b><br>%{x}<br>%{text}<extra></extra>",
            )
        )
        fig_comex.update_layout(
            yaxis=dict(title="US$ (FOB)", side="left"),
            yaxis2=dict(title="PTAX", overlaying="y", side="right", showgrid=False),
            hovermode="closest",
        )
        aplicar_layout_clean(fig_comex, unified_hover=False)
        fig_comex.update_layout(
            title=dict(
                text="Histórico mensal — US$ (export/import) e PTAX (eixo direito)",
                x=0.02,
                xanchor="left",
                yanchor="top",
                y=1.0,
            ),
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.2,
                xanchor="center",
                x=0.5,
            ),
            margin=dict(l=16, r=56, t=56, b=100),
        )
        plotly_mobile_friendly(fig_comex, key="comex_usd_ptax")

        fig_brl = go.Figure()
        fig_brl.add_trace(
            go.Scatter(
                x=ch_pd["periodo"],
                y=ch_pd["export_brl"],
                name="Exportação (R$ est.)",
                mode="lines+markers",
                line=dict(width=2.2, color="#2563eb"),
                marker=dict(size=7, color="#2563eb"),
                text=ch_pd["ht_exp_brl"],
                hovertemplate="<b>%{fullData.name}</b><br>%{x}<br>%{text}<extra></extra>",
            )
        )
        fig_brl.add_trace(
            go.Scatter(
                x=ch_pd["periodo"],
                y=ch_pd["import_brl"],
                name="Importação (R$ est.)",
                mode="lines+markers",
                line=dict(width=2.2, color="#ea580c"),
                marker=dict(size=7, color="#ea580c"),
                text=ch_pd["ht_imp_brl"],
                hovertemplate="<b>%{fullData.name}</b><br>%{x}<br>%{text}<extra></extra>",
            )
        )
        fig_brl.update_layout(
            yaxis=dict(title="R$ (estimado)"),
            hovermode="closest",
        )
        aplicar_layout_clean(fig_brl, unified_hover=False)
        fig_brl.update_layout(
            title=dict(
                text="Histórico mensal — valores estimados em R$ (US$ × PTAX do mês)",
                x=0.02,
                xanchor="left",
                yanchor="top",
                y=1.0,
            ),
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.14,
                xanchor="center",
                x=0.5,
            ),
            margin=dict(l=16, r=56, t=56, b=92),
        )
        plotly_mobile_friendly(fig_brl, key="comex_brl")

        def _fmt_cell_usd_tbl(v: object) -> str:
            try:
                x = float(v)
            except (TypeError, ValueError):
                return "—"
            if x != x:
                return "—"
            return format_usd_fob_en(x)

        def _fmt_cell_brl_tbl(v: object) -> str:
            try:
                x = float(v)
            except (TypeError, ValueError):
                return "—"
            if x != x:
                return "—"
            return format_brl_full(x)

        anos_disp = sorted(cm0.select(pl.col("ano").unique())["ano"].to_list(), reverse=True)
        ano_tot = st.selectbox("Totais anuais (US$ e R$ estimado)", anos_disp, index=0, key="comex_ano_totais")
        by_y = (
            cm0.filter(pl.col("ano") == int(ano_tot))
            .group_by("fluxo")
            .agg(
                pl.col("valor_usd_fob").sum().alias("usd"),
                pl.col("valor_brl_estimado").sum().alias("brl"),
            )
            .sort("fluxo")
        )
        by_y_view = by_y.with_columns(
            [
                pl.when(pl.col("fluxo") == "exportacao")
                .then(pl.lit("Exportação"))
                .otherwise(pl.lit("Importação"))
                .alias("Fluxo"),
                pl.col("usd")
                .map_elements(_fmt_cell_usd_tbl, return_dtype=pl.Utf8)
                .alias("Total US$ (FOB)"),
                pl.col("brl")
                .map_elements(_fmt_cell_brl_tbl, return_dtype=pl.Utf8)
                .alias("Total R$ (estimado)"),
            ]
        ).select(["Fluxo", "Total US$ (FOB)", "Total R$ (estimado)"])
        st.dataframe(
            by_y_view,
            width="stretch",
            height=120,
        )

        st.subheader("Cruzamento SH4 × CNAE (Botucatu)")
        st.caption(
            "Lista aproximada: prefixos de CNAE fiscal sugeridos para cada SH4 em `data/comex_sh4_cnae_aproximacao.csv`. "
            "O Comex não associa produto a CNPJ."
        )
        sh4_labels = comex_sh4_select_labels(comex_top_exp_raw, comex_top_imp_raw, comex_sh4_cnae_map)
        if not sh4_labels:
            st.caption("Sem SH4 no ranking nem no catálogo de mapeamento.")
        else:
            sh4_pick = st.selectbox(
                "SH4",
                options=list(sh4_labels.keys()),
                format_func=lambda k: sh4_labels[k],
                key="comex_sh4_cnae_pick",
            )
            sh4z = str(sh4_pick).strip().zfill(4)
            submap = comex_sh4_cnae_map.filter(pl.col("sh4") == sh4z)
            if submap.is_empty():
                st.info("Este SH4 não está no CSV de mapeamento; edite `data/comex_sh4_cnae_aproximacao.csv`.")
            else:
                emp_sh4, notas_map = empresas_botucatu_por_sh4(cnpj_join_raw, comex_sh4_cnae_map, sh4_pick)
                if cnpj_join_raw.is_empty():
                    st.caption("Gere `cnpj_botucatu_join_empresas.csv` com o ETL de CNPJ para listar empresas.")
                elif emp_sh4.is_empty():
                    st.caption("Nenhuma empresa no município com CNAE fiscal principal compatível com os prefixos mapeados.")
                else:
                    st.caption(f"{emp_sh4.height} registro(s) no join municipal.")
                    notas_txt = [str(n).strip() for n in notas_map if str(n).strip()]
                    if notas_txt:
                        with st.expander("Notas do mapeamento SH4 → CNAE", expanded=False):
                            for n in notas_txt:
                                st.text(n)
                    st.dataframe(emp_sh4, width="stretch", height=360)
                    st.download_button(
                        "CSV — empresas (aproximação por SH4)",
                        data=csv_bytes_from_pandas(emp_sh4.to_pandas()),
                        file_name=f"comex_sh4_{sh4z}_empresas_botucatu_aprox.csv",
                        mime="text/csv",
                        key="dl_comex_sh4_cnae",
                        width="stretch",
                    )

        c1, c2 = st.columns(2)
        with c1:
            st.subheader(f"Top 10 produtos (SH4) — exportação ({rk_ano})")
            if not comex_top_exp_raw.is_empty():
                st.dataframe(
                    comex_top_exp_raw.with_columns(
                        [
                            pl.col("valor_usd_fob")
                            .map_elements(_fmt_cell_usd_tbl, return_dtype=pl.Utf8)
                            .alias("US$ FOB"),
                            pl.col("valor_brl_estimado")
                            .map_elements(_fmt_cell_brl_tbl, return_dtype=pl.Utf8)
                            .alias("R$ (est.)"),
                        ]
                    ).select(
                        [
                            pl.col("rank").alias("Pos."),
                            pl.col("sh4").alias("SH4"),
                            pl.col("descricao").alias("Descrição"),
                            pl.col("US$ FOB"),
                            pl.col("R$ (est.)"),
                        ]
                    ),
                    width="stretch",
                    height=420,
                )
            else:
                st.caption("Sem dados de ranking exportação.")
        with c2:
            st.subheader(f"Top 10 produtos (SH4) — importação ({rk_ano})")
            if not comex_top_imp_raw.is_empty():
                st.dataframe(
                    comex_top_imp_raw.with_columns(
                        [
                            pl.col("valor_usd_fob")
                            .map_elements(_fmt_cell_usd_tbl, return_dtype=pl.Utf8)
                            .alias("US$ FOB"),
                            pl.col("valor_brl_estimado")
                            .map_elements(_fmt_cell_brl_tbl, return_dtype=pl.Utf8)
                            .alias("R$ (est.)"),
                        ]
                    ).select(
                        [
                            pl.col("rank").alias("Pos."),
                            pl.col("sh4").alias("SH4"),
                            pl.col("descricao").alias("Descrição"),
                            pl.col("US$ FOB"),
                            pl.col("R$ (est.)"),
                        ]
                    ),
                    width="stretch",
                    height=420,
                )
            else:
                st.caption("Sem dados de ranking importação.")

        d1, d2, d3, d4 = st.columns(4)
        d1.download_button(
            "CSV — série mensal",
            data=csv_bytes_from_pandas(comex_mensal_raw.to_pandas()),
            file_name="comex_botucatu_mensal.csv",
            mime="text/csv",
            key="dl_comex_mensal",
            width="stretch",
        )
        d2.download_button(
            "CSV — top SH4 export",
            data=csv_bytes_from_pandas(comex_top_exp_raw.to_pandas()),
            file_name="comex_botucatu_top_sh4_export.csv",
            mime="text/csv",
            key="dl_comex_sh4_ex",
            width="stretch",
        )
        d3.download_button(
            "CSV — top SH4 import",
            data=csv_bytes_from_pandas(comex_top_imp_raw.to_pandas()),
            file_name="comex_botucatu_top_sh4_import.csv",
            mime="text/csv",
            key="dl_comex_sh4_im",
            width="stretch",
        )
        d4.download_button(
            "CSV — metadados",
            data=csv_bytes_from_pandas(comex_meta_raw.to_pandas()),
            file_name="comex_botucatu_meta.csv",
            mime="text/csv",
            key="dl_comex_meta",
            width="stretch",
        )


