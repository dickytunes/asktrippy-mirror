import streamlit as st
import sys
import os

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frontend.components.search_bar import render as search_bar
from frontend.components.result_card import render_result_card
from frontend.components.map_view import render as map_view
from frontend.services.api import post_query, get_job

st.set_page_config(page_title="Voy8", layout="wide")

st.title("Voy8 — Travel Intelligence (MVP)")
submitted, q, lat, lon, radius, limit = search_bar()

if submitted and q:
    with st.spinner("Searching…"):
        try:
            data = post_query(q, lat, lon, radius, limit)
            results = data.get("results", [])
        except Exception as e:
            st.error(f"Search failed: {e}")
            results = []

    left, right = st.columns([1.2, 1])
    with left:
        # Cards
        job_ids = []
        for r in results:
            render_result_card(r)
            if r.get("job_id"):
                job_ids.append(r["job_id"])
        if job_ids:
            st.info("Some venues are being refreshed. This page will update when they complete.")
            # lightweight poll: re-fetch statuses and rerun when any reaches success
            try:
                import time
                for _ in range(10):  # up to ~10 * 1s
                    done = 0
                    for jid in job_ids:
                        st.caption(f"Job {jid}…")
                        st.session_state[f"job_{jid}"] = get_job(jid)
                        if st.session_state[f"job_{jid}"]["state"] in ("success", "fail"):
                            done += 1
                    if done:
                        st.experimental_rerun()
                    time.sleep(1)
            except Exception:
                pass

    with right:
        map_view(lat, lon, results)
