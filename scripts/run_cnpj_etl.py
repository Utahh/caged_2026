#!/usr/bin/env python3
"""Atualiza CSVs CNPJ/MEI de Botucatu e regenera o fato SH4×empresas."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("CNPJ_USE_DEFAULT_MIRROR", "1")
os.environ.setdefault("PIPELINE_INCLUDE_COMEX_RASTREABILIDADE", "1")
# Prioriza espelho estável quando a RFB não responde na rede local
if not os.environ.get("CNPJ_BASE_URL", "").strip():
    from cnpj_botucatu_etl import CNPJ_DEFAULT_MIRROR_BASE

    os.environ["CNPJ_BASE_URL"] = CNPJ_DEFAULT_MIRROR_BASE


def main() -> None:
    from cnpj_botucatu_etl import cleanup_workdir, export_cnpj_csvs, run_cnpj_botucatu_etl

    try:
        base = os.environ.get("CNPJ_BASE_URL", "").strip() or None
        dfs = run_cnpj_botucatu_etl(base_url=base)
        export_cnpj_csvs(dfs)
        resumo = dfs["resumo"].iloc[0]
        print(
            f"CNPJ OK — empresas={int(resumo['total_empresas'])}, "
            f"estabelecimentos={int(resumo['total_estabelecimentos'])}"
        )
    finally:
        cleanup_workdir()

    from observatorio_comex.integration import run_rastreabilidade_botucatu

    fact = run_rastreabilidade_botucatu(ROOT)
    print(f"Fato SH4×empresas: {len(fact)} linhas")


if __name__ == "__main__":
    main()
