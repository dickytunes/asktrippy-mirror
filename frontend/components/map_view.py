import streamlit as st
import json

TILE = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"

def render(center_lat, center_lon, results):
    # Simple Leaflet embed (no extra deps)
    points = [
        {"lat": r["latitude"], "lon": r["longitude"], "name": r["name"]} for r in results
    ]
    m = f"""
<div id="map" style="height: 420px;"></div>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
  var map = L.map('map').setView([{center_lat}, {center_lon}], 14);
  L.tileLayer('{TILE}', {{ maxZoom: 19 }}).addTo(map);
  var pts = {json.dumps(points)};
  pts.forEach(function(p) {{
    L.marker([p.lat, p.lon]).addTo(map).bindPopup(p.name);
  }});
</script>
"""
    st.components.v1.html(m, height=440)
