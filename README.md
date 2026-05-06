# Observatório Botucatu (MVP)

Painel web interativo construído com Streamlit e Plotly para acompanhamento de dados econômicos e financeiros de Botucatu/SP.

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

- **Financeiro (Siconfi)**
  - Card com valor total mais recente de `Saldo_em_Reais` formatado em R$.

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

Para deploy público do painel, o Streamlit Community Cloud faz atualização automática quando há push na branch conectada.

## Observações de atualização mensal

Como o MVP usa arquivos estáticos, para atualizar os dados:

1. Gere novamente os CSVs no processo de extração.
2. Substitua os arquivos no projeto/repositório.
3. Faça commit/push para refletir no app publicado.
