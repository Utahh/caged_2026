"""Configuração tipada do pipeline de rastreabilidade SH4 → CNPJ."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MunicipioAlvo:
    """Recorte geográfico do observatório (pushdown na extração de empresas)."""

    ibge_7: str
    nome: str
    # Códigos internos RFB aceitos no filtro de estabelecimentos (ex.: Botucatu)
    codigos_rfb_estabelecimento: frozenset[str] = field(default_factory=frozenset)


# Botucatu-SP — alinhado ao restante do repositório Extracao_CAGED
BOTUCATU = MunicipioAlvo(
    ibge_7="3507506",
    nome="BOTUCATU",
    codigos_rfb_estabelecimento=frozenset({"6249", "3507506"}),
)


@dataclass(frozen=True)
class PipelineConfig:
    municipio: MunicipioAlvo = BOTUCATU
    # Situações cadastrais consideradas "ativas" para o painel
    situacoes_ativas: frozenset[str] = frozenset({"02", "2", "ATIVA"})
    export_path: str | None = None  # ex.: data/processed/sh4_empresas_botucatu.parquet
