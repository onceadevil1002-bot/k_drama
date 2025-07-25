from pymongo import MongoClient
from pprint import pprint

client = MongoClient("mongodb://kdrama_bot:show@ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net:27017/admin?ssl=true")
db = client["k_drama"]
collection = db["shows"]


print("🔍 Checking first few documents:\n")

for doc in collection.find().limit(5):
    pprint(doc)
