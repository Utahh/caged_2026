"""
Camada explícita EVENTO → FATO para o Novo CAGED (Botucatu + comparativo municipal).

- **Eventos (micro, vigente):** uma linha por movimentação após deduplicação MOV/FOR/EXC, com
  proveniência opcional (id, competência da pasta FTP, prioridade da fonte).
- **Fato mensal Botucatu:** agregação por competência do evento × CNAE (subclasse) — CSV principal do app.
- **Fato comparativo:** agregação por competência × município (saldo).

`exec_meta.json` (schema v2) consolida contagens, export opcional de micro e **SHA-256 dos .7z** baixados
por competência de pasta FTP. Gzip de eventos: `PIPELINE_CAGED_EXPORT_EVENTOS=1`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
_DEFAULT_STAGING = BASE_DIR / "data" / "caged_staging"
AUX_COLS = ("__caged_mov_id", "__ftp_decl_y", "__ftp_decl_m", "__caged_src_rank")


def staging_dir() -> Path:
    raw = os.environ.get("PIPELINE_CAGED_STAGING_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_STAGING


def export_eventos_enabled() -> bool:
    return os.environ.get("PIPELINE_CAGED_EXPORT_EVENTOS", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def normalize_subclasse_code(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits.zfill(7) if digits else ""


def strip_caged_aux_columns(df: pd.DataFrame) -> pd.DataFrame:
    dropme = [c for c in AUX_COLS if c in df.columns]
    return df.drop(columns=dropme, errors="ignore")


def _prioridade_fonte_label(rank: object) -> str:
    try:
        r = int(float(rank))
    except (TypeError, ValueError):
        return ""
    return {0: "MOV", 1: "FOR", 2: "EXC"}.get(r, str(r))


def prepare_eventos_export_df(micro: pd.DataFrame) -> pd.DataFrame:
    """Renomeia colunas auxiliares para leitura humana / auditoria."""
    out = micro.copy()
    if "__caged_mov_id" in out.columns:
        out = out.rename(columns={"__caged_mov_id": "id_movimentacao"})
    if "__ftp_decl_y" in out.columns:
        out = out.rename(columns={"__ftp_decl_y": "competencia_declaracao_ano"})
    if "__ftp_decl_m" in out.columns:
        out = out.rename(columns={"__ftp_decl_m": "competencia_declaracao_mes"})
    if "__caged_src_rank" in out.columns:
        out["fonte_prioridade"] = out["__caged_src_rank"].map(_prioridade_fonte_label)
        out = out.drop(columns=["__caged_src_rank"], errors="ignore")
    return out


def aggregate_micro_to_fato_mensal(slim: pd.DataFrame) -> pd.DataFrame:
    """Fato mensal Botucatu: competência do evento × seção × subclasse."""
    if slim.empty:
        return pd.DataFrame(
            columns=[
                "ano_referencia",
                "mes_referencia",
                "secao",
                "subclasse",
                "saldomovimentacao",
                "admissao",
                "demissao",
            ]
        )
    req = ["ano_referencia", "mes_referencia", "secao", "subclasse", "saldomovimentacao", "admissao", "demissao"]
    miss = [c for c in req if c not in slim.columns]
    if miss:
        raise ValueError(f"aggregate_micro_to_fato_mensal: faltam colunas {miss}")
    caged = (
        slim.groupby(["ano_referencia", "mes_referencia", "secao", "subclasse"], as_index=False)[
            ["saldomovimentacao", "admissao", "demissao"]
        ]
        .sum()
        .sort_values(["ano_referencia", "mes_referencia", "secao", "subclasse"])
    )
    caged["subclasse"] = caged["subclasse"].map(normalize_subclasse_code)
    caged["estoque_anual_2026"] = caged.groupby(["secao", "subclasse"])["saldomovimentacao"].transform("sum")
    return caged


def aggregate_comp_micro_to_fato(slim: pd.DataFrame) -> pd.DataFrame:
    """Fato comparativo: competência do evento × código de município (saldo)."""
    if slim.empty:
        return pd.DataFrame(columns=["ano_referencia", "mes_referencia", "municipio_codigo", "Saldo"])
    req = ["ano_referencia", "mes_referencia", "municipio_codigo", "Saldo"]
    miss = [c for c in req if c not in slim.columns]
    if miss:
        raise ValueError(f"aggregate_comp_micro_to_fato: faltam colunas {miss}")
    return (
        slim.groupby(["ano_referencia", "mes_referencia", "municipio_codigo"], as_index=False)["Saldo"]
        .sum()
        .sort_values(["ano_referencia", "mes_referencia", "municipio_codigo"])
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_exec_meta_v2(
    *,
    periods: List[Tuple[int, int]],
    dedupe_por_id: bool,
    ftp_fontes_7z: List[Dict[str, Any]],
    botucatu: Dict[str, Any],
    comparativo_municipios: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "schema": "caged_exec_meta_v2",
        "gerado_em_utc": _iso_now(),
        "periodos_processados": [{"ano": y, "mes": m} for y, m in periods],
        "n_periodos": len(periods),
        "dedupe_por_id_movimentacao": dedupe_por_id,
        "ftp_fontes_7z": ftp_fontes_7z,
        "botucatu": botucatu,
        "comparativo_municipios": comparativo_municipios,
        "nota": (
            "Micro = estado vigente após deduplicação na união das pastas FTP processadas; "
            "fatos mensais = agregações por competência do evento. "
            "ftp_fontes_7z = hash do arquivo baixado antes da extração (auditoria de snapshot). "
            "Reprocesse quando o MTPE republicar MOV/FOR/EXC."
        ),
    }


def write_exec_meta(staging: Path, meta: Dict[str, Any]) -> Path:
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / "exec_meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def export_eventos_micro_gzip(micro: pd.DataFrame, staging: Path, filename: str) -> Path:
    staging.mkdir(parents=True, exist_ok=True)
    out = staging / filename
    prepare_eventos_export_df(micro).to_csv(out, sep=";", index=False, encoding="utf-8-sig", compression="gzip")
    return out


def finalize_caged_botucatu_layers(caged_micro_pos_dedupe: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Micro deduplicado → fato mensal CNAE; fragmento de meta (sem gravar JSON)."""
    staging = staging_dir()
    export_ev = export_eventos_enabled()
    eventos_rel: str | None = None
    if export_ev and not caged_micro_pos_dedupe.empty:
        eventos_rel = export_eventos_micro_gzip(
            caged_micro_pos_dedupe, staging, "eventos_botucatu_micro.csv.gz"
        ).name

    slim = strip_caged_aux_columns(caged_micro_pos_dedupe)
    fato = aggregate_micro_to_fato_mensal(slim)

    bot_info: Dict[str, Any] = {
        "linhas_micro_pos_dedupe": int(len(caged_micro_pos_dedupe)),
        "linhas_fato_mensal": int(len(fato)),
        "export_eventos_micro_habilitado": export_ev,
        "arquivo_eventos_micro": eventos_rel,
    }
    return fato, bot_info


def finalize_caged_comp_municipios_layers(comp_micro_pos_dedupe: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Micro comparativo deduplicado → fato mês × município; fragmento de meta."""
    staging = staging_dir()
    export_ev = export_eventos_enabled()
    eventos_rel: str | None = None
    if export_ev and not comp_micro_pos_dedupe.empty:
        eventos_rel = export_eventos_micro_gzip(
            comp_micro_pos_dedupe, staging, "eventos_comparativo_municipios_micro.csv.gz"
        ).name

    slim = strip_caged_aux_columns(comp_micro_pos_dedupe)
    fato = aggregate_comp_micro_to_fato(slim)

    comp_info: Dict[str, Any] = {
        "linhas_micro_pos_dedupe": int(len(comp_micro_pos_dedupe)),
        "linhas_fato_mes_municipio": int(len(fato)),
        "export_eventos_micro_habilitado": export_ev,
        "arquivo_eventos_micro": eventos_rel,
    }
    return fato, comp_info
