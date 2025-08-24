import streamlit as st
import pandas as pd

st.title("Dashboard")
st.image(
    "https://images.unsplash.com/photo-1465101178521-c1a9136a3b41?auto=format&fit=crop&w=800&q=80",
    caption="Dashboard Overview",
    use_container_width=True
)

# Example data for demonstration
data = pd.DataFrame({
    'Category': ['A', 'B', 'C', 'D'],
    'Value': [10, 23, 7, 15]
})

st.subheader("Data Overview")
st.dataframe(data)

st.subheader("Visualizations")
if st.checkbox("Show Bar Chart"):
    st.bar_chart(data.set_index('Category'))

if st.checkbox("Show Line Chart"):
    st.line_chart(data.set_index('Category'))

st.sidebar.header("Dashboard Options")
option = st.sidebar.selectbox("Select an option", ["Option 1", "Option 2"])

if option == "Option 1":
    st.write("You selected Option 1")
elif option == "Option 2":
    st.write("You selected Option 2")