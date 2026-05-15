"""Transformações Polars: explosão CNAE, crosswalk SH4, flag habilitação Comex."""
from __future__ import annotations

import polars as pl

from observatorio_comex.config import PipelineConfig


def _digits_only(expr: pl.Expr) -> pl.Expr:
    return expr.cast(pl.Utf8).str.replace_all(r"\D", "").fill_null("")


def normalize_crosswalk(lf: pl.LazyFrame) -> pl.LazyFrame:
    return lf.with_columns(
        pl.col("sh4").cast(pl.Utf8).str.zfill(4),
        _digits_only(pl.col("cnae_codigo")).alias("cnae_codigo"),
        _digits_only(pl.col("cnae_prefix")).alias("cnae_prefix"),
    ).with_columns(
        pl.when(pl.col("cnae_prefix").str.len_chars() == 0)
        .then(pl.col("cnae_codigo").str.slice(0, 2))
        .otherwise(pl.col("cnae_prefix"))
        .alias("cnae_prefix")
    )


def filter_empresas_ativas(lf: pl.LazyFrame, config: PipelineConfig) -> pl.LazyFrame:
    sit = pl.col("situacao_cadastral").cast(pl.Utf8).str.to_uppercase()
    ativas = {s.upper() for s in config.situacoes_ativas}
    return lf.filter(sit.is_in(list(ativas)))


def explode_cnaes_empresa(empresas: pl.LazyFrame) -> pl.LazyFrame:
    """
    Uma linha por (CNPJ, CNAE) com flag Principal vs Secundário.

    Regra: não cruzar só o CNAE principal — importadores frequentemente
    têm o CNAE da cadeia nas atividades secundárias.
    """
    principal = empresas.select(
        [
            "cnpj",
            "razao_social",
            "municipio_ibge",
            "municipio_nome",
            "situacao_cadastral",
            _digits_only(pl.col("cnae_fiscal_principal")).alias("cnae_empresa"),
            pl.lit("Principal").alias("tipo_cnae_match"),
        ]
    ).filter(pl.col("cnae_empresa").str.len_chars() > 0)

    secundarios = (
        empresas.select(
            [
                "cnpj",
                "razao_social",
                "municipio_ibge",
                "municipio_nome",
                "situacao_cadastral",
                pl.col("cnaes_secundarios"),
            ]
        )
        .explode("cnaes_secundarios")
        .with_columns(
            _digits_only(pl.col("cnaes_secundarios")).alias("cnae_empresa"),
            pl.lit("Secundario").alias("tipo_cnae_match"),
        )
        .drop("cnaes_secundarios")
        .filter(pl.col("cnae_empresa").str.len_chars() > 0)
    )

    return pl.concat([principal, secundarios], how="vertical_relaxed").unique(
        ["cnpj", "cnae_empresa", "tipo_cnae_match"]
    )


def join_empresas_crosswalk(
    empresas_cnae: pl.LazyFrame,
    crosswalk: pl.LazyFrame,
    comex: pl.LazyFrame,
) -> pl.LazyFrame:
    """
    Associa empresa × CNAE ao SH4 via prefixo do crosswalk.

    Match: `cnae_empresa`.startswith(`cnae_prefix`) OU igualdade exata em `cnae_codigo`.

    Em PostgreSQL (alternativa para alto volume), prefira:
    ```sql
    JOIN crosswalk cw ON e.cnae_empresa LIKE cw.cnae_prefix || '%'
       OR e.cnae_empresa = cw.cnae_codigo
    ```
    com crosswalk pré-filtrado por SH4 de interesse.
    """
    cw = normalize_crosswalk(crosswalk)
    emp = empresas_cnae.with_columns(_digits_only(pl.col("cnae_empresa")).alias("cnae_empresa"))

    # Cross join controlado: volume baixo após pushdown municipal + crosswalk filtrado
    matched = (
        emp.join(cw, how="cross")
        .filter(
            pl.col("cnae_empresa").str.starts_with(pl.col("cnae_prefix"))
            | (pl.col("cnae_empresa") == pl.col("cnae_codigo"))
        )
        .select(
            [
                "cnpj",
                "razao_social",
                "municipio_ibge",
                "municipio_nome",
                "situacao_cadastral",
                "cnae_empresa",
                "tipo_cnae_match",
                "sh4",
                "cnae_codigo",
                "cnae_prefix",
                "motivo_relacao",
                "fonte",
            ]
        )
        .unique(["cnpj", "sh4", "cnae_empresa", "tipo_cnae_match", "cnae_codigo"])
    )

    comex_norm = comex.with_columns(pl.col("sh4").cast(pl.Utf8).str.zfill(4))
    return matched.join(comex_norm, on="sh4", how="left")


def enrich_habilitacao_comex(
    fact: pl.LazyFrame,
    habilitados: pl.LazyFrame,
) -> pl.LazyFrame:
    """Left join: `possui_habilitacao_comex` reduz falsos positivos no painel."""
    hab = habilitados.select(
        _digits_only(pl.col("cnpj")).str.slice(0, 14).alias("cnpj"),
        pl.lit(True).alias("possui_habilitacao_comex"),
    ).unique("cnpj")

    return (
        fact.with_columns(_digits_only(pl.col("cnpj")).str.slice(0, 14).alias("cnpj"))
        .join(hab, on="cnpj", how="left")
        .with_columns(pl.col("possui_habilitacao_comex").fill_null(False))
    )


def build_fato_rastreabilidade(
    empresas: pl.LazyFrame,
    crosswalk: pl.LazyFrame,
    comex: pl.LazyFrame,
    habilitados: pl.LazyFrame,
    config: PipelineConfig,
) -> pl.LazyFrame:
    """Orquestra transformações em ordem segura para memória."""
    emp_f = filter_empresas_ativas(empresas, config)
    emp_cnae = explode_cnaes_empresa(emp_f)
    fact = join_empresas_crosswalk(emp_cnae, crosswalk, comex)
    return enrich_habilitacao_comex(fact, habilitados)
