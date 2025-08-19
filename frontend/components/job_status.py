import streamlit as st
import time
from frontend.services.api import get_job

def poll(job_ids, key="poll"):
    if not job_ids:
        return None

    # Poll up to 15s (every 2s)
    for _ in range(8):
        for jid in job_ids:
            try:
                status = get_job(jid)
                state = status.get("state")
                if state in ("success", "fail"):
                    return status
            except Exception:
                pass
        # sleep then rerun
        time.sleep(2)
        st.experimental_rerun()

    return None
