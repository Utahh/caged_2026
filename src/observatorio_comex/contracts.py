"""Contratos (Protocols) para injeção de dependência — extração mock vs PostgreSQL."""
from __future__ import annotations

from typing import Protocol

import polars as pl

from observatorio_comex.config import MunicipioAlvo


class ComexCatalogExtractor(Protocol):
    """Catálogo MDIC: SH4 e descrições."""

    def extract(self) -> pl.LazyFrame: ...


class Sh4CnaeCrosswalkExtractor(Protocol):
    """De-para IBGE/Concla (ou aproximação interna): SH4 × CNAE."""

    def extract(self) -> pl.LazyFrame: ...


class EmpresasMunicipioExtractor(Protocol):
    """
    Empresas/estabelecimentos já filtrados por município no SQL (pushdown).

    Nunca retornar a base nacional inteira para o Polars em produção.
    """

    def extract(self, municipio: MunicipioAlvo) -> pl.LazyFrame: ...


class HabilitadosComexExtractor(Protocol):
    """CNPJs com habilitação Radar/Siscomex (RFB)."""

    def extract(self) -> pl.LazyFrame: ...


class RastreabilidadeExporter(Protocol):
    """Persistência do fato final (Parquet, CSV ou COPY no PostgreSQL)."""

    def export(self, df: pl.DataFrame) -> None: ...
