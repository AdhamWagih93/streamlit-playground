import streamlit as st
from src.theme import set_theme

set_theme()

# Custom CSS for a beautiful landing page
st.markdown(
    """
    <style>
    .main-hero {
        background: linear-gradient(120deg, #e0eafc 0%, #cfdef3 100%);
        border-radius: 18px;
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.15);
        padding: 2.5rem 2rem 2rem 2rem;
        max-width: 900px;
        margin: 2.5rem auto 1.5rem auto;
        text-align: center;
    }
    .main-hero h1 {
        font-size: 2.8rem;
        font-weight: 800;
        color: #0b63d6;
        margin-bottom: 0.5rem;
        letter-spacing: 1.5px;
    }
    .main-hero h2 {
        font-size: 1.5rem;
        color: #51658a;
        font-weight: 400;
        margin-bottom: 1.2rem;
    }
    .main-hero img {
        border-radius: 12px;
        margin: 1.5rem 0 1rem 0;
        box-shadow: 0 4px 16px rgba(11,99,214,0.10);
    }
    .main-hero .cta {
        margin-top: 1.5rem;
    }
    .main-hero .cta button {
        background: linear-gradient(90deg, #007bff 0%, #00c6ff 100%);
        color: white;
        font-size: 1.2rem;
        border: none;
        border-radius: 8px;
        padding: 0.7rem 2.2rem;
        font-weight: 600;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        transition: background 0.2s;
        cursor: pointer;
    }
    .main-hero .cta button:hover {
        background: linear-gradient(90deg, #0056b3 0%, #007bff 100%);
    }
    .main-hero .desc {
        color: #3a4a6b;
        font-size: 1.1rem;
        margin-bottom: 1.2rem;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown('<div class="main-hero">', unsafe_allow_html=True)
st.markdown('<h1>Best Streamlit Website</h1>', unsafe_allow_html=True)
st.markdown('<h2>Your one-stop solution for beautiful, interactive data apps</h2>', unsafe_allow_html=True)
st.image(
    "https://images.unsplash.com/photo-1506744038136-46273834b3fb?auto=format&fit=crop&w=900&q=80",
    caption="Welcome to our platform!",
    use_container_width=True
)
st.markdown('<div class="desc">Explore dashboards, calculators, team management, and more — all in one place. Use the sidebar to navigate through the pages and discover powerful features designed for teams and individuals alike.</div>', unsafe_allow_html=True)
st.markdown('<div class="cta">', unsafe_allow_html=True)
if st.button("Get Started 🚀"):
    st.success("Use the sidebar to access all features!")
st.markdown('</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)
