# test_3_respuestas.py
# -----------------------------------------------------------
# App Streamlit para tests desde DOCX:
# - 3 opciones por pregunta: A, B, C
# - 1 única correcta
# - Formato esperado:
#
#   <enunciado...>
#   A) opción A
#   B) opción B
#   C) opción C
#   Solución: B
#
# También admite A. / B. / C.
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


LETTERS = "ABC"


# -------------------- Modelos --------------------
@dataclass
class Question:
    qid: str
    text: str
    options: List[str]   # 3 opciones
    correct: int         # 0..2


@dataclass
class QuestionUI:
    options: List[str]
    correct: int
    user: Optional[int] = None
    revealed: bool = False


# -------------------- Regex --------------------
R_SOLUTION = re.compile(
    r"^\s*Soluci[oó]n\s*:\s*([a-cA-C])\s*[\)\.]?\s*$",
    re.IGNORECASE
)
R_OPT = re.compile(
    r"^\s*([a-cA-C])\s*[\)\.]\s*(.+?)\s*$"
)
R_NOISE = re.compile(r"^\s*C2\s*[\-–]\s*Uso\s*Restringido\s*$", re.IGNORECASE)


# -------------------- Utilidades --------------------
def clean_line(s: str) -> str:
    s = (s or "").replace("\xa0", " ").strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_noise(s: str) -> bool:
    if not s:
        return False
    if R_NOISE.match(s):
        return True
    return False


def qkey_from_text(text: str, options: List[str]) -> str:
    base = re.sub(r"\s+", " ", text.strip().lower())
    opts = "||".join(re.sub(r"\s+", " ", o.strip().lower()) for o in options)
    return hashlib.sha1((base + "##" + opts).encode("utf-8")).hexdigest()


def qkey(q: Question) -> str:
    return qkey_from_text(q.text, q.options)


# -------------------- Parser DOCX --------------------
def parse_docx_questions(doc_bytes: bytes) -> List[Question]:
    """
    Formato esperado:
      enunciado (una o varias líneas)
      A) ...
      B) ...
      C) ...
      Solución: A|B|C
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

        stem_parts: List[str] = []
        labeled: Dict[str, str] = {}
        current_opt: Optional[str] = None
        seen_option = False

        for ln in content:
            mopt = R_OPT.match(ln)
            if mopt:
                seen_option = True
                letter = mopt.group(1).upper()
                text = mopt.group(2).strip()
                labeled[letter] = text
                current_opt = letter
            else:
                if seen_option and current_opt:
                    # Continuación de la última opción
                    labeled[current_opt] = (labeled[current_opt] + " " + ln).strip()
                else:
                    # Parte del enunciado
                    stem_parts.append(ln)

        opts = [labeled.get(k, "").strip() for k in LETTERS]
        if not all(opts):
            return

        question_text = " ".join(stem_parts).strip()
        if not question_text:
            return

        idx = ord(sol_letter.upper()) - ord("A")
        if not (0 <= idx <= 2):
            return

        q_counter += 1
        questions.append(Question(
            qid=str(q_counter),
            text=question_text,
            options=opts,
            correct=idx
        ))

    for ln in lines:
        if not ln:
            continue

        m = R_SOLUTION.match(ln)
        if m:
            flush(m.group(1))
        else:
            chunk.append(ln)

    # Deduplicado
    uniq: Dict[str, Question] = {}
    for q in questions:
        uniq[qkey(q)] = q

    return list(uniq.values())


# -------------------- Lógica del quiz --------------------
def build_quiz(bank: List[Question], n: int, seed: Optional[int], shuffle_options: bool):
    rng = random.Random(seed) if seed is not None else random
    sample = rng.sample(bank, k=min(n, len(bank), 100))

    ui_items: List[QuestionUI] = []
    for q in sample:
        if shuffle_options:
            idxs = list(range(3))
            rng.shuffle(idxs)
            new_opts = [q.options[i] for i in idxs]
            mapping = {old: new for new, old in enumerate(idxs)}
            ui_items.append(QuestionUI(
                options=new_opts,
                correct=mapping[q.correct]
            ))
        else:
            ui_items.append(QuestionUI(
                options=list(q.options),
                correct=q.correct
            ))

    return sample, ui_items


def score(quiz: List[Question], ui: List[QuestionUI]) -> Tuple[int, int, int, List[int]]:
    """
    Devuelve:
      ok: respondidas y correctas
      wrong: respondidas e incorrectas
      unanswered: sin responder
      wrong_idx: índices de las falladas
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

    review_quiz, review_ui = build_quiz(
        questions,
        min(100, len(questions), n),
        seed,
        shuffle_opts
    )
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


# -------------------- UI --------------------
st.set_page_config(page_title="Test 3 respuestas", page_icon="📝", layout="centered")
st.title("📝 Test de 3 respuestas")
st.caption("Sube un DOCX con preguntas de 3 opciones (A, B, C) y una única solución correcta.")

with st.sidebar:
    st.subheader("Configuración")
    up = st.file_uploader("Sube el DOCX", type=["docx"])
    num_q = st.number_input("Número de preguntas", 1, 100, 30, step=1)
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
if "mode" not in st.session_state: st.session_state.mode = "normal"
if "session_wrong_map" not in st.session_state: st.session_state.session_wrong_map = {}

if "uploaded_docx_bytes" not in st.session_state:
    st.session_state.uploaded_docx_bytes = None
if "uploaded_docx_name" not in st.session_state:
    st.session_state.uploaded_docx_name = None

# Guardar fichero en sesión
if up is not None:
    if (st.session_state.uploaded_docx_name != up.name) or (st.session_state.uploaded_docx_bytes is None):
        st.session_state.uploaded_docx_bytes = up.getvalue()
        st.session_state.uploaded_docx_name = up.name
        st.session_state.session_wrong_map = {}
        st.session_state.bank = []
        st.session_state.quiz = []
        st.session_state.ui = []
        reset_attempt_state()

    st.sidebar.caption(
        f"Archivo: {up.name} • {len(st.session_state.uploaded_docx_bytes)/1024:.1f} KB"
    )

# Preparar examen
if start:
    data = st.session_state.uploaded_docx_bytes
    if not data:
        st.error("Sube un DOCX antes de preparar el examen.")
    else:
        bank = parse_docx_questions(data)
        if not bank:
            st.error("No se detectaron preguntas. Verifica que el formato sea A/B/C + 'Solución: X'.")
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

# Datos actuales
bank: List[Question] = st.session_state.bank
quiz: List[Question] = st.session_state.quiz
ui_items: List[QuestionUI] = st.session_state.ui
i: int = st.session_state.i

if not quiz:
    st.info("Sube un DOCX y pulsa **Preparar examen**.")
    st.stop()

# Modo
if st.session_state.mode == "review_attempt":
    title = "Repaso de fallos (este intento)"
elif st.session_state.mode == "review_session":
    title = "Repaso de fallos (sesión)"
else:
    title = "Examen"

q = quiz[i]
u = ui_items[i]

st.subheader(f"{title} — Pregunta {i+1} de {len(quiz)}")
st.write(q.text)
st.write("")

opts = [f"{LETTERS[j]}. {u.options[j]}" for j in range(3)]

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
        st.success(f"✅ Correcta ({LETTERS[u.correct]})")
    else:
        st.error(f"❌ Incorrecta. Correcta: {LETTERS[u.correct]}")

st.divider()

ok, wrong, unanswered, wrong_idx = score(quiz, ui_items)
tot = len(quiz)

st.write(
    f"Aciertos: **{ok}/{tot}** · "
    f"Fallos (intento): **{wrong}** · "
    f"Sin responder: **{unanswered}** · "
    f"Fallos (sesión): **{len(st.session_state.session_wrong_map)}**"
)

if st.button("🏁 Finalizar", disabled=st.session_state.done):
    st.session_state.done = True
    st.rerun()

if st.session_state.done:
    add_wrongs_to_session(quiz, ui_items)
    ok, wrong, unanswered, wrong_idx = score(quiz, ui_items)
    tot = len(quiz)
    pct = (ok / tot * 100) if tot else 0.0

    st.subheader("Resultados")
    st.write(f"Puntuación: **{ok}/{tot}** ({pct:.1f}%)")
    st.write(f"Fallos (este intento): **{wrong}**")
    st.write(f"Sin responder: **{unanswered}**")
    st.write(f"Fallos acumulados (sesión): **{len(st.session_state.session_wrong_map)}**")

    seed_val = int(seed) if use_seed else None

    col1, col2, col3 = st.columns(3)

    if wrong > 0 and col1.button("📚 Repasar fallos (intento)"):
        start_review_from_questions(
            [quiz[k] for k in wrong_idx],
            "review_attempt",
            int(num_q),
            seed_val,
            shuffle_opts
        )

    if len(st.session_state.session_wrong_map) > 0 and col2.button("🧠 Repasar fallos (sesión)"):
        start_review_from_questions(
            list(st.session_state.session_wrong_map.values()),
            "review_session",
            int(num_q),
            seed_val,
            shuffle_opts
        )

    if col3.button("🔄 Nuevo examen"):
        restart_normal_exam(
            st.session_state.bank,
            int(num_q),
            seed_val,
            shuffle_opts
        )

    if st.button("🧹 Limpiar fallos de la sesión"):
        st.session_state.session_wrong_map = {}
        st.success("Fallos de sesión borrados.")
