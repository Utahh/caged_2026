from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


st.set_page_config(
    page_title="Dados gerais de março de 2026",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        .block-container {padding-top: 0.75rem; padding-bottom: 1rem; max-width: 1400px;}
        .kpi-label {color: #6b7280; font-size: 0.95rem;}
        .kpi-value {color: #1a237e; font-size: 2.4rem; font-weight: 700; line-height: 1;}
        .stDataFrame thead tr th {font-size: 0.75rem !important; color: #6b7280 !important;}
        [data-testid="stMetricValue"] {font-size: 2rem;}
        [data-testid="stMetricLabel"] {font-size: 0.9rem;}
        @media (max-width: 1024px) {
            .block-container {padding-left: 0.8rem; padding-right: 0.8rem;}
        }
        @media (max-width: 768px) {
            .block-container {padding-top: 0.35rem; padding-left: 0.6rem; padding-right: 0.6rem;}
            h1 {font-size: 1.35rem !important; line-height: 1.25 !important;}
            h2, h3 {font-size: 1.05rem !important; line-height: 1.25 !important;}
            [data-testid="stMetricValue"] {font-size: 1.35rem !important;}
            [data-testid="stMetricLabel"] {font-size: 0.8rem !important;}
            .stDataFrame thead tr th {font-size: 0.68rem !important;}
        }
    </style>
    """,
    unsafe_allow_html=True,
)


MESES_LABEL = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}


def formatar_inteiro_br(valor: float) -> str:
    return f"{valor:,.0f}".replace(",", ".")


def formatar_moeda_br(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def variacao_percentual_mom(atual: float, anterior: float) -> float:
    if anterior == 0:
        return 0.0 if atual == 0 else 100.0
    return ((atual - anterior) / abs(anterior)) * 100


def normalizar_ibge_str(valor: object) -> str:
    texto = "".join(ch for ch in str(valor) if ch.isdigit())
    if len(texto) == 6:
        texto = f"{texto}0"
    return texto.zfill(7) if texto else ""


def carregar_csv_com_fallback(caminhos: list[Path], decimal: str | None = None) -> pd.DataFrame:
    caminho = next((p for p in caminhos if p.exists()), None)
    if not caminho:
        return pd.DataFrame()
    kwargs = {"sep": ";", "encoding": "utf-8-sig", "dtype": str}
    if decimal:
        kwargs["decimal"] = decimal
    return pd.read_csv(caminho, **kwargs)


@st.cache_data
def carregar_modelo_estrela():
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "data"

    fato_caged = carregar_csv_com_fallback(
        [
            base_dir / "relatorio_botucatu_q1_2026.csv",
            base_dir / "caged_botucatu_q1_2026.csv",
            data_dir / "relatorio_botucatu_q1_2026.csv",
            data_dir / "caged_botucatu_q1_2026.csv",
        ]
    )
    fato_fin = carregar_csv_com_fallback(
        [
            base_dir / "investimentos_botucatu_2026.csv",
            base_dir / "financas_botucatu_2026.csv",
            data_dir / "investimentos_botucatu_2026.csv",
            data_dir / "financas_botucatu_2026.csv",
        ],
        decimal=",",
    )

    dim_municipios = carregar_csv_com_fallback([base_dir / "dim_municipios.csv", data_dir / "dim_municipios.csv"])
    dim_cnae = carregar_csv_com_fallback([base_dir / "dim_cnae.csv", data_dir / "dim_cnae.csv"])
    dim_grande = carregar_csv_com_fallback([base_dir / "dim_grande_grupamento.csv", data_dir / "dim_grande_grupamento.csv"])

    return fato_caged, fato_fin, dim_municipios, dim_cnae, dim_grande


def preparar_caged(fato_caged: pd.DataFrame, dim_municipios: pd.DataFrame, dim_cnae: pd.DataFrame, dim_grande: pd.DataFrame) -> pd.DataFrame:
    if fato_caged.empty:
        return fato_caged

    df = fato_caged.copy()
    for col in ["mes_referencia", "admissao", "demissao", "saldomovimentacao", "estoque_anual_2026"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["subclasse"] = df.get("subclasse", "").astype(str).str.zfill(7)
    df["secao"] = df.get("secao", "").astype(str).str.strip()
    if "secao_descricao" not in df.columns:
        df["secao_descricao"] = "Não Identificado"
    df["secao_descricao"] = df["secao_descricao"].fillna("Não Identificado")

    # Se não houver dimensão municipal, mantém Botucatu como default do painel executivo
    df["ibge_municipio"] = "3507500"
    df["municipio"] = "Botucatu"
    df["uf"] = "SP"
    df["regiao"] = "Sudeste"
    if not dim_municipios.empty:
        dm = dim_municipios.copy()
        ibge_col = next((c for c in dm.columns if "ibge" in c.lower()), None)
        mun_col = next((c for c in dm.columns if "municip" in c.lower()), None)
        uf_col = next((c for c in dm.columns if c.lower() == "uf"), None)
        reg_col = next((c for c in dm.columns if "regiao" in c.lower()), None)
        if ibge_col and mun_col:
            dm["ibge_municipio"] = dm[ibge_col].map(normalizar_ibge_str)
            dm["municipio"] = dm[mun_col].astype(str)
            if uf_col:
                dm["uf"] = dm[uf_col].astype(str)
            if reg_col:
                dm["regiao"] = dm[reg_col].astype(str)
            dm = dm[["ibge_municipio", "municipio", "uf", "regiao"]].drop_duplicates()
            df = df.merge(dm, on="ibge_municipio", how="left", suffixes=("", "_dim"))
            for col in ["municipio", "uf", "regiao"]:
                dim_col = f"{col}_dim"
                if dim_col in df.columns:
                    df[col] = df[dim_col].fillna(df[col])
                    df = df.drop(columns=[dim_col])

    # Enriquecimento por dimensão CNAE quando disponível
    if not dim_cnae.empty:
        dc = dim_cnae.copy()
        sub_col = next((c for c in dc.columns if "subclasse" in c.lower()), None)
        if sub_col:
            dc["subclasse"] = dc[sub_col].astype(str).str.zfill(7)
            keep = ["subclasse"] + [c for c in dc.columns if c != sub_col]
            dc = dc[keep].drop_duplicates(subset=["subclasse"])
            df = df.merge(dc, on="subclasse", how="left", suffixes=("", "_dim"))

    # Grande grupamento por seção com fallback robusto
    if not dim_grande.empty:
        dg = dim_grande.copy()
        sec_col = next((c for c in dg.columns if "secao" in c.lower()), None)
        nome_col = next((c for c in dg.columns if "grande" in c.lower() or "grupamento" in c.lower()), None)
        if sec_col and nome_col:
            dg = dg[[sec_col, nome_col]].drop_duplicates()
            dg.columns = ["secao", "grande_grupamento"]
            dg["secao"] = dg["secao"].astype(str).str.strip()
            df = df.merge(dg, on="secao", how="left")

    mapa_grande = {
        "A": "Agropecuária",
        "B": "Indústria",
        "C": "Indústria",
        "D": "Indústria",
        "E": "Indústria",
        "F": "Construção",
        "G": "Comércio",
        "H": "Serviços",
        "I": "Serviços",
        "J": "Serviços",
        "K": "Serviços",
        "L": "Serviços",
        "M": "Serviços",
        "N": "Serviços",
        "O": "Serviços",
        "P": "Serviços",
        "Q": "Serviços",
        "R": "Serviços",
        "S": "Serviços",
        "T": "Serviços",
        "U": "Serviços",
    }
    if "grande_grupamento" not in df.columns:
        df["grande_grupamento"] = df["secao"].map(mapa_grande)
    df["grande_grupamento"] = df["grande_grupamento"].fillna(df["secao_descricao"]).fillna("Outros")

    for col in ["subclasse_descricao", "classe_descricao", "grupo_descricao", "divisao_descricao", "secao_descricao"]:
        if col in df.columns:
            df[col] = df[col].fillna("Não Identificado")

    return df


def preparar_financas(fato_fin: pd.DataFrame) -> pd.DataFrame:
    if fato_fin.empty:
        return fato_fin
    df = fato_fin.copy()
    for col in ["Mes", "Saldo_em_Reais"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["Codigo_Contabil"] = df.get("Codigo_Contabil", "").astype(str)
    df["Natureza"] = df.get("Natureza", "Não Identificado").astype(str)
    df["instituicao_financeira"] = (
        "Conta " + df["Codigo_Contabil"].str.strip() + " (" + df["Natureza"].str.strip() + ")"
    )
    return df


fato_caged, fato_fin, dim_municipios, dim_cnae, dim_grande = carregar_modelo_estrela()
df_caged = preparar_caged(fato_caged, dim_municipios, dim_cnae, dim_grande)
df_financas = preparar_financas(fato_fin)

st.title("📊 Dados gerais de março de 2026")
st.caption("Painel executivo de saúde econômica — Botucatu/SP")

if df_caged.empty and df_financas.empty:
    st.warning("⚠️ Nenhuma base foi encontrada. Verifique os CSVs de fato e dimensões.")
    st.stop()

# Filtros executivos
with st.sidebar:
    st.markdown("## Filtros")
    anos = [2026]
    ano_sel = st.selectbox("Ano", anos, index=0)
    meses = sorted(df_caged["mes_referencia"].dropna().astype(int).unique().tolist()) if not df_caged.empty else [1, 2, 3]
    mes_sel = st.selectbox("Mês", meses, index=meses.index(3) if 3 in meses else 0)
    regioes = sorted(df_caged["regiao"].fillna("Não Identificado").unique().tolist()) if not df_caged.empty else ["Sudeste"]
    regiao_sel = st.selectbox("Região", regioes, index=regioes.index("Sudeste") if "Sudeste" in regioes else 0)
    ufs = sorted(df_caged[df_caged["regiao"] == regiao_sel]["uf"].fillna("Não Identificado").unique().tolist()) if not df_caged.empty else ["SP"]
    uf_sel = st.selectbox("UF", ufs, index=ufs.index("SP") if "SP" in ufs else 0)
    munis = sorted(df_caged[(df_caged["regiao"] == regiao_sel) & (df_caged["uf"] == uf_sel)]["municipio"].fillna("Não Identificado").unique().tolist()) if not df_caged.empty else ["Botucatu"]
    municipio_sel = st.selectbox("Município", munis, index=munis.index("Botucatu") if "Botucatu" in munis else 0)


if not df_caged.empty:
    dff = df_caged[
        (df_caged["mes_referencia"].isin([1, 2, 3]))
        & (df_caged["regiao"] == regiao_sel)
        & (df_caged["uf"] == uf_sel)
        & (df_caged["municipio"] == municipio_sel)
    ].copy()
    df_mes = dff[dff["mes_referencia"] == mes_sel].copy()
    df_mes_ant = dff[dff["mes_referencia"] == max(1, mes_sel - 1)].copy()

    adm = df_mes["admissao"].sum()
    des = df_mes["demissao"].sum()
    saldo = df_mes["saldomovimentacao"].sum()
    estoque = (
        dff[["secao", "subclasse", "estoque_anual_2026"]].drop_duplicates()["estoque_anual_2026"].sum()
        if "estoque_anual_2026" in dff.columns
        else dff["saldomovimentacao"].sum()
    )
    adm_ant = df_mes_ant["admissao"].sum()
    des_ant = df_mes_ant["demissao"].sum()
    saldo_ant = df_mes_ant["saldomovimentacao"].sum()

    st.markdown("## Indicadores-chave")
    is_mobile = st.checkbox("Modo celular (layout compacto)", value=False)
    k1, k2 = st.columns(2)
    k3, k4 = st.columns(2)
    k1.metric("Admissões", formatar_inteiro_br(adm), f"{variacao_percentual_mom(adm, adm_ant):+.1f}%")
    k2.metric("Desligamentos", formatar_inteiro_br(des), f"{variacao_percentual_mom(des, des_ant):+.1f}%", delta_color="inverse")
    k3.metric("Saldo", formatar_inteiro_br(saldo), f"{variacao_percentual_mom(saldo, saldo_ant):+.1f}%")
    k4.metric("Estoque", formatar_inteiro_br(estoque))

    st.markdown("## Evolução")
    evol = dff.groupby("mes_referencia", as_index=False)[["admissao", "demissao", "saldomovimentacao"]].sum()
    evol["mes_label"] = evol["mes_referencia"].map(MESES_LABEL)

    fig_linhas = go.Figure()
    fig_linhas.add_trace(go.Scatter(x=evol["mes_label"], y=evol["admissao"], mode="lines+markers", name="Admissões", line=dict(color="#1a237e", width=3)))
    fig_linhas.add_trace(go.Scatter(x=evol["mes_label"], y=evol["demissao"], mode="lines+markers", name="Desligamentos", line=dict(color="#ef4444", width=3)))
    fig_linhas.update_layout(
        title="Evolução das Admissões e Desligamentos",
        plot_bgcolor="white",
        yaxis=dict(showgrid=True, gridcolor="rgba(107,114,128,0.2)", griddash="dot"),
        xaxis=dict(showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=10, r=10, t=60, b=20),
    )

    cores = ["#1a237e" if v >= 0 else "#ef4444" for v in evol["saldomovimentacao"]]
    fig_saldo = go.Figure(
        data=[
            go.Bar(
                x=evol["mes_label"],
                y=evol["saldomovimentacao"],
                marker_color=cores,
                text=[formatar_inteiro_br(v) for v in evol["saldomovimentacao"]],
                textposition="outside",
                name="Saldo",
            )
        ]
    )
    fig_saldo.update_layout(
        title="Evolução do Saldo por Competência",
        plot_bgcolor="white",
        yaxis=dict(zeroline=True, zerolinewidth=2, zerolinecolor="#6b7280"),
        xaxis=dict(showgrid=False),
        margin=dict(l=10, r=10, t=60, b=20),
    )

    if is_mobile:
        st.plotly_chart(fig_linhas, use_container_width=True)
        st.plotly_chart(fig_saldo, use_container_width=True)
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(fig_linhas, use_container_width=True)
        with c2:
            st.plotly_chart(fig_saldo, use_container_width=True)

    st.markdown("## Atividade Econômica")
    eco1, eco2 = st.columns([1.2, 1]) if not is_mobile else (st.container(), st.container())
    with eco1:
        saldo_grande = (
            df_mes.groupby("grande_grupamento", as_index=False)[["admissao", "demissao", "saldomovimentacao"]]
            .sum()
            .sort_values("saldomovimentacao", ascending=False)
        )
        fig_gg = px.bar(
            saldo_grande,
            x="saldomovimentacao",
            y="grande_grupamento",
            orientation="h",
            color="saldomovimentacao",
            color_continuous_scale=["#ef4444", "#e5e7eb", "#1a237e"],
            labels={"saldomovimentacao": "Saldo", "grande_grupamento": "Grande Grupamento"},
            title="Saldo por Grande Grupamento de Atividade Econômica",
        )
        st.plotly_chart(fig_gg, use_container_width=True)

    with eco2:
        det = saldo_grande.copy()
        total_saldo = max(1, abs(det["saldomovimentacao"].sum()))
        det["Tempo de Emprego"] = "N/D"
        det["Estoque Mensal"] = det["saldomovimentacao"].cumsum()
        det["Valor Relativo"] = (det["saldomovimentacao"] / total_saldo).round(4)
        det = det.rename(
            columns={
                "grande_grupamento": "Grande Grupamento",
                "admissao": "Admitidos",
                "demissao": "Desligados",
                "saldomovimentacao": "Saldo",
            }
        )
        st.dataframe(
            det[
                [
                    "Grande Grupamento",
                    "Admitidos",
                    "Desligados",
                    "Saldo",
                    "Tempo de Emprego",
                    "Estoque Mensal",
                    "Valor Relativo",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("### Top 5 maiores e menores saldos (Março)")
    top = (
        df_mes.groupby(df_mes.get("secao_descricao", df_mes["secao"]), as_index=False)["saldomovimentacao"]
        .sum()
        .rename(columns={"secao_descricao": "Atividade", "saldomovimentacao": "Saldo"})
        .sort_values("Saldo", ascending=False)
    )
    t1, t2 = st.columns(2)
    with t1:
        st.dataframe(top.head(5), hide_index=True, use_container_width=True)
    with t2:
        st.dataframe(top.sort_values("Saldo", ascending=True).head(5), hide_index=True, use_container_width=True)

st.markdown("## 💰 Módulo Financeiro Botucatu 2026")
if not df_financas.empty:
    ff = df_financas[df_financas["Mes"].isin([1, 2, 3])].copy()
    if not ff.empty:
        mes_recente = int(ff["Mes"].max())
        orcamento_total = ff[ff["Mes"] == mes_recente]["Saldo_em_Reais"].sum()
        total_investido = ff["Saldo_em_Reais"].sum()

        f1, f2 = st.columns(2)
        f1.metric("Orçamento Total Disponível", formatar_moeda_br(orcamento_total))
        f2.metric("Total Investido 2026", formatar_moeda_br(total_investido))

        fin_chart = (
            ff.groupby(["Mes", "instituicao_financeira"], as_index=False)["Saldo_em_Reais"]
            .sum()
            .sort_values(["Mes", "Saldo_em_Reais"], ascending=[True, False])
        )
        fin_chart["mes_label"] = fin_chart["Mes"].map(MESES_LABEL)
        fig_fin = px.bar(
            fin_chart,
            x="mes_label",
            y="Saldo_em_Reais",
            color="instituicao_financeira",
            barmode="group",
            labels={"mes_label": "Mês", "Saldo_em_Reais": "Valor (R$)", "instituicao_financeira": "Instituição Financeira"},
            title="Volume de investimentos por instituição financeira e por mês",
        )
        st.plotly_chart(fig_fin, use_container_width=True)
    else:
        st.warning("⚠️ Base financeira carregada, mas sem registros para 2026.")
else:
    st.warning("⚠️ Arquivo de finanças não encontrado ou vazio.")
