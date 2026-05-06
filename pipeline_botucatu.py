import json
import os
import shutil
import time
import urllib.request
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import py7zr
import requests


BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "tmp_pipeline_botucatu"
MONTHS = ["01", "02", "03"]
YEAR = "2026"
BOTUCATU_MUNICIPIO_CAGED = 350750
BOTUCATU_ID_ENTE_SICONFI = 3507506


def log(msg: str) -> None:
    print(f"[PIPELINE] {msg}", flush=True)


def retry_exec(func, attempts: int = 3, sleep_seconds: int = 3, context: str = ""):
    last_error = None
    for i in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            log(f"Falha ({i}/{attempts}) em {context}: {exc}")
            if i < attempts:
                time.sleep(sleep_seconds)
    raise RuntimeError(f"Erro após {attempts} tentativas em {context}") from last_error


def detect_delimiter(file_path: Path) -> str:
    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        sample = "".join([f.readline() for _ in range(5)])
    candidates = [";", ",", "\t", "|"]
    counts = {d: sample.count(d) for d in candidates}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ";"


def normalize_col_map(columns: Iterable[str]) -> Dict[str, str]:
    mapped = {}
    for c in columns:
        raw = str(c).strip().lower()
        ascii_key = (
            unicodedata.normalize("NFKD", raw)
            .encode("ascii", "ignore")
            .decode("ascii")
            .strip()
            .lower()
        )
        mapped[ascii_key] = c
    return mapped


def first_present(col_map: Dict[str, str], candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in col_map:
            return col_map[c]
    return None


def download_file(url: str, output_path: Path) -> None:
    def _do_download():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response, output_path.open("wb") as out:
            shutil.copyfileobj(response, out)

    retry_exec(_do_download, attempts=3, sleep_seconds=5, context=f"download FTP {url}")


def extract_7z(archive_path: Path, output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(archive_path, mode="r") as z:
        names = z.getnames()
        z.extractall(path=output_dir)
    return [output_dir / n for n in names]


def resolve_caged_columns(raw_file: Path, delimiter: str) -> Tuple[Dict[str, str], List[str]]:
    header_df = pd.read_csv(raw_file, sep=delimiter, nrows=0, encoding="utf-8", low_memory=False)
    col_map = normalize_col_map(header_df.columns)

    col_municipio = first_present(col_map, ["municipio", "id_municipio", "codmunicipio"])
    col_secao = first_present(col_map, ["secao"])
    col_subclasse = first_present(col_map, ["subclasse", "subclassecnae20", "subclasse_cnae"])
    col_saldo = first_present(col_map, ["saldomovimentacao", "saldo_movimentacao", "saldo"])

    mandatory = {"municipio": col_municipio, "secao": col_secao, "subclasse": col_subclasse, "saldo": col_saldo}
    missing = [k for k, v in mandatory.items() if not v]
    if missing:
        raise ValueError(
            f"Arquivo CAGED sem colunas obrigatórias ({', '.join(missing)}). "
            f"Colunas detectadas: {list(header_df.columns)}"
        )

    return {
        "municipio": col_municipio,
        "secao": col_secao,
        "subclasse": col_subclasse,
        "saldo": col_saldo,
    }, [col_municipio, col_secao, col_subclasse, col_saldo]


def process_caged_month(month: str) -> pd.DataFrame:
    month_url = f"ftp://ftp.mtps.gov.br/pdet/microdados/NOVO%20CAGED/{YEAR}/{YEAR}{month}/CAGEDMOV{YEAR}{month}.7z"
    archive_path = WORK_DIR / f"CAGEDMOV{YEAR}{month}.7z"
    extract_dir = WORK_DIR / f"extract_{month}"

    log(f"Iniciando CAGED mês {month}: download do FTP.")
    download_file(month_url, archive_path)
    log(f"Download concluído: {archive_path.name}")

    extracted_files = extract_7z(archive_path, extract_dir)
    txt_files = [p for p in extracted_files if p.suffix.lower() in (".txt", ".csv")]
    if not txt_files:
        raise RuntimeError(f"Nenhum TXT/CSV extraído do arquivo {archive_path.name}")

    raw_file = txt_files[0]
    delimiter = detect_delimiter(raw_file)
    log(f"Processando arquivo {raw_file.name} com delimitador '{delimiter}' em chunks de 100.000 linhas.")

    col_map, usecols = resolve_caged_columns(raw_file, delimiter)
    frames = []

    for idx, chunk in enumerate(
        pd.read_csv(
            raw_file,
            sep=delimiter,
            encoding="utf-8",
            chunksize=100_000,
            usecols=usecols,
            low_memory=False,
        ),
        start=1,
    ):
        chunk[col_map["municipio"]] = pd.to_numeric(chunk[col_map["municipio"]], errors="coerce").fillna(0).astype(int)
        filtered = chunk[chunk[col_map["municipio"]] == BOTUCATU_MUNICIPIO_CAGED].copy()
        if filtered.empty:
            if idx % 10 == 0:
                log(f"Mês {month}: chunk {idx} processado (sem linhas de Botucatu).")
            continue

        filtered["mes_referencia"] = int(month)
        filtered["saldomovimentacao"] = pd.to_numeric(filtered[col_map["saldo"]], errors="coerce").fillna(0).astype(int)
        filtered["admissao"] = (filtered["saldomovimentacao"] == 1).astype(int)
        filtered["demissao"] = (filtered["saldomovimentacao"] == -1).astype(int)

        month_df = pd.DataFrame(
            {
                "mes_referencia": filtered["mes_referencia"],
                "secao": filtered[col_map["secao"]].astype(str).str.strip(),
                "subclasse": filtered[col_map["subclasse"]].astype(str).str.strip(),
                "saldomovimentacao": filtered["saldomovimentacao"],
                "admissao": filtered["admissao"],
                "demissao": filtered["demissao"],
            }
        )
        frames.append(month_df)

        if idx % 10 == 0:
            log(f"Mês {month}: chunk {idx} processado, linhas acumuladas: {sum(len(f) for f in frames)}")

    # Limpeza obrigatória do disco após cada mês
    try:
        if archive_path.exists():
            archive_path.unlink()
        if raw_file.exists():
            raw_file.unlink()
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
    except Exception as cleanup_exc:
        log(f"Aviso de limpeza mês {month}: {cleanup_exc}")

    if not frames:
        return pd.DataFrame(
            columns=["mes_referencia", "secao", "subclasse", "saldomovimentacao", "admissao", "demissao"]
        )

    month_all = pd.concat(frames, ignore_index=True)
    return month_all


def run_caged_etl() -> pd.DataFrame:
    log("ETL CAGED iniciado.")
    monthly_results = []
    for m in MONTHS:
        try:
            month_df = process_caged_month(m)
            log(f"CAGED mês {m} finalizado. Linhas de Botucatu: {len(month_df)}")
            monthly_results.append(month_df)
        except Exception as exc:
            log(f"Erro ao processar mês {m}: {exc}")

    if not monthly_results:
        raise RuntimeError("Nenhum mês CAGED foi processado com sucesso.")

    caged = pd.concat(monthly_results, ignore_index=True)
    if caged.empty:
        log("CAGED consolidado vazio para Botucatu.")
        return caged

    caged = (
        caged.groupby(["mes_referencia", "secao", "subclasse"], as_index=False)[
            ["saldomovimentacao", "admissao", "demissao"]
        ]
        .sum()
        .sort_values(["mes_referencia", "secao", "subclasse"])
    )
    return caged


def fetch_siconfi_json() -> Dict:
    base_url = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/msc_patrimonial"
    params = {
        "id_ente": BOTUCATU_ID_ENTE_SICONFI,
        "an_referencia": YEAR,
        "co_tipo_matriz": "MSCC",
    }

    session = requests.Session()
    session.headers.update({"User-Agent": "pipeline-botucatu/1.0"})

    def _do_request():
        response = session.get(base_url, params=params, timeout=60)
        response.raise_for_status()
        return response.json()

    return retry_exec(_do_request, attempts=3, sleep_seconds=5, context="requisição API Siconfi")


def run_siconfi_etl() -> pd.DataFrame:
    log("ETL Siconfi iniciado.")
    payload = fetch_siconfi_json()

    # Estruturas comuns da API ORDS: {"items":[...]} ou lista direta
    if isinstance(payload, dict):
        if "items" in payload and isinstance(payload["items"], list):
            items = payload["items"]
        elif "result" in payload and isinstance(payload["result"], list):
            items = payload["result"]
        else:
            # fallback: tenta usar todos os valores list
            values_lists = [v for v in payload.values() if isinstance(v, list)]
            items = values_lists[0] if values_lists else []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    if not items:
        log("Siconfi retornou JSON vazio ou sem itens.")
        return pd.DataFrame(columns=["Mês", "Código Contábil", "Natureza", "Saldo em Reais"])

    df = pd.DataFrame(items)
    col_map = normalize_col_map(df.columns)

    col_conta = first_present(col_map, ["conta_contabil", "co_conta_contabil", "codigo_contabil"])
    col_mes = first_present(col_map, ["mes_referencia", "me_referencia", "mes", "no_mes"])
    col_natureza = first_present(col_map, ["natureza", "no_natureza_conta", "ds_natureza_conta"])
    col_saldo = first_present(col_map, ["vl_saldo_final", "saldo", "saldo_em_reais", "valor"])

    if not all([col_conta, col_mes, col_natureza, col_saldo]):
        raise ValueError(f"Estrutura inesperada do Siconfi. Colunas recebidas: {list(df.columns)}")

    df[col_conta] = df[col_conta].astype(str).str.strip()
    df = df[df[col_conta].str.startswith("1.1.1")].copy()

    out = pd.DataFrame(
        {
            "Mês": pd.to_numeric(df[col_mes], errors="coerce"),
            "Código Contábil": df[col_conta],
            "Natureza": df[col_natureza].astype(str).str.strip(),
            "Saldo em Reais": pd.to_numeric(df[col_saldo], errors="coerce").fillna(0),
        }
    ).dropna(subset=["Mês"])

    out["Mês"] = out["Mês"].astype(int)
    out = (
        out.groupby(["Mês", "Código Contábil", "Natureza"], as_index=False)["Saldo em Reais"]
        .sum()
        .sort_values(["Mês", "Código Contábil", "Natureza"])
    )
    return out


def export_outputs(caged: pd.DataFrame, siconfi: pd.DataFrame) -> None:
    out_caged = BASE_DIR / "caged_botucatu_q1_2026.csv"
    out_fin = BASE_DIR / "financas_botucatu_2026.csv"
    out_caged_app = BASE_DIR / "relatorio_botucatu_q1_2026.csv"
    out_fin_app = BASE_DIR / "investimentos_botucatu_2026.csv"

    caged.to_csv(out_caged, sep=";", encoding="utf-8-sig", index=False)
    siconfi.to_csv(out_fin, sep=";", encoding="utf-8-sig", index=False, decimal=",")

    # Compatibilidade com app Streamlit já criado no projeto
    caged.to_csv(out_caged_app, sep=";", encoding="utf-8-sig", index=False)
    fin_app = siconfi.rename(
        columns={
            "Mês": "Mes",
            "Código Contábil": "Codigo_Contabil",
            "Saldo em Reais": "Saldo_em_Reais",
        }
    )
    fin_app.to_csv(out_fin_app, sep=";", encoding="utf-8-sig", index=False, decimal=",")

    log(f"Exportação CAGED concluída: {out_caged.name} ({len(caged)} linhas)")
    log(f"Exportação Finanças concluída: {out_fin.name} ({len(siconfi)} linhas)")
    log("Arquivos de compatibilidade do app também foram gerados na raiz do projeto.")


def main() -> None:
    start = time.time()
    log("Pipeline Botucatu iniciado.")
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    try:
        caged = run_caged_etl()
        siconfi = run_siconfi_etl()
        export_outputs(caged, siconfi)
        elapsed = time.time() - start
        log(f"Pipeline finalizado com sucesso em {elapsed:.1f}s.")
    finally:
        # Garantia final de limpeza do diretório temporário
        if WORK_DIR.exists():
            shutil.rmtree(WORK_DIR, ignore_errors=True)
            log("Diretório temporário removido.")


if __name__ == "__main__":
    main()
