"""
Franq — Assistente Virtual de Dados.

Recursos:
  - Conversa em chat com MEMÓRIA (perguntas de acompanhamento: "e via site?").
  - Resposta em linguagem natural + uma ANÁLISE curta para o gestor.
  - Visualização dinâmica (tabela, barra ou linha) no tema da marca.
  - SUGESTÕES de perguntas que mudam conforme a conversa (+ botão "sugerir outras").
  - Download em CSV (Excel) e PDF (relatório pronto para compartilhar).
  - Trilha de raciocínio transparente (schema, plano, SQL, correções).

Execução:  streamlit run app.py
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (LongTable, Paragraph, SimpleDocTemplate, Spacer,
                                TableStyle)

from src.agent import DataAgent

# Paleta da marca Franq
FRANQ_BLUE = "#5B5BD6"
FRANQ_SEQ = ["#5B5BD6", "#22D3A6", "#8B5CF6", "#38BDF8", "#F472B6", "#FBBF24"]

st.set_page_config(page_title="Franq · Assistente de Dados", page_icon="📊", layout="wide")

# Cores leves por função de botão (CSS mira a classe st-key-<key> do Streamlit):
# CSV = verde (Excel), PDF = vermelho (PDF), sugestões = roxo da marca.
st.markdown("""
<style>
[class*="st-key-csv_"] button{border:1px solid rgba(34,211,166,.45)!important;color:#3FE0B6!important;background:rgba(34,211,166,.07)!important;}
[class*="st-key-csv_"] button:hover{background:rgba(34,211,166,.16)!important;border-color:#22D3A6!important;}
[class*="st-key-pdf_"] button{border:1px solid rgba(244,114,114,.45)!important;color:#F49090!important;background:rgba(244,114,114,.07)!important;}
[class*="st-key-pdf_"] button:hover{background:rgba(244,114,114,.16)!important;border-color:#F47272!important;}
[class*="st-key-sug_"] button{border:1px solid rgba(124,108,240,.40)!important;color:#CBC4F7!important;background:rgba(91,91,214,.08)!important;}
[class*="st-key-sug_"] button:hover{background:rgba(91,91,214,.18)!important;border-color:#7C6CF0!important;}
[class*="st-key-full_"] button{border:1px solid rgba(91,91,214,.55)!important;color:#B9B6F2!important;background:rgba(91,91,214,.14)!important;}
[class*="st-key-full_"] button:hover{background:rgba(91,91,214,.26)!important;border-color:#5B5BD6!important;}
[class*="st-key-regen_"] button{color:#9AA0B3!important;}
</style>
""", unsafe_allow_html=True)

# Avatares (com fallback para emoji se faltar o arquivo)
_USER_AVATAR_PATH = Path(__file__).parent / "assets" / "user_executivo.png"
USER_AVATAR = PILImage.open(_USER_AVATAR_PATH) if _USER_AVATAR_PATH.exists() else "🧑‍💼"
_ASST_AVATAR_PATH = Path(__file__).parent / "assets" / "assistant_pessoal.png"
ASSISTANT_AVATAR = PILImage.open(_ASST_AVATAR_PATH) if _ASST_AVATAR_PATH.exists() else "📊"

EXAMPLES = [
    "Liste os 5 estados com maior número de clientes que compraram via app em maio.",
    "Quantos clientes interagiram com campanhas de WhatsApp em 2024?",
    "Quais categorias de produto tiveram o maior número de compras em média por cliente?",
    "Qual o número de reclamações não resolvidas por canal?",
    "Qual a tendência de reclamações por canal no último ano?",
]


@st.cache_resource(show_spinner=False)
def load_agent() -> DataAgent:
    return DataAgent()


# --------------------------------------------------------------------------- #
# Relatório PDF
# --------------------------------------------------------------------------- #
def _fmt_df(df: pd.DataFrame) -> pd.DataFrame:
    """Cópia do DataFrame com colunas decimais arredondadas a 2 casas (para exibição)."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(2)
    return out


def _pdf_table(df: pd.DataFrame):
    df = _fmt_df(df)
    data = [list(df.columns)] + df.astype(str).values.tolist()
    table = LongTable(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(FRANQ_BLUE)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF0FB")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


def build_pdf(question: str, answer: str, analysis: str, df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="Franq - Relatório de Análise")
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Franq — Relatório de Análise de Dados", styles["Title"]),
        Paragraph(datetime.now().strftime("Gerado em %d/%m/%Y às %H:%M"), styles["Normal"]),
        Spacer(1, 14),
        Paragraph("<b>Pergunta</b>", styles["Heading3"]),
        Paragraph(escape(question), styles["Normal"]),
        Spacer(1, 8),
        Paragraph("<b>Resposta</b>", styles["Heading3"]),
        Paragraph(escape(answer), styles["Normal"]),
    ]
    if analysis:
        story += [Spacer(1, 8),
                  Paragraph("<b>Análise</b>", styles["Heading3"]),
                  Paragraph(escape(analysis), styles["Normal"])]
    if df is not None and not df.empty:
        story += [Spacer(1, 12), Paragraph("<b>Dados</b>", styles["Heading3"]),
                  Spacer(1, 4), _pdf_table(df)]
    story += [Spacer(1, 18),
              Paragraph("<font size=8 color='grey'>Gerado automaticamente por Franq — "
                        "Assistente Virtual de Dados.</font>", styles["Normal"])]
    doc.build(story)
    return buf.getvalue()


def build_full_report(turns: list) -> bytes:
    """Relatório consolidado com TODAS as perguntas e respostas da sessão."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="Franq - Relatório Completo")
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Franq — Relatório Completo da Sessão", styles["Title"]),
        Paragraph(datetime.now().strftime("Gerado em %d/%m/%Y às %H:%M"), styles["Normal"]),
        Paragraph(f"{len(turns)} pergunta(s) nesta sessão.", styles["Normal"]),
        Spacer(1, 16),
    ]
    for i, t in enumerate(turns, 1):
        story += [
            Paragraph(f"<b>{i}. {escape(t['question'])}</b>", styles["Heading3"]),
            Paragraph(escape(t.get("answer", "")), styles["Normal"]),
        ]
        if t.get("analysis"):
            story += [Spacer(1, 4),
                      Paragraph(f"<i>Análise:</i> {escape(t['analysis'])}", styles["Normal"])]
        recs = t.get("result")
        if recs:
            df = pd.DataFrame(recs)
            if not df.empty:
                story += [Spacer(1, 6), _pdf_table(df)]
        story += [Spacer(1, 18)]
    story += [Paragraph("<font size=8 color='grey'>Gerado automaticamente por Franq — "
                        "Assistente Virtual de Dados.</font>", styles["Normal"])]
    doc.build(story)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Renderização
# --------------------------------------------------------------------------- #
def _infer_xy(df: pd.DataFrame, chart: dict):
    """Resolve x, y e série para barra/linha, inferindo quando o agente não definiu
    (necessário quando o usuário força um gráfico num resultado que veio como tabela)."""
    cols = list(df.columns)
    num_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    cat_cols = [c for c in cols if c not in num_cols]
    x = chart.get("x") if chart.get("x") in cols else (cat_cols[0] if cat_cols else (cols[0] if cols else None))
    y = chart.get("y") if chart.get("y") in cols else (num_cols[0] if num_cols else None)
    series = chart.get("series") if chart.get("series") in cols else None
    return x, y, series


def _line_ok(df: pd.DataFrame, x, chart: dict) -> bool:
    """Linha só faz sentido com eixo X temporal (mês, data, ano) ou numérico ordenado.
    Para comparações entre categorias (estados, canais) a linha não cabe."""
    if chart.get("type") == "line":
        return True
    if x is None or x not in df.columns:
        return False
    if pd.api.types.is_numeric_dtype(df[x]):
        return True
    name = str(x).lower()
    time_keys = ("mes", "mês", "data", "ano", "year", "month", "date", "dia",
                 "trimestre", "semana", "periodo", "período")
    if any(k in name for k in time_keys):
        return True
    try:  # tenta interpretar os valores como datas (em silêncio)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(df[x], errors="coerce")
        if parsed.notna().mean() > 0.7:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _style_fig(fig, legend: bool, hovermode: str = "closest") -> None:
    """Aparência consistente e moderna: fundo transparente, grade sutil, sem molduras."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Segoe UI, sans-serif", color="#C7C7D1", size=13),
        margin=dict(l=10, r=10, t=30, b=10),
        height=430,
        hovermode=hovermode,
        hoverlabel=dict(bgcolor="#16162A", bordercolor="#5B5BD6",
                        font=dict(color="#FFFFFF", family="Inter, Segoe UI, sans-serif", size=13)),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(showgrid=False, zeroline=False, showline=False, ticks="", showspikes=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                   zeroline=False, showline=False, ticks="", showspikes=False),
    )


def render_chart(df: pd.DataFrame, chart: dict, viz: str, key: str = "0") -> None:
    """Renderiza conforme a escolha do usuário: 'Tabela', 'Barra' ou 'Linha'."""
    if viz == "Tabela":
        st.dataframe(_fmt_df(df), use_container_width=True, key=f"tbl_{key}")
        return
    x, y, series = _infer_xy(df, chart)
    if x is None or y is None or x == y:
        st.info("Não há colunas adequadas para esse gráfico — mostrando a tabela.")
        st.dataframe(_fmt_df(df), use_container_width=True, key=f"tblf_{key}")
        return

    if viz == "Barra":
        # formato dos rótulos: inteiros sem casas, decimais com 1 casa
        whole = pd.api.types.is_integer_dtype(df[y]) or bool((df[y].dropna() % 1 == 0).all())
        yfmt = ".0f" if whole else ".1f"
        n = df[x].nunique()
        # menos categorias => mais espaço entre barras (evita barras "gordas")
        bargap = 0.6 if n <= 4 else (0.45 if n <= 8 else 0.3)
        if series and series in df.columns:
            fig = px.bar(df, x=x, y=y, color=series, barmode="group",
                         color_discrete_sequence=FRANQ_SEQ)
            fig.update_traces(hovertemplate=f"<b>%{{x}}</b> · %{{fullData.name}}<br>%{{y:{yfmt}}}<extra></extra>")
            _style_fig(fig, legend=True, hovermode="closest")
            fig.update_layout(bargap=bargap, bargroupgap=0.15)
        else:
            # gradiente por valor: barras maiores ficam mais saturadas
            fig = px.bar(df, x=x, y=y, color=y,
                         color_continuous_scale=["#8B8CF0", "#5B5BD6", "#3F3FA8"])
            fig.update_layout(coloraxis_showscale=False)
            fig.update_traces(hovertemplate=f"<b>%{{x}}</b><br>%{{y:{yfmt}}}<extra></extra>")
            _style_fig(fig, legend=False, hovermode="closest")
            fig.update_layout(bargap=bargap)
        fig.update_traces(marker_line_width=0, marker_cornerradius=10,
                          texttemplate=f"%{{y:{yfmt}}}", textfont=dict(color="#FFFFFF"),
                          textposition="outside", cliponaxis=False)
        st.plotly_chart(fig, use_container_width=True, key=f"plot_{key}")
    else:  # Linha — com efeito de brilho (glow)
        multi = bool(series and series in df.columns)
        groups = list(df.groupby(series)) if multi else [(None, df)]
        fig = go.Figure()
        for i, (name, g) in enumerate(groups):
            c = FRANQ_SEQ[i % len(FRANQ_SEQ)]
            # camada de brilho: linha larga e translúcida por baixo
            fig.add_trace(go.Scatter(x=g[x], y=g[y], mode="lines",
                                     line=dict(color=c, width=14, shape="spline"),
                                     opacity=0.18, hoverinfo="skip", showlegend=False))
            # linha principal, suave
            fig.add_trace(go.Scatter(
                x=g[x], y=g[y], mode="lines+markers", name=("" if name is None else str(name)),
                line=dict(color=c, width=3, shape="spline"),
                marker=dict(size=8, color=c, line=dict(width=0)),
                fill=("tozeroy" if name is None else None),
                fillcolor="rgba(91,91,214,0.10)",
                hovertemplate=("%{y}<extra></extra>" if multi else "<b>%{x}</b><br>%{y}<extra></extra>")))
        _style_fig(fig, legend=multi, hovermode=("x unified" if multi else "closest"))
        st.plotly_chart(fig, use_container_width=True, key=f"plot_{key}")

    with st.expander("Ver dados da tabela"):
        st.dataframe(_fmt_df(df), use_container_width=True, key=f"tblx_{key}")


def render_downloads(df, question, answer, analysis, key, full_pdf=b"") -> None:
    csv = _fmt_df(df).to_csv(sep=";", index=False).encode("utf-8-sig")
    pdf = build_pdf(question, answer, analysis, df)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("Baixar CSV (Excel)", csv, "resultado.csv", "text/csv",
                           use_container_width=True, key=f"csv_{key}")
    with c2:
        st.download_button("Baixar PDF (relatório)", pdf, "relatorio_franq.pdf",
                           "application/pdf", use_container_width=True, key=f"pdf_{key}")
    with c3:
        st.download_button("Resumo completo (sessão)", full_pdf or pdf,
                           "resumo_completo_franq.pdf", "application/pdf",
                           use_container_width=True, key=f"full_{key}",
                           help="Relatório com TODAS as perguntas e respostas desta conversa.")


def render_trace(steps) -> None:
    # rótulo amigável + explicação em linguagem simples para cada etapa do agente
    EXPLAIN = {
        "schema": ("Explorei o banco", "Li sozinho as tabelas e colunas disponíveis — "
                   "assim não dependo de nada fixo no código."),
        "plan": ("Planejei a resposta", "Defini, em linguagem natural, como chegar ao "
                 "resultado antes de escrever qualquer código."),
        "sql": ("Escrevi a consulta", "Traduzi o plano para uma consulta SQL."),
        "exec": ("Consultei o banco", None),
        "answer": ("Montei a resposta", "Interpretei os dados e escolhi a melhor forma "
                   "de mostrar (tabela, barra ou linha)."),
    }
    with st.expander("Como cheguei a esta resposta (passo a passo)"):
        st.caption("Cada etapa que o assistente percorreu, na ordem — do banco até a resposta.")
        for i, s in enumerate(steps, 1):
            node = s["node"]
            label, desc = EXPLAIN.get(node, (s["title"], None))
            # quando o SQL foi corrigido, deixa claro que houve autocorreção
            if node == "sql" and "corrigido" in s["title"].lower():
                label, desc = ("Corrigi a consulta",
                               "A primeira consulta deu erro no banco; reescrevi usando a "
                               "mensagem de erro para acertar.")
            st.markdown(f"**{i}. {label}**")
            if desc:
                st.caption(desc)
            if node == "sql":
                st.code(s["detail"], language="sql")
            elif node == "schema":
                with st.expander("Ver estrutura lida do banco"):
                    st.code(s["detail"], language="text")
            elif node == "exec":
                st.caption(s["detail"])
            else:
                st.write(s["detail"])


def _stream_words(text: str):
    """Gera o texto palavra a palavra (efeito de 'digitando')."""
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.018)


def render_answer(turn, full_pdf=b"") -> None:
    if turn.pop("animate", False):
        st.write_stream(_stream_words(turn["answer"]))  # digita só na primeira vez
    else:
        st.write(turn["answer"])
    if turn.get("analysis"):
        st.caption(f"Análise: {turn['analysis']}")
    records = turn.get("result")
    df = pd.DataFrame(records) if records else None
    if df is not None and not df.empty:
        if df.shape == (1, 1):  # valor único: número grande e limpo, sem tabela
            val = df.iloc[0, 0]
            if isinstance(val, float):
                val = int(val) if float(val).is_integer() else round(val, 2)
            st.markdown(
                f"<div style='font-size:2.6rem;font-weight:700;color:#FFFFFF;"
                f"line-height:1.15'>{val}</div>", unsafe_allow_html=True)
        elif len(df.columns) >= 2:  # só vale oferecer gráfico se houver o que plotar
            chart = turn.get("chart", {})
            x, _, _ = _infer_xy(df, chart)
            options = ["Tabela", "Barra"]
            if _line_ok(df, x, chart):
                options.append("Linha")
            default = {"bar": "Barra", "line": "Linha"}.get(chart.get("type"), "Tabela")
            if default not in options:
                default = "Barra"
            viz = st.radio("Visualização:", options, index=options.index(default),
                           horizontal=True, key=f"viz_{turn['key']}")
            render_chart(df, chart, viz, key=turn["key"])
        else:
            st.dataframe(_fmt_df(df), use_container_width=True, key=f"tbl1_{turn['key']}")
        render_downloads(df, turn["question"], turn["answer"], turn.get("analysis", ""), turn["key"], full_pdf)
    render_trace(turn.get("steps", []))


def render_suggestions(turn, is_last) -> None:
    # geradas sob demanda (após a resposta já estar na tela) e cacheadas no turno
    if turn.get("suggestions") is None:
        try:
            turn["suggestions"] = load_agent().suggest_questions(seed=turn["question"])
        except Exception:  # noqa: BLE001
            turn["suggestions"] = []
    sugg = turn.get("suggestions") or []
    if sugg:
        st.caption("Perguntas relacionadas:")
        cols = st.columns(len(sugg))
        for j, q in enumerate(sugg):
            if cols[j].button(q, key=f"sug_{turn['key']}_{j}", use_container_width=True):
                st.session_state._pending = q
    if is_last and sugg:
        if st.button("↻ Sugerir outras perguntas", key=f"regen_{turn['key']}"):
            st.session_state._regen = True


# --------------------------------------------------------------------------- #
# Estado da sessão
# --------------------------------------------------------------------------- #
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "turns" not in st.session_state:
    st.session_state.turns = []

_LOGO = Path(__file__).parent / "assets" / "franq_logo.png"
if _LOGO.exists():
    st.image(str(_LOGO), width=210)
else:
    st.title("Franq")
st.caption(
    "Assistente Virtual de Dados. Pergunte em linguagem natural: o agente descobre o "
    "schema, escreve e corrige o SQL sozinho, lembra do contexto e sugere próximas perguntas."
)

with st.sidebar:
    st.subheader("Perguntas de exemplo")
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True):
            st.session_state._pending = ex
    st.divider()
    if st.button("Nova conversa", use_container_width=True):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.turns = []
        st.rerun()

# Botão "sugerir outras": regenera as sugestões do último turno
if st.session_state.pop("_regen", False) and st.session_state.turns:
    last = st.session_state.turns[-1]
    with st.spinner("Gerando novas sugestões..."):
        last["suggestions"] = load_agent().suggest_questions(
            seed=last["question"], exclude=last.get("suggestions", []))

# Renderiza a conversa
_full_pdf = build_full_report(st.session_state.turns) if st.session_state.turns else b""
for i, turn in enumerate(st.session_state.turns):
    with st.chat_message("user", avatar=USER_AVATAR):
        st.write(turn["question"])
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        render_answer(turn, _full_pdf)
        render_suggestions(turn, is_last=(i == len(st.session_state.turns) - 1))

# Entrada
pending = st.session_state.pop("_pending", None)
typed = st.chat_input("Faça uma pergunta (ou um acompanhamento, ex.: 'e via site?')")
question = typed or pending

if question:
    agent = load_agent()
    # mostra a pergunta imediatamente
    with st.chat_message("user", avatar=USER_AVATAR):
        st.write(question)
    # bolha do assistente com carregamento encenado
    try:
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            ph = st.empty()
            ph.markdown("_Consultando informações..._")
            time.sleep(0.2)
            ph.markdown("_Buscando dados no banco..._")
            result = agent.ask(question, thread_id=st.session_state.thread_id)
            ph.empty()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Falha ao executar o agente: {exc}")
        st.info("Verifique a chave de API e o LLM_PROVIDER no .env.")
        st.stop()

    st.session_state.turns.append({
        "question": question,
        "answer": result.get("answer", "Sem resposta."),
        "analysis": result.get("analysis", ""),
        "result": result.get("result"),
        "chart": result.get("chart", {}),
        "steps": result.get("steps", []),
        "suggestions": None,  # geradas sob demanda na renderização (não bloqueiam a resposta)
        "animate": True,  # a resposta sai "digitando" na primeira renderização
        "key": str(len(st.session_state.turns)),
    })
    st.rerun()
