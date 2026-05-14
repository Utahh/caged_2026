"""
ETL Cadastro CNPJ (Receita Federal — dados abertos) filtrado para Botucatu (IBGE 3507506).

Gera agregados para MEI (opções/exclusões no Simples), porte das empresas e CNAE (divisão)
cruzado com tipo (MEI x demais portes).

Uso típico (local ou runner com disco/rede):
  set PIPELINE_INCLUDE_CNPJ=1
  python pipeline_botucatu.py

Fonte padrão: `https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj/{AAAA-MM}/`.

Join principal (microdados públicos): **Estabelecimentos\*.zip** (endereço e município no cadastro) +
**Empresas\*.zip** (porte) + **Simples.zip** (MEI). **Municipios.zip** é lido só para mapear o código de
município interno da RFB ao alvo IBGE (Botucatu).

Sobrescreva a pasta com `CNPJ_BASE_URL` (barra final) ou `CNPJ_DADOS_ABERTOS_REF` (ex.: `2026-03`).
Espelhos: `CNPJ_MIRROR_BASE_URL` + `CNPJ_TRY_MIRROR_FALLBACK`.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "tmp_cnpj_botucatu"
BOTUCATU_IBGE_7 = "3507506"
# A coluna "município" dos Estabelecimentos usa o código interno da tabela Municipios.zip (ex.: 6249), não o IBGE de 7 dígitos.


def _subtract_months(y: int, m: int, n: int) -> Tuple[int, int]:
    for _ in range(n):
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
    return y, m


def default_cnpj_open_data_ref() -> str:
    """Competência mensal sugerida (mês civil atual menos 2 — atraso típico da publicação RFB)."""
    y, m = _subtract_months(date.today().year, date.today().month, 2)
    return f"{y}-{m:02d}"


def resolve_cnpj_base_url() -> str:
    """
    URL da pasta que contém Municipios.zip, Estabelecimentos*.zip, Empresas*.zip, Simples.zip.

    Ordem: `CNPJ_BASE_URL` (pasta completa) > `https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj/{CNPJ_DADOS_ABERTOS_REF}/`
    """
    explicit = os.environ.get("CNPJ_BASE_URL", "").strip()
    if explicit:
        return explicit if explicit.endswith("/") else explicit + "/"
    ref = os.environ.get("CNPJ_DADOS_ABERTOS_REF", "").strip() or default_cnpj_open_data_ref()
    return f"https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj/{ref}/"


def log(msg: str) -> None:
    print(f"[CNPJ] {msg}", flush=True)


def normalize_col_map(columns: Iterable[str]) -> Dict[str, str]:
    mapped: Dict[str, str] = {}
    for c in columns:
        raw = str(c).strip().lower()
        ascii_key = (
            unicodedata.normalize("NFKD", raw)
            .encode("ascii", "ignore")
            .decode("ascii")
            .strip()
            .lower()
        )
        mapped[ascii_key] = str(c).strip()
    return mapped


def first_column_present(col_map: Dict[str, str], candidates: List[str]) -> Optional[str]:
    for key in candidates:
        if key in col_map:
            return col_map[key]
    return None


def simples_csv_first_row_looks_like_header(csv_path: Path) -> bool:
    """Layout novo da RFB traz cabeçalho com nomes de campo; o legado começa direto com CNPJ numérico."""
    with csv_path.open("r", encoding="latin-1", errors="ignore") as f:
        line = f.readline()
    if not line.strip():
        return False
    cell0 = line.split(";")[0].strip()
    if any(ch.isalpha() for ch in cell0):
        return True
    low = cell0.lower()
    return "cnpj" in low


def norm_cnpj_basico(v: object) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    return d[:8].zfill(8) if d else ""


def norm_ibge_municipio(v: object) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    if not d:
        return ""
    return d.zfill(7)[-7:]


def fetch_codigos_municipio_botucatu(work_dir: Path, base_url: str) -> Set[str]:
    """Lê Municipios.zip da RFB e devolve códigos a filtrar (interno + IBGE)."""
    out: Set[str] = set()
    url = f"{base_url.rstrip('/')}/Municipios.zip"
    zpath = work_dir / "Municipios.zip"
    log("Baixando Municipios.zip (tabela de códigos de município da RFB)…")
    download_zip(url, zpath)
    try:
        with zipfile.ZipFile(zpath, "r") as z:
            member = first_member_csv(z)
            text = z.read(member).decode("latin-1", errors="ignore")
    finally:
        try:
            zpath.unlink()
        except OSError:
            pass
    for line in text.splitlines():
        if "BOTUCATU" in line.upper():
            parts = line.split(";")
            if parts:
                internal = re.sub(r"\D", "", parts[0])
                if internal:
                    out.add(internal)
            break
    out.update({"3507506", "350750"})
    log(f"Códigos de município usados no filtro Botucatu: {sorted(out)}")
    return out


def matches_municipio_codigos(raw: object, codigos: Set[str]) -> bool:
    """True se o valor for exatamente um dos códigos (evita casar 6249 dentro do CNPJ)."""
    d = re.sub(r"\D", "", str(raw or ""))
    if not d:
        return False
    targets = {re.sub(r"\D", "", str(x)) for x in codigos if str(x).strip()}
    if d in targets:
        return True
    n7 = norm_ibge_municipio(d)
    for c in targets:
        if len(c) >= 6 and norm_ibge_municipio(c) == n7:
            return True
    return False


def detect_municipio_column_index(csv_path: Path, codigos: Set[str], max_lines: int = 800_000) -> Optional[int]:
    """Varre linhas até achar uma célula com município de Botucatu; retorna índice 0-based."""
    with csv_path.open("r", encoding="latin-1", errors="ignore") as f:
        for ln, line in enumerate(f):
            if ln >= max_lines:
                break
            parts = line.rstrip("\r\n").split(";")
            for i, p in enumerate(parts):
                if matches_municipio_codigos(p, codigos):
                    return i
    return None


def estabelecimento_usecols_from_municipio_idx(mi: int) -> List[int]:
    """
    Layout legado RFB (Estabelecimentos): município coluna 20.
    Se o arquivo tiver colunas extras após a posição 3, o bloco desloca: idx_novo = idx_velho + (mi-20) para idx_velho >= 4.
    """
    shift = mi - 20
    if mi < 10 or mi > 40:
        shift = 0
        mi = 20

    def ix(k: int) -> int:
        return k if k < 4 else k + shift

    cols = [ix(0), ix(1), ix(2), ix(3), ix(5), ix(10), ix(11), ix(19)]
    cols.append(int(mi))
    return cols


def parse_rf_date(series: pd.Series) -> pd.Series:
    def _one(x: object) -> Optional[pd.Timestamp]:
        s = str(x).strip() if x is not None else ""
        if not s or s.lower() in ("nan", "none", "nat"):
            return None
        if s in ("0", "00000000", "0000000"):
            return None
        if len(s) >= 8 and s[:8].isdigit():
            try:
                return pd.Timestamp(s[:8], format="%Y%m%d")
            except Exception:
                return None
        return None

    return series.map(_one)


def download_zip(
    url: str,
    dest: Path,
    timeout_connect: int = 45,
    timeout_read: int = 900,
    attempts: int = 5,
) -> None:
    """Baixa ZIP com retentativas (portal RFB costuma ser lento ou instável)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "observatorio-botucatu/1.0 (dados publicos CNPJ receita federal)"}
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            with requests.get(
                url,
                stream=True,
                timeout=(timeout_connect, timeout_read),
                headers=headers,
            ) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
            return
        except (requests.RequestException, OSError) as exc:
            last_exc = exc
            log(f"Download {attempt}/{attempts} falhou ({url[:96]}): {exc}")
            if attempt < attempts:
                time.sleep(min(20, 4 * attempt))
    raise RuntimeError(f"Download após {attempts} tentativas: {url}") from last_exc


def first_member_csv(z: zipfile.ZipFile) -> str:
    for n in z.namelist():
        if n.endswith("/"):
            continue
        low = n.lower()
        if low.endswith(".csv") or "estabele" in low or "empresa" in low or "simples" in low:
            return n
    return z.namelist()[0]


def iter_estabelecimentos_botucatu(
    zip_path: Path,
    codigos_municipio: Set[str],
    usecols: List[int],
    chunksize: int = 200_000,
) -> Iterable[pd.DataFrame]:
    """Yields chunks já filtrados pelo município IBGE (7 dígitos)."""
    names = [
        "cnpj_basico",
        "cnpj_ordem",
        "cnpj_dv",
        "identificador_matriz_filial",
        "situacao_cadastral",
        "data_inicio_atividade",
        "cnae_fiscal_principal",
        "uf",
        "municipio",
    ]
    tmp_csv = zip_path.with_suffix(".extracted.csv")
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            member = first_member_csv(z)
            tmp_csv.write_bytes(z.read(member))
        reader = pd.read_csv(
            tmp_csv,
            sep=";",
            header=None,
            names=names,
            usecols=usecols,
            dtype=str,
            encoding="latin-1",
            chunksize=chunksize,
            low_memory=False,
        )
        for chunk in reader:
            sub = chunk[chunk["municipio"].map(lambda x: matches_municipio_codigos(x, codigos_municipio))].copy()
            if not sub.empty:
                sub["cnpj_basico"] = sub["cnpj_basico"].map(norm_cnpj_basico)
                yield sub
    finally:
        try:
            if tmp_csv.exists():
                tmp_csv.unlink()
        except OSError:
            pass


def _resolve_empresas_header_columns(columns: Iterable[str]) -> Optional[Tuple[str, str]]:
    cm = normalize_col_map(columns)
    c0 = first_column_present(cm, ["cnpj_basico", "cnpj", "nmcnpjbasico", "nr_cnpj_basico"])
    c1 = first_column_present(
        cm,
        ["porte_empresa", "porte", "cod_porte", "porteempresa", "porte_empressa"],
    )
    if c0 and c1:
        return (c0, c1)
    return None


def _resolve_empresas_razao_social_column(columns: Iterable[str]) -> Optional[str]:
    cm = normalize_col_map(columns)
    return first_column_present(
        cm,
        [
            "razao_social",
            "razaosocial",
            "nome_empresarial",
            "nomeempresarial",
            "nome_empresa",
        ],
    )


def load_empresas_for_bases(
    work_dir: Path, base_url: str, bases: Set[str], digits_needed: Set[str]
) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    usecols_legacy = [0, 1, 5]
    for d in sorted(digits_needed):
        url = f"{base_url.rstrip('/')}/Empresas{d}.zip"
        zpath = work_dir / f"Empresas{d}.zip"
        log(f"Baixando Empresas{d}.zip …")
        download_zip(url, zpath)
        tmp_e = zpath.with_suffix(".extracted.csv")
        try:
            with zipfile.ZipFile(zpath, "r") as z:
                member = first_member_csv(z)
                tmp_e.write_bytes(z.read(member))
            hdr = None
            if simples_csv_first_row_looks_like_header(tmp_e):
                peek = pd.read_csv(tmp_e, sep=";", header=0, nrows=2, dtype=str, encoding="latin-1")
                hdr = _resolve_empresas_header_columns(peek.columns)
                if hdr:
                    log(f"Empresas{d}: layout com cabeçalho (colunas {hdr[0]}, {hdr[1]}).")
            if hdr:
                c0, c1 = hdr
                c_raz = _resolve_empresas_razao_social_column(peek.columns)
                for chunk in pd.read_csv(
                    tmp_e,
                    sep=";",
                    header=0,
                    dtype=str,
                    encoding="latin-1",
                    chunksize=300_000,
                    low_memory=False,
                ):
                    if c_raz and c_raz in chunk.columns:
                        sub = chunk[[c0, c_raz, c1]].rename(
                            columns={c0: "cnpj_basico", c_raz: "razao_social", c1: "porte_empresa"}
                        )
                    else:
                        sub = chunk[[c0, c1]].rename(columns={c0: "cnpj_basico", c1: "porte_empresa"})
                        sub["razao_social"] = ""
                    sub["cnpj_basico"] = sub["cnpj_basico"].map(norm_cnpj_basico)
                    sub["razao_social"] = sub["razao_social"].fillna("").astype(str).str.strip()
                    hit = sub[sub["cnpj_basico"].isin(bases)]
                    if not hit.empty:
                        frames.append(hit.drop_duplicates("cnpj_basico"))
            else:
                if simples_csv_first_row_looks_like_header(tmp_e):
                    log(f"Empresas{d}: cabeçalho não mapeado; usando posições legadas 0, 1 e 5 (CNPJ, razão, porte).")
                for chunk in pd.read_csv(
                    tmp_e,
                    sep=";",
                    header=None,
                    usecols=usecols_legacy,
                    names=["cnpj_basico", "razao_social", "porte_empresa"],
                    dtype=str,
                    encoding="latin-1",
                    chunksize=300_000,
                    low_memory=False,
                ):
                    chunk["cnpj_basico"] = chunk["cnpj_basico"].map(norm_cnpj_basico)
                    chunk["razao_social"] = chunk["razao_social"].fillna("").astype(str).str.strip()
                    hit = chunk[chunk["cnpj_basico"].isin(bases)]
                    if not hit.empty:
                        frames.append(hit.drop_duplicates("cnpj_basico"))
        finally:
            try:
                if tmp_e.exists():
                    tmp_e.unlink()
            except OSError:
                pass
        try:
            zpath.unlink()
        except OSError:
            pass
    if not frames:
        return pd.DataFrame(columns=["cnpj_basico", "razao_social", "porte_empresa"])
    return pd.concat(frames, ignore_index=True).drop_duplicates("cnpj_basico")


def _resolve_simples_header_columns(columns: Iterable[str]) -> Optional[Dict[str, str]]:
    cm = normalize_col_map(columns)
    c0 = first_column_present(cm, ["cnpj_basico", "cnpj", "nmcnpjbasico", "nr_cnpj_basico"])
    c_opt = first_column_present(
        cm,
        ["opcao_pelo_mei", "opcao_mei", "mei", "opcao_mei_simples", "ind_opcao_mei"],
    )
    c_do = first_column_present(
        cm,
        [
            "data_opcao_mei",
            "data_opcao_pelo_mei",
            "dt_opcao_mei",
            "dataopcao_mei",
            "dtopcao_mei",
        ],
    )
    c_de = first_column_present(
        cm,
        [
            "data_exclusao_mei",
            "data_exclusao_do_mei",
            "dt_exclusao_mei",
            "dataexclusao_mei",
            "dtexclusao_mei",
        ],
    )
    if not (c0 and c_opt and c_do and c_de):
        return None
    return {"cnpj_basico": c0, "opcao_mei": c_opt, "data_opcao_mei": c_do, "data_exclusao_mei": c_de}


def load_simples_for_bases(work_dir: Path, base_url: str, bases: Set[str]) -> pd.DataFrame:
    url = f"{base_url.rstrip('/')}/Simples.zip"
    zpath = work_dir / "Simples.zip"
    log("Baixando Simples.zip …")
    download_zip(url, zpath)
    names = ["cnpj_basico", "opcao_mei", "data_opcao_mei", "data_exclusao_mei"]
    frames: List[pd.DataFrame] = []
    tmp_s = zpath.with_suffix(".extracted.csv")
    try:
        with zipfile.ZipFile(zpath, "r") as z:
            member = first_member_csv(z)
            tmp_s.write_bytes(z.read(member))

        hdr_map: Optional[Dict[str, str]] = None
        if simples_csv_first_row_looks_like_header(tmp_s):
            peek = pd.read_csv(tmp_s, sep=";", header=0, nrows=2, dtype=str, encoding="latin-1")
            hdr_map = _resolve_simples_header_columns(peek.columns)
            if hdr_map:
                log("Simples: layout com cabeçalho (dados abertos RFB / arquivo nomeado).")

        if hdr_map:
            c0, co, cd, ce = (
                hdr_map["cnpj_basico"],
                hdr_map["opcao_mei"],
                hdr_map["data_opcao_mei"],
                hdr_map["data_exclusao_mei"],
            )
            for chunk in pd.read_csv(
                tmp_s,
                sep=";",
                header=0,
                dtype=str,
                encoding="latin-1",
                chunksize=500_000,
                low_memory=False,
            ):
                sub = chunk[[c0, co, cd, ce]].rename(
                    columns={c0: "cnpj_basico", co: "opcao_mei", cd: "data_opcao_mei", ce: "data_exclusao_mei"}
                )
                sub["cnpj_basico"] = sub["cnpj_basico"].map(norm_cnpj_basico)
                hit = sub[sub["cnpj_basico"].isin(bases)]
                if not hit.empty:
                    frames.append(hit.drop_duplicates("cnpj_basico"))
        elif simples_csv_first_row_looks_like_header(tmp_s):
            log(
                "Aviso: Simples com cabeçalho mas colunas MEI não mapeadas (nomes diferentes do esperado). "
                "Veja LAYOUT_DADOS_ABERTOS_CNPJ.pdf na pasta oficial da RFB ou use CNPJ_BASE_URL apontando para ZIP compatível."
            )
        else:
            log("Simples: layout posicional legado (sem cabeçalho; colunas 0,4,5,6).")
            for chunk in pd.read_csv(
                tmp_s,
                sep=";",
                header=None,
                usecols=[0, 4, 5, 6],
                names=names,
                dtype=str,
                encoding="latin-1",
                chunksize=500_000,
                low_memory=False,
            ):
                chunk["cnpj_basico"] = chunk["cnpj_basico"].map(norm_cnpj_basico)
                hit = chunk[chunk["cnpj_basico"].isin(bases)]
                if not hit.empty:
                    frames.append(hit.drop_duplicates("cnpj_basico"))
    finally:
        try:
            if tmp_s.exists():
                tmp_s.unlink()
        except OSError:
            pass
    try:
        zpath.unlink()
    except OSError:
        pass
    if not frames:
        return pd.DataFrame(columns=names)
    return pd.concat(frames, ignore_index=True).drop_duplicates("cnpj_basico")


def pick_representative_estabelecimento(df: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por cnpj_basico: prioriza matriz (1) no município."""
    df = df.copy()
    df["matriz_flag"] = df["identificador_matriz_filial"].astype(str).str.strip().eq("1").astype(int)
    df = df.sort_values(["cnpj_basico", "matriz_flag", "cnpj_ordem"], ascending=[True, False, True])
    return df.drop_duplicates("cnpj_basico", keep="first").drop(columns=["matriz_flag"], errors="ignore")


def porte_label(code: object) -> str:
    s = str(code).strip() if code is not None else ""
    if not s or s.lower() == "nan":
        return "Porte não informado"
    # Layout comum: 01 ME, 03 EPP, 05 Demais (também aceita inteiros 1,3,5)
    if s.isdigit():
        s = s.zfill(2)
    mapping = {
        "01": "ME (Microempresa)",
        "02": "MEI (campo porte)",
        "03": "EPP",
        "04": "MEI (campo porte alt.)",
        "05": "Demais portes",
        "00": "Não informado",
        "1": "ME (Microempresa)",
        "2": "MEI (campo porte)",
        "3": "EPP",
        "4": "MEI (campo porte alt.)",
        "5": "Demais portes",
    }
    return mapping.get(s, f"Porte código {s}")


JOIN_EMPRESAS_CSV_COLUMNS = [
    "municipio_ibge",
    "municipio_nome",
    "cnpj_basico",
    "razao_social",
    "cnpj",
    "identificador_matriz_filial",
    "situacao_cadastral",
    "situacao_cadastral_descricao",
    "data_inicio_atividade",
    "cnae_fiscal_principal",
    "cnae_subclasse",
    "divisao_cnae",
    "divisao_descricao",
    "uf",
    "municipio_codigo_rfb_estabelecimento",
    "qtd_estabelecimentos_municipio",
    "porte_empresa",
    "porte_cadastral_descricao",
    "opcao_mei",
    "data_opcao_mei",
    "data_exclusao_mei",
    "mei_simples_vigente",
    "mei_ativo",
    "mei_inativo_cnpj",
    "tipo_empresa",
]


def municipio_fonte_dataframe(
    municipio_ibge: str,
    municipio_nome: str,
    codigos_filtro: Set[str],
    base_url: str,
) -> pd.DataFrame:
    """Explica como o município entra no pipeline (Municipios.zip + coluna Estabelecimentos)."""
    return pd.DataFrame(
        [
            {
                "campo": "URL base (pasta AAAA-MM)",
                "valor": base_url.rstrip("/"),
            },
            {
                "campo": "Município alvo (IBGE 7 dígitos)",
                "valor": municipio_ibge,
            },
            {
                "campo": "Nome de referência",
                "valor": municipio_nome,
            },
            {
                "campo": "Códigos usados no filtro da coluna municipio (Estabelecimentos)",
                "valor": ", ".join(sorted(codigos_filtro)),
            },
            {
                "campo": "Papel de Municipios.zip",
                "valor": (
                    "Arquivo oficial da RFB com códigos de município do cadastro CNPJ; "
                    "localizamos a linha do nome e usamos o código interno para casar com a coluna "
                    "`municipio` dos estabelecimentos (nem sempre é o IBGE de 7 dígitos)."
                ),
            },
            {
                "campo": "Join por empresa (raiz)",
                "valor": (
                    "Agrupamos estabelecimentos no município, escolhemos um representante por "
                    "`cnpj_basico` (prioriza matriz), cruzamos Empresas (porte) e Simples (MEI) pela mesma chave."
                ),
            },
        ]
    )


def _mei_opcao_sim_series(opcao: pd.Series) -> pd.Series:
    s = opcao.fillna("N").astype(str).str.strip().str.upper()
    return s.isin(["S", "SIM", "1", "Y", "YES"])


def build_join_empresas_export(
    merged: pd.DataFrame,
    estab_all: pd.DataFrame,
    cref: pd.DataFrame,
    municipio_ibge: str,
    municipio_nome: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Uma linha por cnpj_basico: join Estabelecimentos (rep.) + Empresas + Simples + CNAE divisão.
    Retorna (todas as empresas do município, subset com opção pelo MEI no Simples).
    """
    estab_counts = (
        estab_all.groupby("cnpj_basico", as_index=False)
        .size()
        .rename(columns={"size": "qtd_estabelecimentos_municipio"})
    )
    jb = merged.merge(estab_counts, on="cnpj_basico", how="left")
    if not cref.empty:
        jb = jb.merge(cref, left_on="cnae_subclasse", right_on="subclasse", how="left").drop(
            columns=["subclasse"], errors="ignore"
        )
    else:
        jb = jb.copy()
        jb["divisao_cnae"] = ""
        jb["divisao_descricao"] = ""

    jb["municipio_ibge"] = municipio_ibge
    jb["municipio_nome"] = municipio_nome
    if "razao_social" not in jb.columns:
        jb["razao_social"] = ""
    else:
        jb["razao_social"] = jb["razao_social"].fillna("").astype(str).str.strip()
    ord_ = jb["cnpj_ordem"].astype(str).str.strip().str.replace(r"\D", "", regex=True).str.zfill(4)
    dv = jb["cnpj_dv"].astype(str).str.strip().str.replace(r"\D", "", regex=True).str.zfill(2)
    jb["cnpj"] = jb["cnpj_basico"].astype(str).str.strip() + ord_ + dv
    jb["situacao_cadastral_descricao"] = jb["situacao_cadastral"].map(situacao_label)
    jb["porte_cadastral_descricao"] = jb["porte_empresa"].map(porte_label)

    di = pd.to_datetime(jb["data_inicio_atividade"], format="%Y%m%d", errors="coerce")
    jb["data_inicio_atividade"] = di.dt.strftime("%Y-%m-%d").fillna(jb["data_inicio_atividade"].astype(str))

    jb["data_opcao_mei"] = pd.to_datetime(jb["data_opcao_mei"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    jb["data_exclusao_mei"] = pd.to_datetime(jb["data_exclusao_mei"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")

    jb["mei_simples_vigente"] = jb["mei_simples_vigente"].map(lambda x: "1" if x else "0")
    jb["mei_ativo"] = jb["mei_ativo"].map(lambda x: "1" if x else "0")
    jb["mei_inativo_cnpj"] = jb["mei_inativo_cnpj"].map(lambda x: "1" if x else "0")

    jb2 = jb.rename(columns={"municipio": "municipio_codigo_rfb_estabelecimento"})
    cols = [c for c in JOIN_EMPRESAS_CSV_COLUMNS if c in jb2.columns]
    out = jb2[cols].copy()
    mei_mask = _mei_opcao_sim_series(jb["opcao_mei"])
    meis = out.loc[mei_mask].copy()
    return out, meis


def situacao_label(code: object) -> str:
    s = str(code).strip() if code is not None else ""
    if s == "2":
        return "Ativa"
    if s == "1":
        return "Nula"
    if s == "3":
        return "Suspensa"
    if s == "4":
        return "Inapta"
    if s == "8":
        return "Baixada"
    return f"Situação {s}" if s else "Sem código"


def fetch_cnae_subclasses_reference() -> pd.DataFrame:
    import requests as rq

    url = "https://servicodados.ibge.gov.br/api/v2/cnae/subclasses"
    r = rq.get(url, timeout=90)
    r.raise_for_status()
    payload = r.json()
    rows = []
    for item in payload if isinstance(payload, list) else []:
        classe = item.get("classe", {}) or {}
        grupo = classe.get("grupo", {}) or {}
        divisao = grupo.get("divisao", {}) or {}
        sc = str(item.get("id", "")).strip()
        digits = "".join(ch for ch in sc if ch.isdigit())
        sub = digits.zfill(7) if digits else ""
        rows.append(
            {
                "subclasse": sub,
                "divisao_cnae": str(divisao.get("id", "")).strip(),
                "divisao_descricao": str(divisao.get("descricao", "")).strip(),
            }
        )
    return pd.DataFrame(rows).drop_duplicates("subclasse")


def norm_cnae_subclasse(v: object) -> str:
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d.zfill(7) if d else ""


def cnpj_download_base_candidates(user_base: Optional[str]) -> List[str]:
    """
    Ordem de tentativa: URL explícita (CNPJ_BASE_URL); portal oficial RFB (pasta AAAA-MM);
    espelho opcional (somente se `CNPJ_MIRROR_BASE_URL` estiver definido e `CNPJ_TRY_MIRROR_FALLBACK` ativo).
    """
    if (user_base or "").strip():
        u = (user_base or "").strip()
        return [u if u.endswith("/") else u + "/"]
    out: List[str] = [resolve_cnpj_base_url()]
    if os.environ.get("CNPJ_TRY_MIRROR_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off"):
        m = os.environ.get("CNPJ_MIRROR_BASE_URL", "").strip()
        if m:
            out.append(m if m.endswith("/") else m + "/")
    return out


def run_cnpj_botucatu_etl(
    base_url: Optional[str] = None,
    municipio_ibge: str = BOTUCATU_IBGE_7,
    municipio_nome: str = "Botucatu",
) -> Dict[str, pd.DataFrame]:
    """
    Executa download + agregação. Tenta, em sequência, a URL configurada (se houver), o portal oficial
    da RFB e, se configurado, uma segunda base (`CNPJ_MIRROR_BASE_URL`).
    """
    last_exc: Optional[BaseException] = None
    for u in cnpj_download_base_candidates(base_url):
        try:
            return _run_cnpj_botucatu_etl_at(u, municipio_ibge, municipio_nome)
        except Exception as exc:
            last_exc = exc
            log(f"Aviso: falha nesta base de arquivos; tentando próxima se houver: {exc}")
    raise RuntimeError(
        "CNPJ: nenhuma URL de download funcionou (rede, firewall ou pasta inexistente). "
        "Defina CNPJ_BASE_URL manualmente ou verifique CNPJ_DADOS_ABERTOS_REF."
    ) from last_exc


def _run_cnpj_botucatu_etl_at(
    resolved_url: str,
    municipio_ibge: str = BOTUCATU_IBGE_7,
    municipio_nome: str = "Botucatu",
) -> Dict[str, pd.DataFrame]:
    """Implementação: `resolved_url` já é a pasta base dos ZIPs."""
    t0 = time.time()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    log(f"Início ETL CNPJ — município {municipio_ibge} — base {resolved_url}")

    codigos_municipio = fetch_codigos_municipio_botucatu(WORK_DIR, resolved_url)

    estab_parts: List[pd.DataFrame] = []
    usecols_est: Optional[List[int]] = None
    for i in range(10):
        url = f"{resolved_url.rstrip('/')}/Estabelecimentos{i}.zip"
        zpath = WORK_DIR / f"Estabelecimentos{i}.zip"
        log(f"Baixando Estabelecimentos{i}.zip …")
        download_zip(url, zpath)
        if usecols_est is None:
            probe = zpath.with_suffix(".layout_probe.csv")
            try:
                with zipfile.ZipFile(zpath, "r") as z:
                    probe.write_bytes(z.read(first_member_csv(z)))
                mi_det = detect_municipio_column_index(probe, codigos_municipio)
                if mi_det is None:
                    log(
                        "Município Botucatu não encontrado na amostra inicial; "
                        "assumindo layout legado (município coluna 21 em layout 0-based índice 20)."
                    )
                    mi_det = 20
                else:
                    log(f"Coluna de município detectada no índice {mi_det} (0-based).")
                usecols_est = estabelecimento_usecols_from_municipio_idx(mi_det)
                log(f"usecols estabelecimento: {usecols_est}")
            finally:
                try:
                    if probe.exists():
                        probe.unlink()
                except OSError:
                    pass
        nrows = 0
        for ch in iter_estabelecimentos_botucatu(zpath, codigos_municipio, usecols_est):
            estab_parts.append(ch)
            nrows += len(ch)
        log(f"Estabelecimentos{i}: {nrows} linhas em Botucatu (acumulado parcial).")
        try:
            zpath.unlink()
        except OSError:
            pass

    if not estab_parts:
        log("Nenhum estabelecimento encontrado para o município — abortando agregações.")
        empty_join = pd.DataFrame(columns=JOIN_EMPRESAS_CSV_COLUMNS)
        return {
            "resumo": pd.DataFrame(
                [
                    {
                        "ref_data_extracao": pd.Timestamp.utcnow().strftime("%Y-%m-%d"),
                        "fonte_url": resolved_url,
                        "municipio_ibge": municipio_ibge,
                        "municipio_nome": municipio_nome,
                        "total_empresas": 0,
                        "total_estabelecimentos": 0,
                        "mei_ativos": 0,
                        "mei_inativos_cnpj": 0,
                        "mei_opcao_sem_exclusao": 0,
                    }
                ]
            ),
            "mei_mensal": pd.DataFrame(columns=["ano_mes", "aberturas_mei", "exclusoes_mei"]),
            "porte_pct": pd.DataFrame(columns=["tipo_empresa", "quantidade", "percentual"]),
            "cnae_x_tipo": pd.DataFrame(
                columns=["tipo_empresa", "divisao_cnae", "divisao_descricao", "quantidade", "percentual_no_tipo"]
            ),
            "join_empresas": empty_join,
            "meis": empty_join,
            "municipio_fonte": municipio_fonte_dataframe(
                municipio_ibge, municipio_nome, codigos_municipio, resolved_url
            ),
        }

    estab_all = pd.concat(estab_parts, ignore_index=True)
    total_estab = len(estab_all)
    rep = pick_representative_estabelecimento(estab_all)
    bases: Set[str] = set(rep["cnpj_basico"].tolist())
    digits_needed = {b[-1] for b in bases if len(b) == 8}

    emp = load_empresas_for_bases(WORK_DIR, resolved_url, bases, digits_needed)
    sim = load_simples_for_bases(WORK_DIR, resolved_url, bases)

    merged = rep.merge(emp, on="cnpj_basico", how="left").merge(sim, on="cnpj_basico", how="left")
    if "razao_social" not in merged.columns:
        merged["razao_social"] = ""
    else:
        merged["razao_social"] = merged["razao_social"].fillna("").astype(str).str.strip()
    merged["opcao_mei"] = merged["opcao_mei"].fillna("N").astype(str).str.strip().str.upper()
    _mei_sim = merged["opcao_mei"].isin(["S", "SIM", "1", "Y", "YES"])
    merged["data_opcao_mei"] = parse_rf_date(merged["data_opcao_mei"])
    merged["data_exclusao_mei"] = parse_rf_date(merged["data_exclusao_mei"])
    merged["situacao_cadastral"] = merged["situacao_cadastral"].fillna("").astype(str).str.strip()
    merged["cnae_subclasse"] = merged["cnae_fiscal_principal"].map(norm_cnae_subclasse)

    # MEI vigente no Simples (sem data de exclusão)
    merged["mei_simples_vigente"] = _mei_sim & merged["data_exclusao_mei"].isna()
    merged["mei_ativo"] = merged["mei_simples_vigente"] & merged["situacao_cadastral"].eq("2")
    merged["mei_inativo_cnpj"] = merged["mei_simples_vigente"] & ~merged["situacao_cadastral"].eq("2")

    # Tipo para gráficos de porte / donut
    base_porte = merged["porte_empresa"].map(porte_label)
    merged["tipo_empresa"] = base_porte.astype(str)
    merged.loc[merged["mei_ativo"], "tipo_empresa"] = "MEI (ativo)"
    merged.loc[merged["mei_simples_vigente"] & merged["mei_inativo_cnpj"], "tipo_empresa"] = "MEI (inativo no CNPJ)"

    total_empresas = len(merged)
    mei_ativos = int(merged["mei_ativo"].sum())
    mei_inativos = int(merged["mei_inativo_cnpj"].sum())
    mei_opcao_sem_exclusao = int(merged["mei_simples_vigente"].sum())

    # Movimento mensal MEI (datas do Simples) — garantir datetime antes de .dt
    opt_dt = pd.to_datetime(merged["data_opcao_mei"], errors="coerce")
    excl_dt = pd.to_datetime(merged["data_exclusao_mei"], errors="coerce")
    opt_cnt = opt_dt[opt_dt.notna()].dt.to_period("M").value_counts().sort_index()
    excl_cnt = excl_dt[excl_dt.notna()].dt.to_period("M").value_counts().sort_index()
    all_months = sorted(set(opt_cnt.index.tolist()) | set(excl_cnt.index.tolist()))
    mei_mensal = pd.DataFrame(
        {
            "ano_mes": [str(p) for p in all_months],
            "aberturas_mei": [int(opt_cnt.get(p, 0)) for p in all_months],
            "exclusoes_mei": [int(excl_cnt.get(p, 0)) for p in all_months],
        }
    )
    # Últimos 48 meses com algum evento
    if not mei_mensal.empty:
        mei_mensal = mei_mensal.tail(48)

    # % porte / tipo
    vc = merged["tipo_empresa"].value_counts()
    porte_pct = (
        pd.DataFrame({"tipo_empresa": vc.index.astype(str), "quantidade": vc.values})
        .assign(percentual=lambda d: (100.0 * d["quantidade"] / total_empresas).round(2))
        .sort_values("quantidade", ascending=False)
    )

    # CNAE divisão x tipo
    try:
        cref = fetch_cnae_subclasses_reference()
    except Exception as exc:
        log(f"Aviso: CNAE IBGE não carregado ({exc}); divisões ficam em branco.")
        cref = pd.DataFrame(columns=["subclasse", "divisao_cnae", "divisao_descricao"])

    if not cref.empty:
        m2 = merged.merge(cref, left_on="cnae_subclasse", right_on="subclasse", how="left")
    else:
        m2 = merged.copy()
        m2["divisao_cnae"] = ""
        m2["divisao_descricao"] = ""

    m2["divisao_descricao"] = m2["divisao_descricao"].fillna("Divisão não mapeada")
    m2["divisao_cnae"] = m2["divisao_cnae"].fillna("")

    rows_cnae = []
    for tipo, g in m2.groupby("tipo_empresa"):
        tot_t = len(g)
        if tot_t == 0:
            continue
        sub = (
            g.groupby(["divisao_cnae", "divisao_descricao"], as_index=False)
            .size()
            .rename(columns={"size": "quantidade"})
            .assign(tipo_empresa=tipo, percentual_no_tipo=lambda d: (100.0 * d["quantidade"] / tot_t).round(2))
            .sort_values("quantidade", ascending=False)
        )
        rows_cnae.append(sub)
    cnae_x_tipo = pd.concat(rows_cnae, ignore_index=True) if rows_cnae else pd.DataFrame()

    join_empresas, meis = build_join_empresas_export(merged, estab_all, cref, municipio_ibge, municipio_nome)
    municipio_fonte = municipio_fonte_dataframe(municipio_ibge, municipio_nome, codigos_municipio, resolved_url)
    log(
        f"Join export: {len(join_empresas)} empresas (raiz), {len(meis)} com opção pelo MEI no Simples; "
        "CSV: cnpj_botucatu_join_empresas.csv, cnpj_botucatu_meis.csv, cnpj_botucatu_municipio_fonte.csv"
    )

    resumo = pd.DataFrame(
        [
            {
                "ref_data_extracao": pd.Timestamp.utcnow().strftime("%Y-%m-%d"),
                "fonte_url": resolved_url,
                "municipio_ibge": municipio_ibge,
                "municipio_nome": municipio_nome,
                "total_empresas": total_empresas,
                "total_estabelecimentos": total_estab,
                "mei_ativos": mei_ativos,
                "mei_inativos_cnpj": mei_inativos,
                "mei_opcao_sem_exclusao": mei_opcao_sem_exclusao,
            }
        ]
    )

    elapsed = time.time() - t0
    log(f"ETL CNPJ concluído em {elapsed:.1f}s — empresas {total_empresas}, estabelecimentos {total_estab}.")

    return {
        "resumo": resumo,
        "mei_mensal": mei_mensal,
        "porte_pct": porte_pct,
        "cnae_x_tipo": cnae_x_tipo,
        "join_empresas": join_empresas,
        "meis": meis,
        "municipio_fonte": municipio_fonte,
    }


def export_cnpj_csvs(dfs: Dict[str, pd.DataFrame], out_dir: Optional[Path] = None) -> None:
    out = out_dir or BASE_DIR
    dfs["resumo"].to_csv(out / "cnpj_botucatu_resumo.csv", sep=";", index=False, encoding="utf-8-sig")
    dfs["mei_mensal"].to_csv(out / "cnpj_botucatu_mei_mensal.csv", sep=";", index=False, encoding="utf-8-sig")
    dfs["porte_pct"].to_csv(out / "cnpj_botucatu_porte_pct.csv", sep=";", index=False, encoding="utf-8-sig")
    dfs["cnae_x_tipo"].to_csv(out / "cnpj_botucatu_cnae_x_tipo.csv", sep=";", index=False, encoding="utf-8-sig")
    if "join_empresas" in dfs:
        dfs["join_empresas"].to_csv(out / "cnpj_botucatu_join_empresas.csv", sep=";", index=False, encoding="utf-8-sig")
    if "meis" in dfs:
        dfs["meis"].to_csv(out / "cnpj_botucatu_meis.csv", sep=";", index=False, encoding="utf-8-sig")
    if "municipio_fonte" in dfs:
        dfs["municipio_fonte"].to_csv(
            out / "cnpj_botucatu_municipio_fonte.csv", sep=";", index=False, encoding="utf-8-sig"
        )
    log(f"CSV exportados em {out}")


def cleanup_workdir() -> None:
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR, ignore_errors=True)


def main_cli() -> None:
    try:
        dfs = run_cnpj_botucatu_etl()
        export_cnpj_csvs(dfs)
    finally:
        cleanup_workdir()


if __name__ == "__main__":
    main_cli()
