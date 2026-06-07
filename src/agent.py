"""
Agente em LangGraph para responder perguntas de negócio sobre o banco.

Fluxo do grafo:

    introspect_schema
          v
        plan                <- raciocínio em linguagem natural
          v
    generate_sql  <----+
          v            |  (loop de auto-correção:
    execute_sql        |   erro de SQL volta para regenerar,
          |            |   até MAX_SQL_ATTEMPTS)
          +--- erro? --+
          | ok
          v
      interpret             <- resposta em NL + escolha de visualização
          v
         END

Cada nó registra um "passo" no estado, formando a trilha de raciocínio que o
frontend exibe (requisito de transparência do desafio).
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import config, prompts
from .database import Database
from .llm import get_llm


# --------------------------------------------------------------------------- #
# Estado compartilhado entre os nós
# --------------------------------------------------------------------------- #
class AgentState(TypedDict, total=False):
    question: str
    schema: str
    plan: str
    sql: str
    attempts: int
    last_error: str | None
    # result: linhas como registros (serializável para o checkpointer).
    result: list[dict[str, Any]] | None
    answer: str
    analysis: str
    chart: dict[str, Any]
    # steps: trilha de raciocínio DESTE turno (reiniciada a cada pergunta).
    steps: list[dict[str, Any]]
    # history: turnos anteriores. operator.add => acumula entre perguntas e é
    # mantido pelo checkpointer do LangGraph (memória conversacional).
    history: Annotated[list[dict[str, Any]], operator.add]


def _strip_fences(text: str) -> str:
    """Remove cercas de código (```sql / ```json) que o LLM às vezes adiciona."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        for lang in ("sql", "json"):
            if t.lstrip().lower().startswith(lang):
                t = t.lstrip()[len(lang):]
    return t.strip().strip("`").strip()


class DataAgent:
    def __init__(self, db: Database | None = None, llm=None):
        self.db = db or Database(config.DB_PATH)
        self.llm = llm or get_llm()
        self.graph = self._build_graph()

    # ------------------------------- nós ------------------------------- #
    def _introspect(self, state: AgentState) -> dict:
        schema = self.db.describe_schema()
        return {
            "schema": schema,
            "attempts": 0,
            "steps": [{"node": "schema", "title": "Schema descoberto dinamicamente",
                       "detail": schema}],
        }

    def _plan(self, state: AgentState) -> dict:
        msgs = [
            SystemMessage(prompts.PLANNER_SYSTEM.format(
                schema=state["schema"],
                history=prompts.format_history(state.get("history", [])))),
            HumanMessage(prompts.PLANNER_HUMAN.format(question=state["question"])),
        ]
        plan = self.llm.invoke(msgs).content.strip()
        if plan.upper().startswith("FORA_DE_ESCOPO"):
            return {"plan": "FORA_DE_ESCOPO"}  # roteado para o nó de orientação
        step = {"node": "plan", "title": "Plano de análise", "detail": plan}
        return {"plan": plan, "steps": state.get("steps", []) + [step]}

    def _guide(self, state: AgentState) -> dict:
        """Resposta para perguntas que não são sobre os dados (saudação, meta, off-topic)."""
        msg = ("Posso responder perguntas sobre os dados de **clientes, compras, suporte e "
               "campanhas de marketing**. Por exemplo: número de reclamações não resolvidas "
               "por canal, categorias que mais vendem, ou a tendência de compras por mês. "
               "Veja as sugestões abaixo para começar.")
        step = {"node": "answer", "title": "Fora do escopo dos dados",
                "detail": "A pergunta não é sobre os dados; respondi com uma orientação."}
        return {"answer": msg, "analysis": "", "result": None,
                "chart": {"type": "table"}, "steps": state.get("steps", []) + [step]}

    def _generate_sql(self, state: AgentState) -> dict:
        system = prompts.SQL_SYSTEM.format(
            schema=state["schema"], plan=state["plan"],
            history=prompts.format_history(state.get("history", [])))
        if state.get("last_error"):
            system += prompts.SQL_RETRY_APPENDIX.format(
                failed_sql=state.get("sql", ""), error=state["last_error"]
            )
        msgs = [
            SystemMessage(system),
            HumanMessage(prompts.SQL_HUMAN.format(question=state["question"])),
        ]
        sql = _strip_fences(self.llm.invoke(msgs).content)
        attempt = state.get("attempts", 0) + 1
        title = "SQL gerado" if attempt == 1 else f"SQL corrigido (tentativa {attempt})"
        step = {"node": "sql", "title": title, "detail": sql}
        return {"sql": sql, "attempts": attempt, "steps": state.get("steps", []) + [step]}

    def _execute(self, state: AgentState) -> dict:
        result = self.db.run_query(state["sql"])
        if result.success:
            step = {"node": "exec", "title": "Execução bem-sucedida",
                    "detail": f"{result.row_count} linha(s) retornada(s)."}
            records = result.dataframe.to_dict(orient="records")
            return {"result": records, "last_error": None,
                    "steps": state.get("steps", []) + [step]}
        step = {"node": "exec", "title": "Erro na execução", "detail": result.error}
        return {"last_error": result.error, "steps": state.get("steps", []) + [step]}

    def _interpret(self, state: AgentState) -> dict:
        df = pd.DataFrame(state.get("result") or [])
        result_json = df.head(config.MAX_ROWS_TO_LLM).to_json(
            orient="records", force_ascii=False
        )
        msgs = [
            SystemMessage(prompts.INTERPRET_SYSTEM),
            HumanMessage(prompts.INTERPRET_HUMAN.format(
                question=state["question"], schema=state.get("schema", ""),
                sql=state["sql"], result=result_json)),
        ]
        raw = _strip_fences(self.llm.invoke(msgs).content)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"answer": raw, "chart_type": "table", "x": None, "y": None}
        chart = {
            "type": parsed.get("chart_type", "table"),
            "x": parsed.get("x"),
            "y": parsed.get("y"),
            "series": parsed.get("series"),
        }
        step = {"node": "answer", "title": "Resposta e visualização",
                "detail": f"Gráfico: {chart['type']}"}
        return {
            "answer": parsed.get("answer", ""),
            "analysis": parsed.get("analysis", ""),
            "chart": chart,
            "steps": state.get("steps", []) + [step],
            # registra este turno na memória conversacional (persistido pelo checkpointer)
            "history": [{"question": state["question"], "sql": state.get("sql", "")}],
        }

    # ----------------------------- arestas ----------------------------- #
    def _route_after_plan(self, state: AgentState) -> str:
        return "guide" if state.get("plan", "").upper().startswith("FORA_DE_ESCOPO") else "sql"

    def _route_after_exec(self, state: AgentState) -> str:
        if state.get("last_error") and state.get("attempts", 0) < config.MAX_SQL_ATTEMPTS:
            return "retry"
        return "ok"

    def _build_graph(self):
        g = StateGraph(AgentState)
        g.add_node("introspect", self._introspect)
        g.add_node("plan", self._plan)
        g.add_node("guide", self._guide)
        g.add_node("generate_sql", self._generate_sql)
        g.add_node("execute", self._execute)
        g.add_node("interpret", self._interpret)

        g.add_edge(START, "introspect")
        g.add_edge("introspect", "plan")
        g.add_conditional_edges(
            "plan", self._route_after_plan,
            {"guide": "guide", "sql": "generate_sql"},
        )
        g.add_edge("guide", END)
        g.add_edge("generate_sql", "execute")
        g.add_conditional_edges(
            "execute", self._route_after_exec,
            {"retry": "generate_sql", "ok": "interpret"},
        )
        g.add_edge("interpret", END)
        # checkpointer => o estado (incl. histórico) persiste entre perguntas do
        # mesmo thread_id, dando memória conversacional.
        return g.compile(checkpointer=MemorySaver())

    # ------------------------------ API ------------------------------- #
    def ask(self, question: str, thread_id: str = "default") -> AgentState:
        """
        Executa o agente para uma pergunta e devolve o estado final.

        Perguntas com o mesmo thread_id compartilham memória: o agente enxerga
        os turnos anteriores e resolve referências como "e via site?".
        """
        config = {"configurable": {"thread_id": thread_id}}
        return self.graph.invoke({"question": question}, config=config)

    def _schema(self) -> str:
        """Schema introspectado, cacheado (usado pelas sugestões)."""
        if not getattr(self, "_schema_cache", None):
            self._schema_cache = self.db.describe_schema()
        return self._schema_cache

    def suggest_questions(self, seed: str | None = None,
                          exclude: list[str] | None = None, n: int = 3) -> list[str]:
        """
        Gera perguntas de análise. Se `seed` for dado, as sugestões são RELACIONADAS
        àquela pergunta (sugestões contextuais que mudam conforme a conversa).
        `exclude` evita repetir perguntas já mostradas (para o botão "sugerir outras").
        """
        exclude_txt = ""
        if exclude:
            exclude_txt = "Não repita nem reformule estas: " + " | ".join(exclude)
        if seed:
            human = prompts.SUGGEST_HUMAN_SEED.format(seed=seed, n=n, exclude=exclude_txt)
        else:
            human = prompts.SUGGEST_HUMAN_COLD.format(n=n, exclude=exclude_txt)
        msgs = [
            SystemMessage(prompts.SUGGEST_SYSTEM.format(schema=self._schema(), n=n)),
            HumanMessage(human),
        ]
        raw = _strip_fences(self.llm.invoke(msgs).content)
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                return [str(x) for x in items][:n]
        except json.JSONDecodeError:
            pass
        return []
