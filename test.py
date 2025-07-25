from pymongo import MongoClient
from pprint import pprint

# Replace with your existing connection info
MONGO_URI = "mongodb://kdrama_bot:show@ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net:27017/admin?ssl=true"
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = client['kdrama']
collection = db['shows']

# Replace with your actual values
category = "Hindi Dubbed"
show_name = "Head Over Heels"

doc = collection.find_one({
    "category": category,
    "show_name": show_name
})

pprint(doc)