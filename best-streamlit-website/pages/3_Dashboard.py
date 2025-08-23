import streamlit as st
import pandas as pd
from src.auth import check_login
from src.utils import load_data

# Check if the user is logged in
if not check_login():
    st.warning("Please log in to access the dashboard.")
    st.stop()

# Set the title of the dashboard
st.title("Dashboard")

# Load data for the dashboard
data = load_data()

# Display data in a table
st.subheader("Data Overview")
st.dataframe(data)

# Create interactive visualizations
st.subheader("Visualizations")
if st.checkbox("Show Histogram"):
    st.bar_chart(data['column_name'])  # Replace 'column_name' with the actual column name

if st.checkbox("Show Line Chart"):
    st.line_chart(data['column_name'])  # Replace 'column_name' with the actual column name

# Add any additional dashboard components here
st.sidebar.header("Dashboard Options")
option = st.sidebar.selectbox("Select an option", ["Option 1", "Option 2"])

if option == "Option 1":
    st.write("You selected Option 1")
elif option == "Option 2":
    st.write("You selected Option 2")