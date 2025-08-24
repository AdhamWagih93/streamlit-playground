import streamlit as st

def about_page():
    st.title("About This Website")
    st.image(
        "https://images.unsplash.com/photo-1465101046530-73398c7f28ca?auto=format&fit=crop&w=800&q=80",
        caption="About our platform",
        use_container_width=True
    )
    st.write("""
        Welcome to the best Streamlit website in the world! This platform is designed to provide users with an intuitive and interactive experience.
        
        ### Purpose
        Our goal is to showcase the capabilities of Streamlit and provide valuable insights through data visualization and user-friendly interfaces.
        
        ### Features
        - Interactive dashboards
        - User authentication
        - Custom themes and styles
        - Easy navigation across multiple pages
        
        ### Get Involved
        We encourage contributions and feedback. If you have suggestions or would like to collaborate, please reach out!
    """)

if __name__ == "__main__":
    about_page()