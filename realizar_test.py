# realizar_test.py
# -----------------------------------------------------------
# Test desde DOCX con 3 o 4 opciones (A–C / A–D), 1 correcta.
# - Soporta opciones etiquetadas: a) / a. ... (recomendado)
# - Soporta sin letras: toma las últimas 3 o 4 líneas como opciones (heurística)
# - Solución: a/b/c/d (también admite "Solución: c." o "Solución: c)")
# - Radio sin selección inicial (index=None)
# - Fallos (intento) = SOLO respondidas mal (no incluye sin responder)
# - Repaso fallos: intento y sesión
# -----------------------------------------------------------

import io
import re
import random
import hashlib
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple

import streamlit as st

try:
    import docx  # python-docx
except Exception as e:
    docx = None
    DOCX_IMPORT_ERR = e

# ------------ Modelos ------------
@dataclass
class Question:
    qid: str
    text: str
    options: List[str]   # longitud 3 o 4
    correct: int         # índice 0..len(options)-1

@dataclass
class QuestionUI:
    options: List[str]
    correct: int
    user: Optional[int] = None
    revealed: bool = False


# ------------ Regex / limpieza ------------
# Solución tolerante: "Solución: c", "Solución: c." "Solución: c)"
R_SOLUTION = re.compile(r"^\s*Soluci[oó]n\s*:\s*([a-dA-D])\s*[\)\.]?\s*$", re.IGNORECASE)
# Opción etiquetada (a/b/c/d):
R_OPT_LABELED = re.compile(r"^\s*([a-dA-D])\s*[\)\.]\s*(.*?)\s*$")
# Numeración de pregunta al inicio (la quitamos del enunciado)
R_QNUM = re.compile(r"^\s*\d{1,4}\s*[\.\)\-:]\s*")
# Ruido típico
R_NOISE = re.compile(r"^\s*C2\s*[\-–]\s*Uso\s*Restringido\s*$", re.IGNORECASE)

def clean_line(s: str) -> str:
    s = (s or "").replace("\xa0", " ").strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s

def is_noise(s: str) -> bool:
    if not s:
        return False
    if R_NOISE.match(s):
        return True
    if s.lower() in {"certyiq"}:
        return True
    return False

def qkey_from_text(text: str, options: List[str]) -> str:
    base = re.sub(r"\s+", " ", text.strip().lower())
    opts = "||".join(re.sub(r"\s+", " ", o.strip().lower()) for o in options)
    return hashlib.sha1((base + "##" + opts).encode("utf-8")).hexdigest()

def qkey(q: Question) -> str:
    return qkey_from_text(q.text, q.options)


# ------------ Parser DOCX (3 o 4 opciones) ------------
def parse_docx_questions(doc_bytes: bytes) -> List[Question]:
    """
    Cierra bloques por 'Solución: x'.
    En cada bloque intenta:
      1) Formato con letras:
         a) texto
         o
         a)
         texto
      2) Formato sin letras: últimas 3 o 4 líneas como opciones.
    """
    if docx is None:
        raise RuntimeError(f"Falta python-docx. Error importando: {DOCX_IMPORT_ERR}")

    d = docx.Document(io.BytesIO(doc_bytes))
    raw_lines = [clean_line(p.text) for p in d.paragraphs]

    lines: List[str] = []
    for ln in raw_lines:
        if is_noise(ln):
            continue
        if ln == "":
            if lines and lines[-1] == "":
                continue
        lines.append(ln)

    questions: List[Question] = []
    chunk: List[str] = []
    q_counter = 0

    def flush(sol_letter: str):
        nonlocal q_counter, chunk, questions

        content = [x for x in chunk if x.strip()]
        chunk = []

        if len(content) < 4:
            return

        # ---------- 1) Con letras a)/a. ----------
        labeled: Dict[str, str] = {}
        current_opt: Optional[str] = None
        stem_parts: List[str] = []
        seen_any_option = False

        for ln in content:
            mopt = R_OPT_LABELED.match(ln)

            if mopt:
                # Detecta tanto:
                #   a) En el nivel 2
                # como:
                #   a)
                #   En el nivel 2
                seen_any_option = True
                k = mopt.group(1).upper()
                txt = mopt.group(2).strip()

                if k not in labeled:
                    labeled[k] = txt
                else:
                    labeled[k] = (labeled[k] + " " + txt).strip()

                current_opt = k

            else:
                if seen_any_option and current_opt:
                    # Si ya estamos dentro de opciones, cualquier línea sin a)/b)/c)/d)
                    # se considera continuación de la última opción.
                    labeled[current_opt] = (labeled[current_opt] + " " + ln).strip()
                else:
                    # Antes de ver la primera opción, todo pertenece al enunciado.
                    stem_parts.append(ln)

        if seen_any_option:
            # Limpia opciones vacías/espacios
            labeled = {k: v.strip() for k, v in labeled.items()}

            have_abc = all(labeled.get(k, "") for k in ["A", "B", "C"])
            have_abcd = have_abc and bool(labeled.get("D", ""))

            nopt = 4 if have_abcd else 3 if have_abc else 0

            if nopt in (3, 4):
                option_letters = ["A", "B", "C", "D"][:nopt]
                opts = [labeled[k] for k in option_letters]

                text = " ".join(stem_parts).strip()
                text = R_QNUM.sub("", text).strip()

                idx = ord(sol_letter.upper()) - ord("A")

                if text and 0 <= idx < nopt:
                    q_counter += 1
                    questions.append(Question(str(q_counter), text, opts, idx))
                    return

        # ---------- 2) Sin letras: heurística 4 o 3 últimas líneas ----------
        def try_tail(nopt: int) -> Optional[Question]:
            if len(content) < (nopt + 1):
                return None

            opts = [o.strip() for o in content[-nopt:]]
            stem = content[:-nopt]

            text = " ".join(stem).strip()
            text = R_QNUM.sub("", text).strip()

            if not text:
                return None
            if any(not o for o in opts):
                return None

            idx = ord(sol_letter.upper()) - ord("A")
            if not (0 <= idx < nopt):
                return None

            return Question("?", text, opts, idx)

        cand4 = try_tail(4)
        cand3 = try_tail(3)

        chosen = None
        if cand4 and cand3:
            chosen = cand4 if len(cand4.text) >= len(cand3.text) else cand3
        else:
            chosen = cand4 or cand3

        if chosen:
            q_counter += 1
            chosen.qid = str(q_counter)
            questions.append(chosen)

    for ln in lines:
        if not ln:
            continue

        m = R_SOLUTION.match(ln)
        if m:
            flush(m.group(1))
        else:
            chunk.append(ln)

    # Deduplicado por enunciado + opciones
    uniq: Dict[str, Question] = {}
    for q in questions:
        uniq[qkey(q)] = q

    return list(uniq.values())
        # ---------- 2) Sin letras: heurística 4 o 3 últimas líneas ----------
        def try_tail(nopt: int) -> Optional[Question]:
            nonlocal q_counter
            if len(content) < (nopt + 1):  # enunciado + opciones
                return None
            opts = [o.strip() for o in content[-nopt:]]
            stem = content[:-nopt]
            text = " ".join(stem).strip()
            text = R_QNUM.sub("", text).strip()
            if not text:
                return None
            if any(not o for o in opts):
                return None
            idx = ord(sol_letter.upper()) - ord("A")
            if not (0 <= idx < nopt):
                return None
            return Question("?", text, opts, idx)

        cand4 = try_tail(4)
        cand3 = try_tail(3)

        chosen = None
        if cand4 and cand3:
            # Preferimos la que deje un enunciado más razonable (más largo)
            # (suele evitar tragarse parte del enunciado como opción)
            chosen = cand4 if len(cand4.text) >= len(cand3.text) else cand3
        else:
            chosen = cand4 or cand3

        if chosen:
            q_counter += 1
            chosen.qid = str(q_counter)
            questions.append(chosen)

    for ln in lines:
        if not ln:
            continue
        m = R_SOLUTION.match(ln)
        if m:
            flush(m.group(1))
        else:
            chunk.append(ln)

    # Deduplicado por (enunciado+opciones)
    uniq: Dict[str, Question] = {}
    for q in questions:
        uniq[qkey(q)] = q
    return list(uniq.values())


# ------------ Quiz helpers (n opciones) ------------
def build_quiz(bank: List[Question], n: int, seed: Optional[int], shuffle_options: bool):
    rng = random.Random(seed) if seed is not None else random
    sample = rng.sample(bank, k=min(n, len(bank), 100))

    ui_items: List[QuestionUI] = []
    for q in sample:
        nopt = len(q.options)
        if shuffle_options:
            idxs = list(range(nopt))
            rng.shuffle(idxs)
            new_opts = [q.options[i] for i in idxs]
            mapping = {old: new for new, old in enumerate(idxs)}
            ui_items.append(QuestionUI(options=new_opts, correct=mapping[q.correct]))
        else:
            ui_items.append(QuestionUI(options=list(q.options), correct=q.correct))
    return sample, ui_items

def score(quiz: List[Question], ui: List[QuestionUI]) -> Tuple[int, int, int, List[int]]:
    """
    ok: respondidas y correctas
    wrong: respondidas e incorrectas (NO incluye sin responder)
    unanswered: sin responder
    wrong_idx: índices de las falladas (respondidas mal)
    """
    ok = 0
    wrong = 0
    unanswered = 0
    wrong_idx: List[int] = []

    for k, u in enumerate(ui):
        if u.user is None:
            unanswered += 1
        elif u.user == u.correct:
            ok += 1
        else:
            wrong += 1
            wrong_idx.append(k)

    return ok, wrong, unanswered, wrong_idx
def tribunal_grade(ok: int, wrong: int, total: int) -> float:
    """
    Criterio del tribunal:
    - Acierto: +1
    - Fallo: -1/3
    - Sin responder: 0
    La nota se calcula sobre el total de preguntas del test.
    """
    if total == 0:
        return 0.0

    raw_score = ok - (wrong / 3)
    grade = (raw_score / total) * 10
    return grade


def format_grade(grade: float) -> str:
    """
    Devuelve la nota con coma decimal: 7,40
    """
    return f"{grade:.2f}".replace(".", ",")
    
def reset_attempt_state():
    st.session_state.i = 0
    st.session_state.done = False

def add_wrongs_to_session(quiz: List[Question], ui: List[QuestionUI]):
    _, _, _, wrong_idx = score(quiz, ui)
    for k in wrong_idx:
        st.session_state.session_wrong_map[qkey(quiz[k])] = quiz[k]

def start_review_from_questions(questions: List[Question], mode_name: str,
                                n: int, seed: Optional[int], shuffle_opts: bool):
    if not questions:
        st.info("No hay preguntas para repasar 🙂")
        return
    review_quiz, review_ui = build_quiz(questions, min(100, len(questions), n), seed, shuffle_opts)
    st.session_state.quiz = review_quiz
    st.session_state.ui = review_ui
    st.session_state.mode = mode_name
    reset_attempt_state()
    st.rerun()

def restart_normal_exam(bank: List[Question], n: int, seed: Optional[int], shuffle_opts: bool):
    new_quiz, new_ui = build_quiz(bank, n, seed, shuffle_opts)
    st.session_state.quiz = new_quiz
    st.session_state.ui = new_ui
    st.session_state.mode = "normal"
    reset_attempt_state()
    st.rerun()


# ------------ UI ------------
st.set_page_config(page_title="Test desde DOCX ", page_icon="📝", layout="centered")
st.title("📝 Test desde DOCX ")
st.caption("por Miguel Ángel Gómez Ortiz")

with st.sidebar:
    st.subheader("Configuración")
    up = st.file_uploader("Sube el DOCX", type=["docx"])
    num_q = st.number_input("Número de preguntas", 1, 100, 50, step=1)
    use_seed = st.checkbox("Fijar semilla", value=False)
    seed = st.number_input("Semilla", 0, 10_000_000, 0, step=1, disabled=not use_seed)
    shuffle_opts = st.checkbox("Barajar opciones", value=True)
    start = st.button("🎲 Preparar examen")

# Estado
if "bank" not in st.session_state: st.session_state.bank = []
if "quiz" not in st.session_state: st.session_state.quiz = []
if "ui" not in st.session_state: st.session_state.ui = []
if "i" not in st.session_state: st.session_state.i = 0
if "done" not in st.session_state: st.session_state.done = False
if "mode" not in st.session_state: st.session_state.mode = "normal"  # normal | review_attempt | review_session
if "session_wrong_map" not in st.session_state: st.session_state.session_wrong_map = {}

# Persistencia del uploader (móvil)
if "uploaded_docx_bytes" not in st.session_state: st.session_state.uploaded_docx_bytes = None
if "uploaded_docx_name" not in st.session_state: st.session_state.uploaded_docx_name = None

if up is not None:
    if (st.session_state.uploaded_docx_name != up.name) or (st.session_state.uploaded_docx_bytes is None):
        st.session_state.uploaded_docx_bytes = up.getvalue()
        st.session_state.uploaded_docx_name = up.name
        st.session_state.session_wrong_map = {}
        st.session_state.bank = []
        st.session_state.quiz = []
        st.session_state.ui = []
        reset_attempt_state()
    st.sidebar.caption(f"Archivo: {up.name} • {len(st.session_state.uploaded_docx_bytes)/1024:.1f} KB")

# Preparar examen
if start:
    data = st.session_state.uploaded_docx_bytes
    if not data:
        st.error("Sube un DOCX antes de preparar el examen.")
    else:
        bank = parse_docx_questions(data)
        if not bank:
            st.error("No se detectaron preguntas. Verifica que existan líneas tipo 'Solución: a/b/c/d'.")
        else:
            st.session_state.bank = bank
            seed_val = int(seed) if use_seed else None
            quiz, ui_items = build_quiz(bank, int(num_q), seed_val, shuffle_opts)
            st.session_state.quiz = quiz
            st.session_state.ui = ui_items
            st.session_state.mode = "normal"
            reset_attempt_state()
            st.success(f"Banco: {len(bank)} preguntas • Examen: {len(quiz)}.")
            st.rerun()

# Render
quiz: List[Question] = st.session_state.quiz
ui_items: List[QuestionUI] = st.session_state.ui
i: int = st.session_state.i

if not quiz:
    st.info("Sube un DOCX y pulsa **Preparar examen**.")
    st.stop()

# título
if st.session_state.mode == "review_attempt":
    title = "Repaso de fallos (este intento)"
elif st.session_state.mode == "review_session":
    title = "Repaso de fallos (sesión)"
else:
    title = "Examen"

q = quiz[i]
u = ui_items[i]
nopt = len(u.options)

letters = "ABCD"[:nopt]
opts = [f"{letters[j]}. {u.options[j]}" for j in range(nopt)]

st.subheader(f"{title} — Pregunta {i+1} de {len(quiz)}")
st.write(q.text)
st.write("")

chosen = st.radio(
    "Selecciona la respuesta:",
    options=opts,
    index=None if u.user is None else u.user,
    key=f"radio_{st.session_state.mode}_{i}"
)
u.user = None if chosen is None else opts.index(chosen)

c1, c2, c3 = st.columns(3)
if c1.button("✅ Corregir", key=f"rev_{st.session_state.mode}_{i}"):
    u.revealed = True
if c2.button("⬅️ Anterior", disabled=(i == 0), key=f"prev_{st.session_state.mode}_{i}"):
    st.session_state.i = max(0, i - 1)
    st.rerun()
if c3.button("Siguiente ➡️", disabled=(i == len(quiz) - 1), key=f"next_{st.session_state.mode}_{i}"):
    st.session_state.i = min(len(quiz) - 1, i + 1)
    st.rerun()

if u.revealed:
    if u.user is None:
        st.warning("No has seleccionado respuesta.")
    elif u.user == u.correct:
        st.success(f"✅ Correcta ({letters[u.correct]})")
    else:
        st.error(f"❌ Incorrecta. Correcta: {letters[u.correct]}")

st.divider()

ok, wrong, unanswered, wrong_idx = score(quiz, ui_items)
tot = len(quiz)
grade = tribunal_grade(ok, wrong, tot)

st.write(
    f"Aciertos: **{ok}/{tot}** · "
    f"Fallos (intento): **{wrong}** · "
    f"Sin responder: **{unanswered}** · "
    f"Fallos (sesión): **{len(st.session_state.session_wrong_map)}** · "
    f"Nota: **{format_grade(grade)}**"
)

if st.button("🏁 Finalizar", disabled=st.session_state.done):
    st.session_state.done = True
    st.rerun()

if st.session_state.done:
    add_wrongs_to_session(quiz, ui_items)
    ok, wrong, unanswered, wrong_idx = score(quiz, ui_items)
    pct = (ok / tot * 100) if tot else 0.0
    grade = tribunal_grade(ok, wrong, tot)

    st.subheader("Resultados")
    st.write(f"Puntuación: **{ok}/{tot}** ({pct:.1f}%)")
    st.write(f"Fallos (este intento): **{wrong}**")
    st.write(f"Sin responder: **{unanswered}**")
    st.write(f"Fallos acumulados (sesión): **{len(st.session_state.session_wrong_map)}**")
    st.write(f"Nota: **{format_grade(grade)}**")

    seed_val = int(seed) if use_seed else None
    col1, col2, col3 = st.columns(3)

    if wrong > 0 and col1.button("📚 Repasar fallos (intento)"):
        start_review_from_questions([quiz[k] for k in wrong_idx], "review_attempt", int(num_q), seed_val, shuffle_opts)

    if len(st.session_state.session_wrong_map) > 0 and col2.button("🧠 Repasar fallos (sesión)"):
        start_review_from_questions(list(st.session_state.session_wrong_map.values()), "review_session", int(num_q), seed_val, shuffle_opts)

    if col3.button("🔄 Nuevo examen"):
        restart_normal_exam(st.session_state.bank, int(num_q), seed_val, shuffle_opts)

    if st.button("🧹 Limpiar fallos de la sesión"):
        st.session_state.session_wrong_map = {}
        st.success("Fallos de sesión borrados.")
