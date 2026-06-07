"""Configurações centrais, lidas de variáveis de ambiente (.env)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


# Provedor de LLM: "google" (padrão, alinhado ao stack Vertex AI/Gemini da Franq),
# "openai" ou "anthropic". Trocar de provedor é só mudar esta variável + a chave.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google")

# Modelo. Verifique os modelos disponíveis no seu provedor.
# Modelos no tier gratuito (Flash/Flash-Lite): gemini-2.5-flash (recomendado),
# gemini-2.5-flash-lite (mais barato). Modelos Pro exigem faturamento.
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-flash")

# Temperatura 0 => máxima reprodutibilidade (importante para text-to-SQL).
TEMPERATURE = float(os.getenv("TEMPERATURE", "0"))

# Caminho do banco SQLite.
DB_PATH = os.getenv("DB_PATH", "data/clientes_completo.db")

# Tentativas máximas do loop de auto-correção de SQL.
MAX_SQL_ATTEMPTS = int(os.getenv("MAX_SQL_ATTEMPTS", "3"))

# Limite de linhas enviadas ao LLM na interpretação (controle de custo/tokens).
MAX_ROWS_TO_LLM = int(os.getenv("MAX_ROWS_TO_LLM", "50"))

# Tentativas do LLM em erros transitórios (ex.: 429/rate limit). Faz backoff e re-tenta.
MAX_LLM_RETRIES = int(os.getenv("MAX_LLM_RETRIES", "4"))
