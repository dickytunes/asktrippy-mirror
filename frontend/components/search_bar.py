import streamlit as st

def render():
    with st.form("search"):
        q = st.text_input("Search", placeholder="late-night ramen near me", help="Natural language")
        c1, c2, c3, c4 = st.columns([1,1,1,1])
        with c1:
            lat = st.number_input("Lat", value=51.5074, format="%.6f")
        with c2:
            lon = st.number_input("Lon", value=-0.1278, format="%.6f")
        with c3:
            radius = st.slider("Radius (m)", min_value=100, max_value=10000, value=1500, step=100)
        with c4:
            limit = st.slider("Results", min_value=5, max_value=30, value=15, step=5)
        submitted = st.form_submit_button("Search")
    return submitted, q.strip(), float(lat), float(lon), int(radius), int(limit)
