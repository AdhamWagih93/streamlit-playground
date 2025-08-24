import streamlit as st
import ast
import operator as op
import math


st.set_page_config(page_title="Ultimate Calculator", page_icon="🧮", layout="centered")


# ----- Styles (professional, responsive) -----
st.markdown(
    """
    <style>
    .uc-container {
        max-width: 520px;
        margin: 24px auto;
        background: linear-gradient(180deg, #ffffff 0%, #f4f8ff 100%);
        border-radius: 14px;
        padding: 20px;
        box-shadow: 0 10px 30px rgba(13,38,76,0.08);
        font-family: Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial;
    }
    .uc-header { text-align:center; padding-bottom:8px; }
    .uc-title { font-size:1.6rem; font-weight:700; color:#0b63d6; letter-spacing:0.6px; }
    .uc-sub { color:#51658a; font-size:0.9rem; margin-top:4px }
    .uc-display {
        background: linear-gradient(180deg,#f7fbff,#ffffff);
        border-radius:10px; padding:12px; font-size:1.4rem; text-align:right; border:1px solid #e6eefc; color:#0b2140;
        margin: 12px 0 16px 0;
    }
    .uc-grid { display:grid; grid-template-columns: repeat(4, 1fr); gap:10px }
    .uc-btn { background: linear-gradient(180deg,#0b63d6,#0070e0); color:white; border-radius:10px; padding:12px; font-weight:600; border:none; box-shadow:0 6px 18px rgba(11,99,214,0.16); }
    .uc-btn-op { background: linear-gradient(180deg,#ffffff,#f1f5fb); color:#0b63d6; border-radius:10px; border:1px solid #e6eefc; font-weight:600 }
    .uc-wide { grid-column: span 2 }
    .uc-small { padding:10px; font-size:0.95rem }
    .uc-meta { display:flex; justify-content:space-between; align-items:center; margin-top:12px; color:#6b7b8f; font-size:0.9rem }
    .uc-history { max-height:140px; overflow:auto; padding:8px; background:#ffffff; border-radius:8px; border:1px solid #eef4ff }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----- Safe expression evaluation -----
# Allowed operators and functions
ALLOWED_OPERATORS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.Mod: op.mod,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}

ALLOWED_FUNCTIONS = {k: getattr(math, k) for k in [
    'sqrt', 'sin', 'cos', 'tan', 'log', 'log10', 'ceil', 'floor', 'fabs', 'factorial', 'pow', 'exp', 'degrees', 'radians'
] if hasattr(math, k)}
ALLOWED_FUNCTIONS.update({'abs': abs, 'round': round})


def safe_eval(expr: str):
    """Safely evaluate a math expression using ast."""

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Num):
            return node.n
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            oper = ALLOWED_OPERATORS.get(type(node.op))
            if oper is None:
                raise ValueError("Operator not allowed")
            return oper(left, right)
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            oper = ALLOWED_OPERATORS.get(type(node.op))
            if oper is None:
                raise ValueError("Unary operator not allowed")
            return oper(operand)
        if isinstance(node, ast.Call):
            func_name = node.func.id if isinstance(node.func, ast.Name) else None
            if func_name not in ALLOWED_FUNCTIONS:
                raise ValueError(f"Function '{func_name}' not allowed")
            args = [_eval(a) for a in node.args]
            return ALLOWED_FUNCTIONS[func_name](*args)
        if isinstance(node, ast.Name):
            if node.id in ('pi', 'e'):
                return getattr(math, node.id)
            raise ValueError(f"Name '{node.id}' is not allowed")
        raise ValueError("Expression not allowed")

    parsed = ast.parse(expr, mode='eval')
    return _eval(parsed)


# ----- UI and state -----
if 'uc_display' not in st.session_state:
    st.session_state.uc_display = ''
if 'uc_history' not in st.session_state:
    st.session_state.uc_history = []
if 'uc_memory' not in st.session_state:
    st.session_state.uc_memory = 0.0


def append_char(c):
    st.session_state.uc_display = (st.session_state.uc_display or '') + str(c)


def backspace():
    st.session_state.uc_display = st.session_state.uc_display[:-1]


def clear_all():
    st.session_state.uc_display = ''


def store_memory():
    try:
        st.session_state.uc_memory = float(safe_eval(st.session_state.uc_display))
    except Exception:
        st.session_state.uc_memory = 0.0


def recall_memory():
    append_char(st.session_state.uc_memory)


def evaluate():
    expr = st.session_state.uc_display.strip()
    if not expr:
        return
    try:
        val = safe_eval(expr)
        st.session_state.uc_history.insert(0, f"{expr} = {val}")
        st.session_state.uc_display = str(val)
    except Exception as e:
        st.session_state.uc_history.insert(0, f"{expr} -> ERROR: {e}")
        st.session_state.uc_display = 'ERROR'


# Layout
st.markdown('<div class="uc-container">', unsafe_allow_html=True)
st.markdown('<div class="uc-header"><div class="uc-title">Ultimate Calculator</div><div class="uc-sub">Professional, safe, and beautiful</div></div>', unsafe_allow_html=True)

st.markdown(f'<div class="uc-display">{st.session_state.uc_display or "0"}</div>', unsafe_allow_html=True)

cols = st.columns([1, 1, 1, 1])
with cols[0]:
    if st.button('MC'):
        st.session_state.uc_memory = 0.0
with cols[1]:
    if st.button('MR'):
        recall_memory()
with cols[2]:
    if st.button('M+'):
        store_memory()
with cols[3]:
    if st.button('C'):
        clear_all()

buttons = [
    '7', '8', '9', '/',
    '4', '5', '6', '*',
    '1', '2', '3', '-',
    '0', '.', '(', ')',
]

st.markdown('<div class="uc-grid">', unsafe_allow_html=True)
for b in buttons:
    if b in ('/', '*', '-', '+'):
        if st.button(b, key=f'op_{b}', help=f'Operator {b}', args=None):
            append_char(b)
    else:
        if st.button(b, key=f'btn_{b}'):
            append_char(b)
st.markdown('</div>', unsafe_allow_html=True)

op_cols = st.columns([1,1,1])
with op_cols[0]:
    if st.button('⌫'):
        backspace()
with op_cols[1]:
    if st.button('^'):
        append_char('**')
with op_cols[2]:
    if st.button('%'):
        append_char('%')

if st.button('Evaluate', key='eval'):
    evaluate()

st.markdown('<div class="uc-meta"><div>History</div><div>Memory: <b>{}</b></div></div>'.format(st.session_state.uc_memory), unsafe_allow_html=True)
st.markdown(f'<div class="uc-history">' + '<br>'.join(st.session_state.uc_history[:20]) + '</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

st.markdown("<div style='text-align:center; margin-top:14px; color:#74839a; font-size:0.9rem;'>Made with care • Safe eval • Math functions: sqrt, sin, cos, tan, log, log10, exp, etc.</div>", unsafe_allow_html=True)
