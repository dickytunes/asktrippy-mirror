import json
from collections import Counter

# Load your category_clusters.json
with open("category_clusters.json", "r", encoding="utf-8") as f:
    category_clusters = json.load(f)

# Define rule-based mappings by keyword
rules = {
    "restaurant": ["restaurant", "bistro", "café", "diner", "eatery", "canteen", "joint", "house", "trattoria", "place"],
    "nightlife": ["bar", "pub", "club", "lounge", "cocktail", "speakeasy"],
    "retail": ["store", "shop", "boutique", "retail", "market"],
    "healthcare": ["clinic", "hospital", "pharmacy", "doctor", "medical", "dentist", "mental", "surgeon", "urgent care"],
    "transport": ["station", "airport", "terminal", "bus", "tram", "metro", "ferry", "train", "taxi", "transport"],
    "education": ["school", "college", "university", "academic"],
    "government": ["city hall", "government", "embassy", "courthouse", "consulate"],
    "accomodation": ["hotel", "hostel", "motel", "inn", "bnb", "bed and breakfast"],
    "entertainment": ["theater", "museum", "zoo", "gallery", "cinema", "attraction", "amusement", "park"],
    "finance": ["bank", "finance", "insurance", "broker", "atm", "exchange"],
    "fitness": ["gym", "fitness", "yoga", "pilates", "wellness", "massage", "trainer"],
    "religion": ["temple", "church", "mosque", "synagogue", "religious, cathedral"],
    "services": ["service", "agency", "repair", "cleaner", "contractor"],
    "residential": ["apartment", "condo", "residence", "retirement", "nursing home"],
}

def match_supercategory(categories):
    flat = " ".join(categories).lower()
    scores = {label: sum(flat.count(k) for k in keywords) for label, keywords in rules.items()}
    top = max(scores.items(), key=lambda x: x[1])
    return top[0] if top[1] > 0 else "other"

# Generate mapping
cluster_labels = {
    cluster_id: match_supercategory(categories)
    for cluster_id, categories in category_clusters.items()
}

# Save to JSON
with open("cluster_labels.json", "w", encoding="utf-8") as f:
    json.dump(cluster_labels, f, indent=2)

print("✅ cluster_labels.json generated with rule-based mapping.")
