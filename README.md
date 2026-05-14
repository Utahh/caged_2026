---
title: Observatório Botucatu
emoji: 📊
colorFrom: blue
colorTo: green
sdk: streamlit
app_file: app.py
pinned: false
---

# Observatório Botucatu (MVP)

Painel web interativo construído com Streamlit e Plotly para acompanhamento de dados econômicos e financeiros de Botucatu/SP.

## Estrutura sugerida do projeto

```text
.
├── app.py                          # Entry-point do Streamlit (deploy)
├── pipeline_botucatu.py            # Entry-point do ETL
├── cnpj_botucatu_etl.py           # ETL opcional CNPJ/MEI (Receita Federal, dados abertos)
├── data/                           # Espaço para datasets e artefatos
├── scripts/                        # Scripts operacionais (ex.: run_pipeline.py)
├── src/                            # Módulos reutilizáveis (expansão futura)
├── .github/workflows/              # CI/CD
└── .streamlit/                     # Configuração do Streamlit
```

Essa organização mantém compatibilidade total com o deploy atual, sem quebrar caminhos de execução existentes.

## Arquivos esperados

Mantenha estes arquivos na mesma pasta:

- `app.py`
- `requirements.txt`
- `relatorio_botucatu_q1_2026.csv`
- `investimentos_botucatu_2026.csv`

## Requisitos

- Python 3.10+ (recomendado)
- pip

## Como rodar localmente

1. Abra o terminal na pasta do projeto.
2. (Opcional) Crie e ative um ambiente virtual.
3. Instale as dependências:

```bash
pip install -r requirements.txt
```

4. Execute o app:

```bash
streamlit run app.py
```

5. Abra o endereço exibido no terminal (normalmente `http://localhost:8501`).

## Pipeline Mestre (extração automática)

Arquivo: `pipeline_botucatu.py`

Esse pipeline único executa em sequência:

1. **CAGED (FTP — Novo CAGED)**:
   - para cada competência `AAAA/MM`, baixa **na mesma execução** os arquivos disponíveis: `CAGEDMOV…7z` (obrigatório), `CAGEDFOR…7z` e `CAGEDEXC…7z` quando existirem na mesma pasta do FTP;
   - **Não soma** MOV de um download antigo com FOR de outro: tudo vem do **mesmo snapshot** da pasta `NOVO CAGED/AAAA/AAAAMM/`, evitando duplicar movimentos (o inflado “2 e −2” aparece quando se misturam versões ou se conta duas vezes o mesmo evento fora do modelo da RFB);
   - agrega **uma linha = um registro**; `saldomovimentacao` é somado sobre a união MOV ∪ FOR ∪ EXC; `admissao`/`demissao` derivam de `saldo == 1` / `saldo == -1`;
   - descompacta `.7z` com `py7zr`, lê em chunks de `100000` linhas, filtra município `350750` (Botucatu) e códigos do comparativo;
   - remove `.7z` e `.txt` após processar cada arquivo.

2. **Siconfi (API)**:
   - consulta endpoint `msc_patrimonial`;
   - filtra contas iniciadas por `1.1.1`;
   - gera tabela de finanças consolidada.

3. **Exporta**:
   - `caged_botucatu_q1_2026.csv`
   - `financas_botucatu_2026.csv`
   - e também os arquivos compatíveis com o app:
     - `relatorio_botucatu_q1_2026.csv`
     - `investimentos_botucatu_2026.csv`

4. **Cadastro CNPJ / MEI (opcional)** — módulo `cnpj_botucatu_etl.py`, acionado **somente** se existir a variável de ambiente `PIPELINE_INCLUDE_CNPJ=1`:
   - baixa os ZIPs públicos `Estabelecimentos0..9`, `Empresas*` e `Simples` da **Receita Federal** (`https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj/AAAA-MM/`, competência padrão = mês civil atual menos 2, ou `CNPJ_DADOS_ABERTOS_REF` / `CNPJ_BASE_URL`); ~vários GB;
   - lê `Simples` com **cabeçalho** (layout novo do portal) ou posicional (espelhos legados);
   - filtra estabelecimentos com município IBGE **3507506** (Botucatu);
   - cruza `Empresas` (porte) e `Simples` (MEI, datas de opção/exclusão);
   - gera na raiz do projeto: `cnpj_botucatu_resumo.csv`, `cnpj_botucatu_mei_mensal.csv`, `cnpj_botucatu_porte_pct.csv`, `cnpj_botucatu_cnae_x_tipo.csv`, além do **join por empresa** (`cnpj_botucatu_join_empresas.csv`), **somente MEI** (`cnpj_botucatu_meis.csv`) e **rastreio do filtro de município** (`cnpj_botucatu_municipio_fonte.csv`);
   - o job agendado do GitHub **não** inclui essa etapa por padrão (tempo e disco). Rode localmente ou em `workflow_dispatch` com a variável configurada no ambiente do runner, se desejar versionar esses CSVs.

```bash
set PIPELINE_INCLUDE_CNPJ=1
set CNPJ_DADOS_ABERTOS_REF=2026-03
python pipeline_botucatu.py
```

(Linux/macOS: `export …`. Opcional: `CNPJ_BASE_URL` com URL completa da pasta dos ZIPs, se usar CDN/espelho.)

Se `dadosabertos.rfb.gov.br` não responder, defina um espelho com a **mesma árvore de arquivos** (`Municipios.zip`, `Estabelecimentos*.zip`, etc.): `CNPJ_MIRROR_BASE_URL=https://…/pasta/` e mantenha `CNPJ_TRY_MIRROR_FALLBACK=1` (padrão). Sem `CNPJ_MIRROR_BASE_URL`, só o portal oficial é tentado (com retentativas de download).

5. **Comex Stat / balança comercial (opcional)** — módulo `comexstat_botucatu_etl.py`, acionado no pipeline com **`PIPELINE_INCLUDE_COMEX=1`** (ou rode `python comexstat_botucatu_etl.py` direto):
   - consulta a API do MDIC (`api-comexstat.mdic.gov.br`) para **totais mensais** de exportação/importação do município no Comex (código interno do município na API, padrão **3407506** para Botucatu-SP — ajuste com `COMEX_CITY_ID` se necessário);
   - cruza com **PTAX** (BCB SGS 1) para coluna estimada em R$;
   - gera `comex_botucatu_mensal.csv`, `comex_botucatu_top_sh4_export.csv`, `comex_botucatu_top_sh4_import.csv`, `comex_botucatu_meta.csv`;
   - **não existe ranking público por empresa/CNPJ** (sigilo); o painel do app usa **top 10 por produto (SH4)** como proxy oficial.
   - variáveis úteis: `COMEX_MONTHS_BACK`, `COMEX_RANKING_YEAR`, `COMEX_REQUEST_PAUSE_SEC` (pausa entre POSTs; padrão 12s ajuda a evitar **HTTP 429**).

```powershell
$env:PIPELINE_INCLUDE_COMEX="1"
python pipeline_botucatu.py
```

### Rodar pipeline mestre

```bash
python pipeline_botucatu.py
```

**Retomar o CAGED após interrupção:** o pipeline refaz o período inteiro por padrão. Para não baixar de novo desde `2024-01`, defina o primeiro mês a processar, por exemplo:

```powershell
$env:PIPELINE_CAGED_START_YEAR="2025"
$env:PIPELINE_CAGED_START_MONTH="6"
python pipeline_botucatu.py
```

Use o último mês que apareceu como concluído no log (`CAGED mês YYYY-MM …`) e comece no **mês seguinte**.

**Última competência no FTP (padrão):** antes de processar, o pipeline consulta o FTP e retrocede mês a mês até achar o último `CAGEDMOV*.7z` publicado; assim não se pede mês ainda inexistente (evita 550) e cada execução acompanha o **histórico vivo** do Ministério. Desligar a varredura: `PIPELINE_CAGED_FTP_DISCOVER=0` (volta a usar só o **lag**). Profundidade máxima da busca: `PIPELINE_CAGED_FTP_MAX_BACKTRACK` (padrão 48). Se a varredura falhar (rede/FTP), usa-se o limite por lag (`PIPELINE_CAGED_FTP_LAG_MONTHS`, padrão 2). Teto manual: `PIPELINE_CAGED_END_YEAR` e `PIPELINE_CAGED_END_MONTH`.

**Competência da movimentação (Novo CAGED):** após baixar MOV, FOR e EXC da **mesma** pasta, o pipeline unifica o modo de período (`competenciamov` / `competencia` AAAAMM, ou ano/mês de movimento) **só se essa coluna existir em todos os arquivos** daquele mês; assim retificações FOR tendem a ir para o **mês do evento**. Se faltar coluna em algum arquivo, usa-se o mês da pasta como fallback (conferir layout oficial). Cada nova execução rebaixa o FTP e **regrava** os CSVs — não são séries estáticas.

**Deduplicação de movimentos:** se o mesmo identificador existir com o **mesmo nome de coluna** em MOV, FOR e EXC, linhas repetidas são removidas antes do agregado, mantendo a declaração mais recente (pasta FTP mais nova e prioridade EXC > FOR > MOV). Desativar: `PIPELINE_CAGED_DEDUPE_ID=0`.

**Camada evento → fato (auditoria):** após a deduplicação, o micro vigente vira fato mensal por CNAE (Botucatu) e por município (comparativo), via `caged_eventos.py`. Cada execução grava `data/caged_staging/exec_meta.json` (schema v2: contagens, **SHA-256 dos .7z** baixados por competência de pasta FTP, flags). Gzip opcional do micro (`eventos_botucatu_micro.csv.gz` e `eventos_comparativo_municipios_micro.csv.gz`): `PIPELINE_CAGED_EXPORT_EVENTOS=1`. Pasta alternativa: `PIPELINE_CAGED_STAGING_DIR`.

**Limpar CSVs antes de uma carga cheia:** `PIPELINE_CLEAN_OUTPUTS=1` remove os arquivos de saída na raiz antes de gerar os novos (não há banco SQL — os CSV são o “armazém”).

## Funcionalidades do MVP

- **CAGED (Março/2026 vs Fevereiro/2026)**
  - Cards com:
    - Total de admissões
    - Total de desligamentos
    - Saldo de março
  - Indicador de variação percentual MoM em cada card.
  - Gráfico horizontal (Plotly) com:
    - Top 5 seções que mais geraram saldo
    - Top 5 seções que mais perderam saldo
  - Tabelas com:
    - Top 3 CNAEs (`subclasse`) que mais admitiram
    - Top 3 CNAEs (`subclasse`) que mais demitiram

- **Financeiro (Siconfi) — aba "Finanças da Prefeitura"**
  - Caixa, aplicações e poupança **do ente municipal** (não da população); métricas para fundos (`111111900`) e renda fixa (`111115000`) no mês do popover; evolução e saldo por instituição.

- **Cadastro CNPJ e MEI (quando os CSVs existirem na raiz ou em `data/`)**
  - Abas: visão geral municipal (gráficos e totais), base **JOIN** (uma linha por empresa: Estabelecimentos + Empresas + Simples) e aba **somente MEI**.
  - Totais de empresas (raiz), estabelecimentos no município, MEI ativos/inativos e opção no Simples.
  - Expander com texto e tabela sobre como o **município** entra no filtro (`Municipios.zip` + coluna `municipio` dos estabelecimentos).
  - Gráfico de barras com opções e exclusões MEI por mês (datas do `Simples`).
  - Distribuição percentual por tipo (MEI, EPP, ME, etc.).
  - CNAE (divisão) por tipo de empresa, com filtro e exportação CSV.

- **Resiliência**
  - Validação da existência dos CSVs com `os.path.exists()`.
  - Mensagens amigáveis com `st.warning()` quando arquivos/colunas estiverem ausentes.

## Deploy no Streamlit Community Cloud

1. Crie um repositório no GitHub.
2. Suba os arquivos:
   - `app.py`
   - `requirements.txt`
   - `relatorio_botucatu_q1_2026.csv`
   - `investimentos_botucatu_2026.csv`
3. Acesse [share.streamlit.io](https://share.streamlit.io/).
4. Conecte sua conta GitHub.
5. Selecione o repositório e o arquivo principal `app.py`.
6. Clique em **Deploy**.

## CI/CD (GitHub Actions)

O projeto possui workflow em `.github/workflows/ci-cd.yml` com:

- **CI (pull_request e push na main)**:
  - Instala dependências;
  - Valida sintaxe de `app.py`;
  - Executa smoke check de import das bibliotecas.

- **CD (push na main)**:
  - Gera artefato empacotado (`observatorio-botucatu.tar.gz`);
  - Publica o artefato no GitHub Actions.
- **Refresh automático de dados**:
  - Executa `pipeline_botucatu.py` diariamente (schedule) e também sob demanda (`workflow_dispatch`);
  - Se os CSVs mudarem, faz commit automático na `main`.

Para deploy público do painel, o Streamlit Community Cloud faz atualização automática quando há push na branch conectada.

## Hugging Face Hub (dataset)

Para publicar ou atualizar os CSVs em um **Dataset** no Hugging Face (ex.: espelho dos dados do repositório):

1. Crie um token com permissão de escrita em [Configuração → Access Tokens](https://huggingface.co/settings/tokens).
2. Instale a dependência opcional: `pip install -r requirements-hf.txt`
3. Defina o token e (se quiser outro nome) o repositório:
   - `HF_TOKEN` ou `HUGGING_FACE_HUB_TOKEN`
   - `HF_REPO` (padrão: `Utahh/caged_2026`, formato `usuario/nome-do-dataset`)
4. Execute: `python scripts/sync_hf_dataset.py`

O script cria o dataset se não existir e envia os principais CSVs + `README.md`.

**GitHub Actions:** workflow [Sync Hugging Face dataset](.github/workflows/hf-sync.yml) (`workflow_dispatch`). Configure o secret **`HF_TOKEN`** no repositório GitHub e dispare a action manualmente; opcionalmente altere o input `hf_repo`.

## Observações de atualização mensal

Como o MVP usa arquivos estáticos, para atualizar os dados:

1. Gere novamente os CSVs no processo de extração.
2. Substitua os arquivos no projeto/repositório.
3. Faça commit/push para refletir no app publicado.
