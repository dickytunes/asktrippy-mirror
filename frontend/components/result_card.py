import streamlit as st
from frontend.components.job_status import poll
from frontend.services.api import get_job


def render_result_card(venue: dict, job_ids=None):
    """
    Render a single venue card with enrichment data.
    If enrichment is stale/missing and a job is enqueued, show 'Updating…' and poll until completion.
    """

    fsq_id = venue.get("fsq_place_id")
    st.markdown(f"### {venue.get('name', 'Unknown Venue')}")
    st.caption(venue.get("category_name", ""))

    enrichment = venue.get("enrichment", {})

    # Core facts
    description = enrichment.get("description")
    hours = enrichment.get("hours")
    contact = enrichment.get("contact_details")
    price = enrichment.get("price_range")

    # Show enrichment if we have it
    if description:
        st.write(description)

    if hours:
        st.write("**Hours:**")
        for day, val in hours.items():
            st.text(f"{day}: {val}")

    if contact:
        st.write("**Contact:**")
        for k, v in contact.items():
            st.text(f"{k}: {v}")

    if price:
        st.write(f"**Price range:** {price}")

    # Handle stale/missing case
    if not description or not contact or not hours:
        st.warning("Updating… ⏳")

        if job_ids:
            status = poll(job_ids, key=f"poll-{fsq_id}")
            if status:
                # Get latest job info
                job_state = status.get("state")
                job_error = status.get("error")

                if job_state == "success":
                    # trigger refresh to load updated enrichment
                    st.experimental_rerun()
                elif job_state == "fail":
                    st.error(f"Update failed: {job_error or 'unknown error'}")

    # Source links (Safe RAG requirement)
    sources = enrichment.get("sources") or []
    if sources:
        st.write("**Sources:**")
        for url in sources:
            st.markdown(f"- [{url}]({url})", unsafe_allow_html=True)

    # Google Maps link
    lat, lon = venue.get("latitude"), venue.get("longitude")
    if lat and lon:
        maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        st.markdown(f"[View on Maps]({maps_url})", unsafe_allow_html=True)
