import psycopg2
import csv
import json

# Load name → cluster_id map
with open("backend/enrich/category_clustering/category_clusters.json", "r", encoding="utf-8") as f:
    name_clusters = json.load(f)

name_to_cluster = {}
for cluster_id, names in name_clusters.items():
    for name in names:
        name_to_cluster[name] = int(cluster_id)

# Connect to Postgres
conn = psycopg2.connect(
    dbname="travapture",
    user="postgres",
    password="Ee312Cvst",
    host="localhost",
    port=5432
)
cur = conn.cursor()

# Fetch distinct category_id + category_name
cur.execute("""
    SELECT DISTINCT fsq_category_id, category_name
    FROM venues
    WHERE fsq_category_id IS NOT NULL AND category_name IS NOT NULL
""")

rows = cur.fetchall()
output_rows = []

for fsq_id, name in rows:
    cluster_id = name_to_cluster.get(name)
    if cluster_id is not None:
        output_rows.append((fsq_id, cluster_id))

# Write to CSV
with open("backend/fsq_category_id_to_cluster.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["fsq_category_id", "cluster_id"])
    writer.writerows(output_rows)

print(f"✅ Wrote {len(output_rows)} rows to fsq_category_id_to_cluster.csv")
