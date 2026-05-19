#!/usr/bin/env python3
"""
Publica app + CSVs no Hugging Face Space (Streamlit).

Requer: pip install -r requirements-hf.txt

Variáveis:
  HF_TOKEN / HUGGING_FACE_HUB_TOKEN (ou `.env`)
  HF_SPACE_REPO — padrão: {usuario}/observatorio-botucatu
  HF_DATASET_REPO — usado com --from-dataset (padrão: {usuario}/caged_2026)

Uso:
  python scripts/sync_hf_space.py
  python scripts/sync_hf_space.py --from-dataset
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from sync_hf_dataset import (  # noqa: E402
    DEFAULT_FILES,
    OPTIONAL_FILES,
    ROOT as _ROOT,
    _load_token_from_dotenv,
)

assert _ROOT == ROOT

SPACE_APP_FILES = [
    "app.py",
    "requirements.txt",
    "README.md",
    ".streamlit/config.toml",
]

# Opcionais do dataset que o painel consome (sem metadata/exec_meta.json)
SPACE_OPTIONAL = [
    entry for entry in OPTIONAL_FILES if not entry[1].startswith("metadata/")
]


def _default_repo_id(token: str, suffix: str) -> str:
    try:
        from huggingface_hub import HfApi

        who = HfApi(token=token).whoami()
        user = who.get("name") or "user"
    except Exception:
        user = "user"
    return f"{user}/{suffix}"


def _collect_uploads() -> list[tuple[Path, str]]:
    """(caminho local, path_in_repo no Space)."""
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def add(local_rel: str, remote: str | None = None) -> None:
        remote_path = (remote or local_rel).replace("\\", "/")
        if remote_path in seen:
            return
        path = ROOT / local_rel
        if not path.is_file():
            return
        seen.add(remote_path)
        out.append((path, remote_path))

    for rel in SPACE_APP_FILES:
        add(rel)
    for rel in DEFAULT_FILES:
        add(rel)
    for local_rel, remote in SPACE_OPTIONAL:
        add(local_rel, remote)
    return out


def _all_repo_paths() -> list[str]:
    paths: list[str] = []
    for rel in SPACE_APP_FILES + DEFAULT_FILES:
        paths.append(rel.replace("\\", "/"))
    for local_rel, remote in SPACE_OPTIONAL:
        paths.append(remote.replace("\\", "/"))
    return list(dict.fromkeys(paths))


def _ensure_from_dataset(dataset_id: str, token: str, force: bool = False) -> int:
    from huggingface_hub import hf_hub_download

    fetched = 0
    for remote in _all_repo_paths():
        dest = ROOT / remote
        if dest.is_file() and not force:
            continue
        print(f"[dataset] baixando {remote} de {dataset_id} …")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            hf_hub_download(
                repo_id=dataset_id,
                repo_type="dataset",
                filename=remote,
                local_dir=str(ROOT),
                token=token,
            )
        except Exception as exc:
            print(f"[warn] {remote}: {exc}", file=sys.stderr)
            continue
        if dest.is_file():
            fetched += 1
        else:
            print(f"[warn] não encontrado no dataset: {remote}", file=sys.stderr)
    return fetched


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza app e CSVs com um HF Space.")
    parser.add_argument(
        "--from-dataset",
        action="store_true",
        help="Baixa do HF_DATASET_REPO os arquivos ausentes localmente antes do upload.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Lista arquivos sem enviar.")
    args = parser.parse_args()

    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or _load_token_from_dotenv(ROOT)
    )
    if not token:
        print(
            "Defina HF_TOKEN (env ou `.env`). Token em https://huggingface.co/settings/tokens",
            file=sys.stderr,
        )
        return 2

    space_id = os.environ.get("HF_SPACE_REPO", _default_repo_id(token, "observatorio-botucatu")).strip()
    if not space_id or "/" not in space_id:
        print("HF_SPACE_REPO deve ser 'usuario/nome-do-space'.", file=sys.stderr)
        return 2

    if args.from_dataset:
        dataset_id = os.environ.get("HF_DATASET_REPO", _default_repo_id(token, "caged_2026")).strip()
        _ensure_from_dataset(dataset_id, token)

    uploads = _collect_uploads()

    missing = [remote for path, remote in uploads if not path.is_file()]
    if missing:
        print("Arquivos ausentes (rode o pipeline ou use --from-dataset):", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        uploads = [(p, r) for p, r in uploads if p.is_file()]

    if not uploads:
        print("Nenhum arquivo para enviar.", file=sys.stderr)
        return 1

    print(f"Space: https://huggingface.co/spaces/{space_id}")
    print(f"Arquivos: {len(uploads)}")
    for path, remote in uploads:
        print(f"  {remote} ({path.stat().st_size // 1024} KiB)")

    if args.dry_run:
        return 0

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("Instale: pip install -r requirements-hf.txt", file=sys.stderr)
        return 2

    from huggingface_hub.errors import RepositoryNotFoundError

    api = HfApi(token=token)
    try:
        api.repo_info(repo_id=space_id, repo_type="space")
    except RepositoryNotFoundError:
        api.create_repo(
            repo_id=space_id,
            repo_type="space",
            space_sdk="streamlit",
            private=False,
        )

    with tempfile.TemporaryDirectory(prefix="hf_space_") as tmp:
        staging = Path(tmp)
        for path, remote in uploads:
            dest = staging / remote
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        print("[upload] enviando pasta (um commit) …")
        api.upload_folder(
            folder_path=str(staging),
            repo_id=space_id,
            repo_type="space",
            commit_message="chore: sync observatorio (app + dados)",
        )

    print(f"Concluído: https://huggingface.co/spaces/{space_id}")
    print("O Space recompila automaticamente; use Restart se o cache do Streamlit persistir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
