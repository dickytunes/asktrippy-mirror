import json
import csv

# Load your local category_clusters.json
with open("backend/enrich/category_clustering/category_clusters.json", "r", encoding="utf-8") as f:
    category_clusters = json.load(f)

# Build rows: fsq_category_id, cluster_id
rows = []
for cluster_id, categories in category_clusters.items():
    for cat in categories:
        rows.append((cat, int(cluster_id)))

# Write to CSV
with open("backend/fsq_category_to_cluster.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["fsq_category_id", "cluster_id"])
    writer.writerows(rows)

print("✅ fsq_category_to_cluster.csv written.")
