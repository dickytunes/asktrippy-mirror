import json
from pathlib import Path
from sentence_transformers import SentenceTransformer
import hdbscan

# Load category list
category_path = Path(__file__).parent / "fsq_categories.txt"
with open(category_path, "r", encoding="utf-8") as f:
    categories = sorted(set(line.strip() for line in f if line.strip()))

print(f"🔍 Loaded {len(categories)} unique categories")

# Embed categories
model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
embeddings = model.encode(categories, show_progress_bar=True)

# Run HDBSCAN
clusterer = hdbscan.HDBSCAN(min_cluster_size=2, min_samples=1, metric="euclidean")
labels = clusterer.fit_predict(embeddings)

# Build initial cluster map
cluster_map = {}
for label, category in zip(labels, categories):
    if label == -1:
        continue
    cluster_map.setdefault(str(label), []).append(category)

# Check what was clustered
clustered = set(cat for group in cluster_map.values() for cat in group)
missing = sorted(set(categories) - clustered)

# Add missing as singleton clusters
max_id = max(int(k) for k in cluster_map.keys()) if cluster_map else 0
for i, cat in enumerate(missing, start=max_id + 1):
    cluster_map[str(i)] = [cat]

print(f"✅ Clustered {len(categories)} categories into {len(cluster_map)} groups (after singleton fallback)")

# Save output
out_path = Path(__file__).parent / "category_clusters.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(cluster_map, f, indent=2, ensure_ascii=False)
