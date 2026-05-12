"""
ETL Comércio exterior (Comex Stat / MDIC) para Botucatu — série mensal + top SH4 + PTAX BCB.

Fontes:
  - https://api-comexstat.mdic.gov.br (endpoint `/cities`, município do declarante)
  - PTAX venda (série SGS 1) — https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados

Limitação legal dos dados públicos: **não há estatística oficial por empresa/CNPJ** no Comex Stat
(sigilo fiscal). O painel traz **totais municipais** e **ranking por produto (SH4)** como visão analítica.

O código interno de município no Comex Stat para Botucatu-SP costuma ser **3407506** (tabela
`/tables/cities?search=botucatu`). Ajuste com `COMEX_CITY_ID` se necessário.
"""

from __future__ import annotations

import os
import random
import re
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
COMEX_BASE = "https://api-comexstat.mdic.gov.br"
BCB_SGS_PTAX = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados"
DEFAULT_CITY_ID = os.environ.get("COMEX_CITY_ID", "3407506").strip()
MONTHS_BACK = max(12, int(os.environ.get("COMEX_MONTHS_BACK", "24")))
RANKING_YEAR = os.environ.get("COMEX_RANKING_YEAR", "").strip()


def log(msg: str) -> None:
    print(f"[COMEX] {msg}", flush=True)


def _ym_key(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def enumerate_months(sy: int, sm: int, ey: int, em: int) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    y, m = sy, sm
    while y < ey or (y == ey and m <= em):
        out.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def group_months_into_intra_year_chunks(months: List[Tuple[int, int]]) -> List[Tuple[int, int, int, int]]:
    """Agrupa meses consecutivos dentro do **mesmo ano civil** (a API Comex por município não cruza anos num POST)."""
    if not months:
        return []
    chunks: List[Tuple[int, int, int, int]] = []
    sy, sm = months[0]
    py, pm = months[0]
    for y, m in months[1:]:
        ano_diferente = y != py
        buraco = not (y == py and m == pm + 1)
        if ano_diferente or buraco:
            chunks.append((sy, sm, py, pm))
            sy, sm = y, m
        py, pm = y, m
    chunks.append((sy, sm, py, pm))
    return chunks


def comex_last_updated() -> Tuple[int, int]:
    r = requests.get(f"{COMEX_BASE}/cities/dates/updated", timeout=45)
    r.raise_for_status()
    d = r.json()["data"]
    return int(d["year"]), int(d["monthNumber"])


def subtract_months(y: int, m: int, n: int) -> Tuple[int, int]:
    for _ in range(n):
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
    return y, m


def _retry_after_seconds(r: requests.Response) -> int:
    """Segundos a aguardar após 429 (header Retry-After ou texto da API MDIC)."""
    h = (r.headers.get("Retry-After") or "").strip()
    if h.isdigit():
        return max(int(h), 12)
    try:
        payload = r.json()
        msg = str((payload.get("error") or {}).get("message") or "")
    except Exception:
        msg = (r.text or "")[:500]
    m = re.search(r"(\d+)\s*segundos?", msg, re.I)
    if m:
        return max(int(m.group(1)), 12)
    return 20


def comex_post_cities(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """POST /cities com pausa e re-tentativas em caso de 429 (limite MDIC)."""
    url = f"{COMEX_BASE}/cities?language=pt"
    pause = float(os.environ.get("COMEX_REQUEST_PAUSE_SEC", "12"))
    last_txt = ""
    for attempt in range(1, 26):
        time.sleep(pause + random.uniform(0, 2.5))
        r = requests.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=180)
        if r.status_code == 429:
            wait = _retry_after_seconds(r) + int(random.uniform(1, 4))
            log(f"HTTP 429 (tentativa interna {attempt}/25) — aguardando {wait}s…")
            time.sleep(wait)
            last_txt = r.text[:200]
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
        payload = r.json()
        if not payload.get("success"):
            raise RuntimeError(str(payload.get("message") or payload))
        return list(payload.get("data", {}).get("list") or [])
    raise RuntimeError(f"Comex Stat: limite de taxa (429) persistente. Última resposta: {last_txt}")


def fetch_monthly_totals(city_id: str, sy: int, sm: int, ey: int, em: int) -> List[Dict[str, Any]]:
    months = enumerate_months(sy, sm, ey, em)
    rows: List[Dict[str, Any]] = []
    for csy, csm, cey, cem in group_months_into_intra_year_chunks(months):
        body = {
            "flow": "export",
            "monthDetail": True,
            "period": {"from": f"{csy}-{csm:02d}", "to": f"{cey}-{cem:02d}"},
            "filters": [{"filter": "city", "values": [city_id]}],
            "details": [],
            "metrics": ["metricFOB"],
        }
        for row in comex_post_cities(body):
            rows.append(
                {
                    "ano": int(row["year"]),
                    "mes": int(row["monthNumber"]),
                    "fluxo": "exportacao",
                    "valor_usd_fob": float(str(row.get("metricFOB", "0") or "0")),
                }
            )
        body["flow"] = "import"
        for row in comex_post_cities(body):
            rows.append(
                {
                    "ano": int(row["year"]),
                    "mes": int(row["monthNumber"]),
                    "fluxo": "importacao",
                    "valor_usd_fob": float(str(row.get("metricFOB", "0") or "0")),
                }
            )
    return rows


def fetch_heading_year(city_id: str, year: int, flow: str) -> List[Dict[str, Any]]:
    body = {
        "flow": flow,
        "monthDetail": False,
        "period": {"from": f"{year}-01", "to": f"{year}-12"},
        "filters": [{"filter": "city", "values": [city_id]}],
        "details": ["heading"],
        "metrics": ["metricFOB"],
    }
    out: List[Dict[str, Any]] = []
    for row in comex_post_cities(body):
        out.append(
            {
                "sh4": str(row.get("headingCode", "")).strip(),
                "descricao": str(row.get("heading", "")).strip(),
                "valor_usd_fob": float(str(row.get("metricFOB", "0") or "0")),
            }
        )
    return out


def fetch_bcb_ptax_year(year: int) -> pd.DataFrame:
    """Série diária PTAX venda (USD) para um ano civil."""
    ini = f"01/01/{year}"
    fim = f"31/12/{year}"
    url = f"{BCB_SGS_PTAX}?formato=json&dataInicial={ini}&dataFinal={fim}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame(columns=["data", "valor"])
    df = pd.DataFrame(rows)
    df["data"] = pd.to_datetime(df["data"], dayfirst=True, errors="coerce")
    df["valor"] = pd.to_numeric(df["valor"].astype(str).str.replace(",", "."), errors="coerce")
    return df.dropna(subset=["data", "valor"])


def ptax_monthly_means(years: range) -> pd.DataFrame:
    parts = []
    for y in years:
        try:
            d = fetch_bcb_ptax_year(y)
        except Exception as exc:
            log(f"Aviso PTAX {y}: {exc}")
            continue
        if d.empty:
            continue
        d["ano"] = d["data"].dt.year
        d["mes"] = d["data"].dt.month
        g = d.groupby(["ano", "mes"], as_index=False)["valor"].mean().rename(columns={"valor": "ptax_media"})
        parts.append(g)
    if not parts:
        return pd.DataFrame(columns=["ano", "mes", "ptax_media"])
    return pd.concat(parts, ignore_index=True)


def attach_ptax(monthly_rows: List[Dict[str, Any]], ptax: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(monthly_rows)
    if df.empty:
        return df
    m = ptax.rename(columns={"ano": "ano", "mes": "mes"})
    out = df.merge(m, on=["ano", "mes"], how="left")
    out["valor_brl_estimado"] = (out["valor_usd_fob"] * out["ptax_media"]).round(2)
    return out.sort_values(["ano", "mes", "fluxo"]).reset_index(drop=True)


def ranking_year_default(api_y: int, api_m: int) -> int:
    """Ano civil completo preferencial para ranking SH4 (evita ano corrente incompleto)."""
    if RANKING_YEAR.isdigit():
        return int(RANKING_YEAR)
    if api_m >= 12:
        return api_y
    return api_y - 1


def export_comex_csvs(out_dir: Optional[Path] = None) -> None:
    out = out_dir or BASE_DIR
    city_id = DEFAULT_CITY_ID
    api_y, api_m = comex_last_updated()
    log(f"Comex Stat última referência: {api_y}-{api_m:02d} · município (API) id={city_id}")

    ey, em = api_y, api_m
    sy, sm = subtract_months(ey, em, MONTHS_BACK - 1)

    years_needed = range(sy, ey + 1)
    ptax = ptax_monthly_means(years_needed)
    log(f"PTAX: {len(ptax)} meses agregados ({sy}-{sm} … {ey}-{em}).")

    monthly_raw = fetch_monthly_totals(city_id, sy, sm, ey, em)
    log(f"Série mensal: {len(monthly_raw)} linhas (exp+imp).")
    mensal = attach_ptax(monthly_raw, ptax)
    mensal.to_csv(out / "comex_botucatu_mensal.csv", sep=";", index=False, encoding="utf-8-sig")

    rk_year = ranking_year_default(api_y, api_m)
    log(f"Ranking SH4 (export/import) ano {rk_year}.")
    exp_h = fetch_heading_year(city_id, rk_year, "export")
    imp_h = fetch_heading_year(city_id, rk_year, "import")

    ptax_y = ptax[ptax["ano"] == rk_year]["ptax_media"].mean()
    if pd.isna(ptax_y) or ptax_y == 0:
        ptax_y = float(ptax["ptax_media"].median()) if not ptax.empty else 0.0

    def top10(rows: List[Dict[str, Any]], fluxo: str) -> pd.DataFrame:
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "rank",
                    "ano",
                    "fluxo",
                    "sh4",
                    "descricao",
                    "valor_usd_fob",
                    "ptax_media_ano",
                    "valor_brl_estimado",
                ]
            )
        df = df.sort_values("valor_usd_fob", ascending=False).head(10).reset_index(drop=True)
        df.insert(0, "rank", range(1, len(df) + 1))
        df.insert(1, "ano", rk_year)
        df.insert(2, "fluxo", fluxo)
        df["ptax_media_ano"] = round(float(ptax_y), 4)
        df["valor_brl_estimado"] = (df["valor_usd_fob"] * ptax_y).round(2)
        return df

    top_x = top10(exp_h, "exportacao")
    top_m = top10(imp_h, "importacao")
    top_x.to_csv(out / "comex_botucatu_top_sh4_export.csv", sep=";", index=False, encoding="utf-8-sig")
    top_m.to_csv(out / "comex_botucatu_top_sh4_import.csv", sep=";", index=False, encoding="utf-8-sig")

    meta = pd.DataFrame(
        [
            {"chave": "municipio_api_id", "valor": city_id},
            {"chave": "municipio_nome", "valor": "Botucatu - SP (Comex Stat)"},
            {"chave": "ibge_referencia", "valor": "3507506"},
            {"chave": "comex_ultima_atualizacao", "valor": f"{api_y}-{api_m:02d}"},
            {"chave": "ranking_sh4_ano", "valor": str(rk_year)},
            {"chave": "ptax_serie", "valor": "BCB SGS 1 (dólar venda, média mensal)"},
            {
                "chave": "nota_empresa",
                "valor": (
                    "O MDIC não divulga exportação/importação por CNPJ ou razão social nos dados abertos "
                    "(sigilo fiscal). O ranking disponível é por posição SH4 (produto)."
                ),
            },
            {
                "chave": "metodologia_brl",
                "valor": (
                    "Valor em R$ = US$ FOB × média mensal PTAX (venda). Aproximação contábil; contratos podem "
                    "usar taxas e incoterm diferentes."
                ),
            },
        ]
    )
    meta.to_csv(out / "comex_botucatu_meta.csv", sep=";", index=False, encoding="utf-8-sig")
    log(f"CSV exportados em {out}")


def main_cli() -> None:
    export_comex_csvs()


if __name__ == "__main__":
    main_cli()
