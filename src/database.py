"""
Camada de acesso ao banco de dados.

Responsabilidades:
  - Conectar ao SQLite.
  - Introspecção DINÂMICA do schema (nada hardcoded): tabelas, colunas, tipos,
    valores distintos de colunas categóricas e linhas de amostra.
  - Execução de queries em modo SOMENTE LEITURA (defesa contra SQL destrutivo).

A introspecção é o que permite o agente "descobrir" a estrutura do banco em
tempo de execução, conforme exigido pelo desafio.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

import pandas as pd

# Colunas categóricas com no máximo este número de valores distintos têm seus
# valores listados no schema, ajudando o LLM a usar os termos corretos
# (ex.: 'App' vs 'Aplicativo', 'Reclamação' vs 'reclamacao').
MAX_DISTINCT_TO_LIST = 25

# Apenas comandos de leitura são permitidos.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|pragma)\b",
    re.IGNORECASE,
)


class UnsafeQueryError(Exception):
    """Levantada quando a query tenta modificar o banco."""


@dataclass
class QueryResult:
    """Resultado de uma execução de SQL."""

    success: bool
    dataframe: pd.DataFrame | None = None
    error: str | None = None

    @property
    def row_count(self) -> int:
        return 0 if self.dataframe is None else len(self.dataframe)


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        # uri read-only: o banco fica imutável mesmo que um SELECT malicioso passe.
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------------ #
    # Introspecção de schema
    # ------------------------------------------------------------------ #
    def list_tables(self) -> list[str]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        return [r["name"] for r in rows]

    def _columns(self, con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
        return [dict(r) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()]

    def _distinct_values(
        self, con: sqlite3.Connection, table: str, column: str
    ) -> list[Any] | None:
        """Retorna valores distintos se a coluna for de baixa cardinalidade."""
        try:
            n = con.execute(
                f'SELECT COUNT(DISTINCT "{column}") FROM "{table}"'
            ).fetchone()[0]
        except sqlite3.Error:
            return None
        if n == 0 or n > MAX_DISTINCT_TO_LIST:
            return None
        rows = con.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL ORDER BY 1'
        ).fetchall()
        return [r[0] for r in rows]

    def describe_schema(self) -> str:
        """
        Constrói uma descrição textual rica do schema para o LLM.

        Inclui, por tabela: colunas + tipos, valores distintos de colunas
        categóricas e uma linha de amostra. Tudo lido dinamicamente.
        """
        parts: list[str] = []
        with self._connect() as con:
            for table in self.list_tables():
                cols = self._columns(con, table)
                count = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

                lines = [f'Tabela "{table}" ({count} linhas):']
                for col in cols:
                    name, ctype = col["name"], (col["type"] or "TEXT")
                    desc = f'  - {name} ({ctype})'
                    # lista valores apenas para colunas textuais de baixa cardinalidade
                    if "TEXT" in ctype.upper() or "CHAR" in ctype.upper():
                        values = self._distinct_values(con, table, name)
                        if values:
                            shown = ", ".join(repr(v) for v in values)
                            desc += f"  -> valores: {shown}"
                    # para colunas de data, informa o período coberto (essencial p/
                    # interpretar "últimos 30 dias", "último ano" etc.)
                    if "data" in name.lower() or "date" in name.lower():
                        try:
                            mn, mx = con.execute(
                                f'SELECT MIN("{name}"), MAX("{name}") FROM "{table}"'
                            ).fetchone()
                            if mn and mx:
                                desc += f"  -> período: {mn} a {mx}"
                        except sqlite3.Error:
                            pass
                    lines.append(desc)

                sample = con.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchone()
                if sample:
                    lines.append(f"  amostra: {dict(sample)}")
                parts.append("\n".join(lines))

        header = (
            "BANCO DE DADOS SQLite. Datas são TEXT no formato 'YYYY-MM-DD' "
            "(use strftime para extrair ano/mês). Colunas BOOLEAN são 0/1.\n"
        )
        return header + "\n\n".join(parts)

    # ------------------------------------------------------------------ #
    # Execução
    # ------------------------------------------------------------------ #
    def run_query(self, sql: str) -> QueryResult:
        """Executa SQL de leitura e devolve um DataFrame ou o erro capturado."""
        if _FORBIDDEN.search(sql):
            return QueryResult(
                success=False,
                error="Query rejeitada: apenas comandos de leitura (SELECT) são permitidos.",
            )
        try:
            with self._connect() as con:
                df = pd.read_sql_query(sql, con)
            return QueryResult(success=True, dataframe=df)
        except Exception as exc:  # noqa: BLE001 - queremos capturar tudo p/ devolver ao LLM
            return QueryResult(success=False, error=f"{type(exc).__name__}: {exc}")
