"""
Avaliação automatizada do agente (execution accuracy).

Para cada pergunta do conjunto-ouro (evals/golden_set.json):
  1. Roda uma query de REFERÊNCIA (sabidamente correta) para obter o resultado esperado.
  2. Roda o AGENTE com a pergunta em linguagem natural.
  3. Compara os dois RESULTADOS (não o texto do SQL — existem muitos SQLs corretos).

Métricas reportadas: acurácia de execução (% de acertos), latência média e
número total de auto-correções de SQL. Salva um relatório em evals/report.json.

Uso:
    python -m evals.run_evals

Observação: cada pergunta consome ~2-3 chamadas ao LLM (cota da API).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from src import config
from src.agent import DataAgent
from src.database import Database

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
REPORT_PATH = Path(__file__).parent / "report.json"


def _normalize(df: pd.DataFrame | None) -> set[tuple]:
    """
    Converte um DataFrame num conjunto de tuplas de valores, ignorando nomes de
    colunas e ordem das linhas. Números são arredondados para tolerar variações
    de tipo (17 vs 17.0) e de precisão (1.96 vs 1.962).
    """
    if df is None or df.empty:
        return set()

    def norm_val(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            f = float(v)
            return int(f) if f.is_integer() else round(f, 2)
        return str(v).strip()

    return {tuple(norm_val(v) for v in row) for row in df.itertuples(index=False)}


def grade(expected: pd.DataFrame, actual: pd.DataFrame | None) -> bool:
    """Acerto = mesmo conjunto de valores no resultado."""
    return _normalize(expected) == _normalize(actual)


def run() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    db = Database(config.DB_PATH)
    agent = DataAgent(db=db)

    results = []
    print(f"\nAvaliando {len(golden)} perguntas...\n" + "=" * 70)

    for item in golden:
        expected = db.run_query(item["reference_sql"]).dataframe

        t0 = time.perf_counter()
        try:
            state = agent.ask(item["question"])
            records = state.get("result")
            actual = pd.DataFrame(records) if records else None
            attempts = state.get("attempts", 0)
            error = None
        except Exception as exc:  # noqa: BLE001
            actual, attempts, error = None, 0, str(exc)
        latency = time.perf_counter() - t0

        passed = grade(expected, actual) if error is None else False
        results.append({
            "id": item["id"],
            "passed": passed,
            "self_corrections": max(0, attempts - 1),
            "latency_s": round(latency, 2),
            "error": error,
        })

        status = "PASS" if passed else "FALHOU"
        extra = f" (auto-correções: {attempts - 1})" if attempts > 1 else ""
        print(f"[{status}] {item['id']:38} {latency:5.2f}s{extra}")
        if error:
            print(f"         erro: {error}")

    # ----------------------------- resumo ----------------------------- #
    total = len(results)
    passed_n = sum(r["passed"] for r in results)
    accuracy = passed_n / total if total else 0
    avg_latency = sum(r["latency_s"] for r in results) / total if total else 0
    corrections = sum(r["self_corrections"] for r in results)

    print("=" * 70)
    print(f"Acurácia de execução : {passed_n}/{total} ({accuracy:.0%})")
    print(f"Latência média       : {avg_latency:.2f}s")
    print(f"Auto-correções totais: {corrections}")

    report = {
        "accuracy": accuracy,
        "passed": passed_n,
        "total": total,
        "avg_latency_s": round(avg_latency, 2),
        "total_self_corrections": corrections,
        "details": results,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRelatório salvo em {REPORT_PATH}")


if __name__ == "__main__":
    run()
