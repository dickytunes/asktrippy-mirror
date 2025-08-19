import json
import psycopg2

# Load category_clusters.json
with open("backend/enrich/category_clustering/category_clusters.json", "r", encoding="utf-8") as f:
    category_clusters = json.load(f)

# Build a reverse map: fsq_category_id -> cluster_id
category_to_cluster = {}
for cluster_id, categories in category_clusters.items():
    for cat in categories:
        category_to_cluster[cat] = int(cluster_id)

# Connect to PostgreSQL
conn = psycopg2.connect(
    dbname="travapture",
    user="postgres",
    password="Ee312Cvst",
    host="localhost",
    port=5432
)
cur = conn.cursor()

# Update each venue
update_sql = """
    UPDATE venues
    SET cluster_id = %s
    WHERE fsq_category_id = %s
"""

count = 0
for fsq_cat_id, cluster_id in category_to_cluster.items():
    cur.execute(update_sql, (cluster_id, fsq_cat_id))
    count += cur.rowcount

conn.commit()
cur.close()
conn.close()

print(f"✅ Updated {count} rows with cluster IDs.")
