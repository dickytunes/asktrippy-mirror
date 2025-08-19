import os, requests

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

def post_query(query: str, lat: float, lon: float, radius_m: int, limit: int = 15):
    try:
        r = requests.post(f"{API_BASE}/query", json={
            "query": query, "lat": lat, "lon": lon, "radius_m": radius_m, "limit": limit
        }, timeout=30)  # Reduced from 60s to 30s to match backend timeout
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        raise Exception("Search timed out after 30 seconds. The system may be experiencing high load.")
    except requests.exceptions.ConnectionError:
        raise Exception("Cannot connect to search service. Please check if the backend is running.")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Search request failed: {e}")

def get_job(job_id: int):
    try:
        r = requests.get(f"{API_BASE}/scrape/{job_id}", timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        raise Exception("Job status check timed out.")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Job status check failed: {e}")
