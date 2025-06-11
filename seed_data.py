import os
from pymongo import MongoClient
from dotenv import load_dotenv

# Load .env variables
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")

# Connect to MongoDB
client = MongoClient(MONGO_URI)
db = client['kdrama']
collection = db['shows']

# Optional: Clean previous data from these categories (avoid duplication)
collection.delete_many({"category": {"$in": ["Japanese Drama", "C Drama", "Arabic"]}})

# Sample data
sample_data = [
    {
        "category": "Japanese Drama",
        "show_name": "Tokyo Love Story",
        "episodes": {
            "1": ["Ep 1", "Ep 2", "Ep 3"],
            "2": ["Ep 1", ["Split 1", "Split 2"], "Ep 3"]
        }
    },
    {
        "category": "C Drama",
        "show_name": "Falling Into Your Smile",
        "episodes": {
            "1": ["Ep 1", "Ep 2", "Ep 3"],
            "2": ["Ep 1", "Ep 2", ["Part 1", "Part 2", "Part 3"]]
        }
    },
    {
        "category": "Arabic",
        "show_name": "Bab Al-Hara",
        "episodes": {
            "1": ["Ep 1", "Ep 2", ["Split A", "Split B"]],
            "2": ["Ep 1", "Ep 2", "Ep 3"]
        }
    }
]

# Insert into collection
collection.insert_many(sample_data)
print("✅ Sample categories inserted successfully.")