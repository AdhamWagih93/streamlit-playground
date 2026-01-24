import streamlit as st

from src.admin_config import load_admin_config
from src.theme import set_theme
from src.ai.agents.datagen_agent import run_agent


set_theme(page_title="DataGen Agent", page_icon="🧪")

admin = load_admin_config()
if not admin.is_agent_enabled("datagen", default=True):
    st.info("DataGen agent is disabled by Admin.")
    st.stop()

# --- Custom styling for a modern chat-like experience ---
st.markdown(
    """
    <style>
    .datagen-hero {
        background: linear-gradient(120deg, #0b63d6, #6c5ce7, #00b894);
        border-radius: 18px;
        padding: 1.7rem 1.6rem 1.4rem 1.6rem;
        margin-bottom: 1.2rem;
        color: #fff;
        box-shadow: 0 12px 32px rgba(11, 99, 214, 0.35);
    }
    .datagen-hero-title {
        font-size: 1.7rem;
        font-weight: 800;
        letter-spacing: 0.06em;
        margin-bottom: 0.35rem;
    }
    .datagen-hero-sub {
        font-size: 0.98rem;
        opacity: 0.95;
    }
    .datagen-layout {
        max-width: 1100px;
        margin: 0 auto;
    }
    .datagen-panel {
        background: linear-gradient(145deg, #ffffff, #f3f6fb);
        border-radius: 18px;
        padding: 1.1rem 1.1rem 0.9rem 1.1rem;
        box-shadow: 0 6px 24px rgba(15, 23, 42, 0.10);
        border: 1px solid #d3ddec;
    }
    .datagen-sidebar-card {
        background: #ffffff;
        border-radius: 14px;
        padding: 0.8rem 0.9rem;
        box-shadow: 0 2px 8px rgba(15, 23, 42, 0.10);
        border: 1px solid #e2e8f0;
        font-size: 0.9rem;
    }
    .datagen-hint {
        font-size: 0.8rem;
        color: #64748b;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


if "datagen_messages" not in st.session_state:
    st.session_state.datagen_messages = []  # list[dict(role, content)]


st.markdown("<div class='datagen-layout'>", unsafe_allow_html=True)

st.markdown(
    """
    <div class="datagen-hero">
      <div class="datagen-hero-title">DataGen • Sample Data Agent</div>
      <div class="datagen-hero-sub">
        Ask for realistic sample users and JSON files powered by your
        LangChain + Ollama agent. Great for quickly seeding frontends
        and APIs with believable fake data.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

col_main, col_side = st.columns([2.2, 1])

with col_main:
    st.markdown("<div class='datagen-panel'>", unsafe_allow_html=True)
    st.markdown("### Chat with DataGen", unsafe_allow_html=False)
    st.caption(
        "Describe the sample data you need. For example: "
        "*Generate 25 users and save them to ./data/dev_users.json*.",
    )

    use_chat_api = hasattr(st, "chat_input") and hasattr(st, "chat_message")

    # Render existing conversation
    if use_chat_api:
        for msg in st.session_state.datagen_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        prompt = st.chat_input("Ask DataGen to generate or save data…")
        if prompt:
            st.session_state.datagen_messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.spinner("DataGen is thinking…"):
                ai_msg = run_agent(prompt, history=[])
            content = getattr(ai_msg, "content", str(ai_msg))
            st.session_state.datagen_messages.append({"role": "assistant", "content": content})
            with st.chat_message("assistant"):
                st.markdown(content)
    else:
        # Fallback for older Streamlit: simple text area + button
        for msg in st.session_state.datagen_messages:
            role_label = "You" if msg["role"] == "user" else "DataGen"
            st.markdown(f"**{role_label}:** {msg['content']}")

        prompt = st.text_area("Your request", key="datagen_prompt", height=80)
        if st.button("Send to DataGen") and prompt.strip():
            st.session_state.datagen_messages.append({"role": "user", "content": prompt})
            with st.spinner("DataGen is thinking…"):
                ai_msg = run_agent(prompt, history=[])
            content = getattr(ai_msg, "content", str(ai_msg))
            st.session_state.datagen_messages.append({"role": "assistant", "content": content})

    if st.button("Clear conversation", type="secondary"):
        st.session_state.datagen_messages = []
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

with col_side:
    st.markdown("<div class='datagen-sidebar-card'>", unsafe_allow_html=True)
    st.markdown("**Examples**")
    st.markdown("- Generate 10 users and show them.")
    st.markdown("- Generate 50 finance users and save to ./data/finance_users.json.")
    st.markdown("- Generate QA test users for a login form.")
    st.markdown("<hr style='margin:0.5rem 0;'>", unsafe_allow_html=True)
    st.markdown("**How it works**")
    st.markdown(
        "The underlying agent uses LangChain tools to: "
        "(1) generate users and (2) write/read JSON files on disk.",
    )
    st.markdown(
        "The agent fills in first/last names, domains, and age ranges "
        "automatically based on your request.",
    )
    st.markdown("<div class='datagen-hint'>", unsafe_allow_html=True)
    st.markdown(
        "Tip: include a relative file path like `./data/users.json` "
        "when you want to persist the output.",
    )
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("</div>", unsafe_allow_html=True)
