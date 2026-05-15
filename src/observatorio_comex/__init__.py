"""
Rastreabilidade Comex (SH4) → CNAE → CNPJ para o Observatório Econômico municipal.

Pipeline ETL/ELT em Polars com pushdown geográfico, explosão de CNAEs secundários
e enriquecimento com habilitação Siscomex/Radar.
"""

from observatorio_comex.config import MunicipioAlvo, PipelineConfig
from observatorio_comex.pipeline import ComexCnpjRastreabilidadePipeline

__all__ = [
    "ComexCnpjRastreabilidadePipeline",
    "MunicipioAlvo",
    "PipelineConfig",
]
