import streamlit as st

def main():
    st.title("Welcome to the Best Streamlit Website!")
    st.subheader("Your one-stop solution for amazing data insights.")
    
    st.write("""
        This website is designed to provide users with an interactive experience 
        through various features including dashboards, informative pages, and more.
    """)
    
    st.image("assets/welcome_image.png", caption="Welcome to our platform!", use_column_width=True)
    
    st.write("Explore the navigation menu to learn more about our features and offerings.")
    
    if st.button("Get Started"):
        st.write("Let's dive into the features of our website!")

if __name__ == "__main__":
    main()