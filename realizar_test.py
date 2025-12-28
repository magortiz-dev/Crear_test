# realizar_test.py
# -----------------------------------------------------------
# Quiz desde DOCX  - opción única
# - Radio buttons A–D SIN opción "Sin responder"
# - Inicio sin ninguna marcada (index=None si tu Streamlit lo soporta)
# - Repasar fallos (intento) y fallos acumulados (sesión)
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
    DOCX_OK = True
except Exception as e:
    DOCX_OK = False
    DOCX_ERR = e

LETTERS = "ABCD"


@dataclass
class Question:
    qid: str
    text: str
    options: List[str]  # A..D
    correct: int        # 0..3


@dataclass
class QuestionUI:
    options: List[str]
    correct: int
    user: Optional[int] = None
    revealed: bool = False


# -------------------- Regex / limpieza --------------------
R_SOLUTION = re.compile(r"^\s*Soluci[oó]n\s*:\s*([a-dA-D])\s*$", re.IGNORECASE)
R_OPT_LABELED = re.compile(r"^\s*([a-dA-D])\s*[\)\.]\s*(.+?)\s*$")  # a) / a.
R_NOISE = re.compile(r"^\s*C2\s*[\-–]\s*Uso\s*Restringido\s*$", re.IGNORECASE)
R_QNUM = re.compile(r"^\s*\d{1,4}\s*[\.\)\-:]\s*")  # "16." / "1)" / "23 -"


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


def qkey(q: Question) -> str:
    base = re.sub(r"\s+", " ", q.text.strip().lower())
    opts = "||".join([re.sub(r"\s+", " ", o.strip().lower()) for o in q.options])
    return hashlib.sha1((base + "##" + opts).encode("utf-8")).hexdigest()


# -------------------- Parser DOCX --------------------
def parse_docx_questions(doc_bytes: bytes) -> List[Question]:
    if not DOCX_OK:
        raise RuntimeError(f"Falta python-docx: {DOCX_ERR}")

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
        if len(content) < 5:
            return

        # --- Intento 1: opciones etiquetadas a)/a. ---
        labeled: Dict[str, str] = {}
        current_opt: Optional[str] = None
        stem_parts: List[str] = []
        seen_any_option = False

        for ln in content:
            mopt = R_OPT_LABELED.match(ln)
            if mopt:
                seen_any_option = True
                k = mopt.group(1).upper()  # A-D
                txt = mopt.group(2).strip()
                labeled[k] = txt
                current_opt = k
            else:
                if seen_any_option and current_opt:
                    labeled[current_opt] = (labeled[current_opt] + " " + ln).strip()
                else:
                    stem_parts.append(ln)

        if seen_any_option and len(labeled) >= 3:
            opts = [labeled.get(k, "").strip() for k in LETTERS]
            if all(opts):
                text = " ".join(stem_parts).strip()
                text = R_QNUM.sub("", text).strip()
                idx = ord(sol_letter.upper()) - ord("A")
                if text and 0 <= idx <= 3:
                    q_counter += 1
                    questions.append(Question(str(q_counter), text, opts, idx))
                return

        # --- Intento 2: formato sin letras ---
        opts2 = [o.strip() for o in content[-4:]]
        stem2 = content[:-4]
        text2 = " ".join(stem2).strip()
        text2 = R_QNUM.sub("", text2).strip()

        if not text2:
            return
        if any(not o for o in opts2):
            return
        idx2 = ord(sol_letter.upper()) - ord("A")
        if not (0 <= idx2 <= 3):
            return

        q_counter += 1
        questions.append(Question(str(q_counter), text2, opts2, idx2))

    for ln in lines:
        if not ln:
            continue
        m = R_SOLUTION.match(ln)
        if m:
            flush(m.group(1))
        else:
            chunk.append(ln)

    uniq: Dict[str, Question] = {}
    for q in questions:
        uniq[qkey(q)] = q
    return list(uniq.values())


# -------------------- Quiz helpers --------------------
def build_quiz(bank: List[Question], n: int, seed: Optional[int], shuffle_options: bool):
    rng = random.Random(seed) if seed is not None else random
    sample = rng.sample(bank, k=min(n, len(bank), 100))
    ui_items: List[QuestionUI] = []
    for q in sample:
        if shuffle_options:
            idxs = list(range(4))
            rng.shuffle(idxs)
            new_opts = [q.options[i] for i in idxs]
            mapping = {old: new for new, old in enumerate(idxs)}
            ui_items.append(QuestionUI(options=new_opts, correct=mapping[q.correct]))
        else:
            ui_items.append(QuestionUI(options=list(q.options), correct=q.correct))
    return sample, ui_items


def score(quiz: List[Question], ui: List[QuestionUI]) -> Tuple[int, int, List[int]]:
    wrong_idx = []
    ok = 0
    for k, (q, u) in enumerate(zip(quiz, ui)):
        if u.user is not None and u.user == u.correct:
            ok += 1
        else:
            wrong_idx.append(k)
    return ok, len(quiz), wrong_idx


def reset_attempt_state():
    st.session_state.i = 0
    st.session_state.done = False


def add_wrongs_to_session(quiz: List[Question], ui: List[QuestionUI]):
    _, _, wrong_idx = score(quiz, ui)
    for k in wrong_idx:
        q = quiz[k]
        st.session_state.session_wrong_map[qkey(q)] = q


# -------------------- Streamlit UI --------------------
st.set_page_config(page_title="CREADOR DE TEST sobre 4 opciones (DOCX)", page_icon="📝", layout="wide")
st.title("📝 Test de preguntas con 4 opciones (1 única respuesta válida) ")

with st.sidebar:
    up = st.file_uploader("Sube el DOCX", type=["docx"])
    path_txt = st.text_input("…o ruta local (opcional)", placeholder="C:\\ruta\\Test con respuestas para programa.docx")
    num_q = st.number_input("Número de preguntas", 1, 100, 50, step=1)
    use_seed = st.checkbox("Fijar semilla", value=False)
    seed = st.number_input("Semilla", 0, 10_000_000, 0, step=1, disabled=not use_seed)
    shuffle_opts = st.checkbox("Barajar opciones", value=True)
    start = st.button("🎲 Preparar examen")

# estado
if "bank" not in st.session_state: st.session_state.bank = []
if "quiz" not in st.session_state: st.session_state.quiz = []
if "ui" not in st.session_state:   st.session_state.ui = []
if "i" not in st.session_state:    st.session_state.i = 0
if "done" not in st.session_state: st.session_state.done = False
if "mode" not in st.session_state: st.session_state.mode = "normal"  # normal | review_attempt | review_session
if "session_wrong_map" not in st.session_state: st.session_state.session_wrong_map = {}  # key->Question


def load_docx_bytes() -> Optional[bytes]:
    if up is not None:
        return up.read()
    if path_txt:
        try:
            with open(path_txt, "rb") as f:
                return f.read()
        except Exception as e:
            st.error(f"No se pudo leer el DOCX: {e}")
    return None


if start:
    if not DOCX_OK:
        st.error("Falta dependencia: python-docx. Instala con: pip install python-docx")
    else:
        data = load_docx_bytes()
        if not data:
            st.error("Sube un DOCX o indica una ruta válida.")
        else:
            try:
                bank = parse_docx_questions(data)
            except Exception as e:
                st.error(f"Error al parsear el DOCX: {e}")
                bank = []

            if not bank:
                st.error("No se detectaron preguntas. Verifica que existan líneas tipo 'Solución: a/b/c/d'.")
            else:
                st.session_state.bank = bank
                st.session_state.session_wrong_map = {}

                seed_val = int(seed) if use_seed else None
                quiz, ui = build_quiz(bank, int(num_q), seed_val, shuffle_opts)
                st.session_state.quiz = quiz
                st.session_state.ui = ui
                st.session_state.mode = "normal"
                reset_attempt_state()
                st.success(f"Banco: {len(bank)} preguntas • Examen: {len(quiz)}.")


quiz: List[Question] = st.session_state.quiz
ui: List[QuestionUI] = st.session_state.ui
i: int = st.session_state.i


def render_question(i: int):
    q = quiz[i]
    u = ui[i]

    if st.session_state.mode == "review_attempt":
        title = "Repaso de fallos (este intento)"
    elif st.session_state.mode == "review_session":
        title = "Repaso de fallos (sesión)"
    else:
        title = "Examen"

    st.subheader(f"{title} — Pregunta {i+1} de {len(quiz)}")
    st.write(q.text)
    st.write("")

    # Radio A–D SIN opción extra.
    # Queremos que empiece sin ninguna marcada: index=None (Streamlit reciente).
    opts = [f"{LETTERS[j]}. {u.options[j]}" for j in range(4)]

    radio_key = f"radio_{st.session_state.mode}_{i}"

    # Valor actual (si ya había elegido)
    # Si no, lo dejamos sin seleccionar (index=None).
    if u.user is None:
        chosen = st.radio("Selecciona la respuesta:", options=opts, index=None, key=radio_key)
    else:
        chosen = st.radio("Selecciona la respuesta:", options=opts, index=u.user, key=radio_key)

    # Mapear selección a índice
    if chosen is None:
        u.user = None
    else:
        u.user = opts.index(chosen)

    c1, c2, c3, _ = st.columns([1, 1, 1, 5])
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

    st.markdown("---")


def start_review_from_questions(questions: List[Question], mode_name: str):
    if not questions:
        st.info("No hay preguntas para repasar 🙂")
        return
    seed_val = int(seed) if use_seed else None
    review_quiz, review_ui = build_quiz(questions, min(100, len(questions)), seed_val, shuffle_opts)
    st.session_state.quiz = review_quiz
    st.session_state.ui = review_ui
    st.session_state.mode = mode_name
    reset_attempt_state()
    st.rerun()


def restart_normal_exam():
    bank = st.session_state.bank or []
    if not bank:
        st.warning("Carga el DOCX primero.")
        return
    seed_val = int(seed) if use_seed else None
    new_quiz, new_ui = build_quiz(bank, int(num_q), seed_val, shuffle_opts)
    st.session_state.quiz = new_quiz
    st.session_state.ui = new_ui
    st.session_state.mode = "normal"
    reset_attempt_state()
    st.rerun()


def clear_session_wrongs():
    st.session_state.session_wrong_map = {}
    st.success("Fallos de la sesión limpiados.")


if quiz:
    render_question(i)

    ok, tot, wrong_idx = score(quiz, ui)
    session_wrong_count = len(st.session_state.session_wrong_map)

    st.write(
        f"Aciertos actuales: **{ok}/{tot}** • "
        f"Fallos (este intento): **{len(wrong_idx)}** • "
        f"Fallos acumulados (sesión): **{session_wrong_count}**"
    )

    colA, colB, colC, colD = st.columns([1, 1, 1, 2])

    if colA.button("🏁 Finalizar", key=f"finish_{st.session_state.mode}", disabled=st.session_state.done):
        st.session_state.done = True
        st.rerun()

    if st.session_state.done:
        add_wrongs_to_session(quiz, ui)

        ok, tot, wrong_idx = score(quiz, ui)
        pct = (ok / tot * 100) if tot else 0.0
        st.subheader("Resultados")
        st.write(f"Puntuación: **{ok}/{tot}** ({pct:.1f}%)")
        st.write(f"Fallos (este intento): **{len(wrong_idx)}**")
        st.write(f"Fallos acumulados (sesión): **{len(st.session_state.session_wrong_map)}**")

        if len(wrong_idx) > 0:
            if colB.button("📚 Repasar fallos (este intento)", key="review_attempt"):
                wrong_questions = [quiz[k] for k in wrong_idx]
                start_review_from_questions(wrong_questions, "review_attempt")

        if len(st.session_state.session_wrong_map) > 0:
            if colC.button("🧠 Repasar fallos (sesión)", key="review_session"):
                qs = list(st.session_state.session_wrong_map.values())
                start_review_from_questions(qs, "review_session")

        if colD.button("🔄 Nuevo examen (del banco)", key="restart_normal"):
            restart_normal_exam()

        if st.button("🧹 Limpiar fallos de la sesión"):
            clear_session_wrongs()

else:
    st.info("Sube el DOCX, configura el examen y pulsa **Preparar examen**.")
    if not DOCX_OK:
        st.warning("Instala python-docx: pip install python-docx")
