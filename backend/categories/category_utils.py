# backend/categories/category_utils.py
import json
import os
from typing import Optional

# Get the directory of this file to load JSON files
CATEGORIES_DIR = os.path.dirname(os.path.abspath(__file__))

# Load category data once at module level
def load_category_data():
    cluster_labels_path = os.path.join(CATEGORIES_DIR, "cluster_labels.json")
    category_clusters_path = os.path.join(CATEGORIES_DIR, "category_clusters.json")
    
    with open(cluster_labels_path, "r") as f:
        cluster_labels = json.load(f)
    
    with open(category_clusters_path, "r") as f:
        category_clusters = json.load(f)
    
    return cluster_labels, category_clusters

# Load data at import time
CLUSTER_LABELS, CATEGORY_CLUSTERS = load_category_data()

# Build reverse mapping: category_name -> cluster_id
CATEGORY_TO_CLUSTER = {}
for cluster_id, categories in CATEGORY_CLUSTERS.items():
    for category in categories:
        CATEGORY_TO_CLUSTER[category] = int(cluster_id)

def get_supercategory_from_cluster_id(cluster_id: int) -> str:
    """Get supercategory from cluster ID"""
    return CLUSTER_LABELS.get(str(cluster_id), "other")

def get_supercategory_from_name(category_name: str) -> str:
    """Get supercategory from category name"""
    if not category_name:
        return "other"
    
    # First try exact match
    cluster_id = CATEGORY_TO_CLUSTER.get(category_name)
    if cluster_id is not None:
        return get_supercategory_from_cluster_id(cluster_id)
    
    # If no exact match, try partial matching
    category_lower = category_name.lower()
    for cat_name, cluster_id in CATEGORY_TO_CLUSTER.items():
        if cat_name.lower() in category_lower or category_lower in cat_name.lower():
            return get_supercategory_from_cluster_id(cluster_id)
    
    return "other"

def get_cluster_id_from_name(category_name: str) -> Optional[int]:
    """Get cluster ID from category name"""
    if not category_name:
        return None
    
    # First try exact match
    cluster_id = CATEGORY_TO_CLUSTER.get(category_name)
    if cluster_id is not None:
        return cluster_id
    
    # If no exact match, try partial matching
    category_lower = category_name.lower()
    for cat_name, cluster_id in CATEGORY_TO_CLUSTER.items():
        if cat_name.lower() in category_lower or category_lower in cat_name.lower():
            return cluster_id
    
    return None