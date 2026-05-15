#!/usr/bin/env python3
"""
Executa o pipeline de rastreabilidade SH4 → CNAE → CNPJ (mocks).

Uso (na raiz do projeto):
  python scripts/run_comex_rastreabilidade.py

Com PostgreSQL no futuro, injete extractors Postgres* e ParquetExporter/COPY.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import polars as pl

from observatorio_comex.integration import run_rastreabilidade_botucatu


def main() -> None:
    df = run_rastreabilidade_botucatu(ROOT)

    print("\n--- Amostra do fato (SH4 × empresa) ---")
    cols = [
        "sh4",
        "descricao_sh4",
        "cnpj",
        "razao_social",
        "cnae_empresa",
        "tipo_cnae_match",
        "possui_habilitacao_comex",
        "motivo_relacao",
    ]
    present = [c for c in cols if c in df.columns]
    out = df.select(present).sort("sh4", "cnpj")
    with pl.Config(tbl_formatting="ASCII_FULL_CONDENSED"):
        print(out)


if __name__ == "__main__":
    main()
