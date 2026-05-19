# scripts

Pasta para scripts operacionais (execução de ETL, utilitários e automações de manutenção).

- `run_pipeline.py`: atalho para executar o pipeline principal.
- `sync_hf_dataset.py`: envia CSVs para o dataset no Hugging Face Hub.
- `sync_hf_space.py`: envia `app.py`, `requirements.txt` e CSVs para o [Space Streamlit](https://huggingface.co/spaces/cauanalima/observatorio-botucatu) (`HF_SPACE_REPO`).
- `rebuild_cnpj_from_join.py`: reagrega CNPJ/MEI a partir do join local (sem rebaixar ZIPs).
