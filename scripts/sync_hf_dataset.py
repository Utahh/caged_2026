#!/usr/bin/env python3
"""
Sobe artefatos CSV (e README) do projeto para um Dataset no Hugging Face Hub.

Requer: pip install huggingface_hub
Token: variável de ambiente HF_TOKEN ou HUGGING_FACE_HUB_TOKEN (https://huggingface.co/settings/tokens)

Repositório alvo (dataset):
  HF_REPO — padrão: Utahh/caged_2026 (ajuste se o dataset no Hub tiver outro nome)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_FILES = [
    "README.md",
    "caged_botucatu_q1_2026.csv",
    "caged_comparativo_municipios.csv",
    "relatorio_botucatu_q1_2026.csv",
    "financas_botucatu_2026.csv",
    "investimentos_botucatu_2026.csv",
    "estban_botucatu_2025_2026.csv",
    "comex_botucatu_mensal.csv",
    "comex_botucatu_meta.csv",
    "comex_botucatu_top_sh4_export.csv",
    "comex_botucatu_top_sh4_import.csv",
    "cnpj_botucatu_resumo.csv",
    "cnpj_botucatu_mei_mensal.csv",
    "cnpj_botucatu_porte_pct.csv",
    "cnpj_botucatu_cnae_x_tipo.csv",
    "data/comex_sh4_cnae_aproximacao.csv",
]


def main() -> int:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        print(
            "Defina HF_TOKEN (ou HUGGING_FACE_HUB_TOKEN) com um token de escrita em "
            "https://huggingface.co/settings/tokens",
            file=sys.stderr,
        )
        return 2

    repo_id = os.environ.get("HF_REPO", "Utahh/caged_2026").strip()
    if not repo_id or "/" not in repo_id:
        print("HF_REPO deve ser 'organizacao/nome-do-repo' (ex.: Utahh/caged_2026).", file=sys.stderr)
        return 2

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("Instale: pip install huggingface_hub", file=sys.stderr)
        return 2

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=False, exist_ok=True)

    uploaded = 0
    for rel in DEFAULT_FILES:
        path = ROOT / rel
        if not path.is_file():
            print(f"[skip] ausente: {rel}")
            continue
        path_in_repo = rel.replace("\\", "/")
        print(f"[upload] {path_in_repo} …")
        api.upload_file(
            path_or_fileobj=str(path),
            path_in_repo=path_in_repo,
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=f"chore: atualiza {path_in_repo}",
        )
        uploaded += 1

    print(f"Concluído: {uploaded} arquivo(s) em https://huggingface.co/datasets/{repo_id}")
    return 0 if uploaded else 1


if __name__ == "__main__":
    raise SystemExit(main())
