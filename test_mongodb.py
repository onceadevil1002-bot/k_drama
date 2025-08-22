import os
from pymongo import MongoClient
from dotenv import load_dotenv
import sys

# Load environment variables
load_dotenv()

# Get MongoDB URI
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("❌ MONGO_URI environment variable is not set")
    print("Please check your .env file and ensure it contains:")
    print("MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/")
    sys.exit(1)

print(f"🔗 MongoDB URI found: {MONGO_URI[:MONGO_URI.find('@')+1]}...")  # Hide password

try:
    # Test connection with longer timeout
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=15000)
    
    # Test the connection
    client.admin.command('ping')
    print("✅ MongoDB connection successful!")
    
    # List databases to verify access
    dbs = client.list_database_names()
    print(f"📊 Available databases: {dbs}")
    
    # Check if kdrama database exists
    if 'kdrama' in dbs:
        db = client['kdrama']
        collections = db.list_collection_names()
        print(f"📁 Collections in kdrama database: {collections}")
    else:
        print("ℹ️ kdrama database doesn't exist yet")
        
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")
    print("\n🔧 Troubleshooting steps:")
    print("1. Check if your MongoDB Atlas cluster is running")
    print("2. Verify your MONGO_URI in the .env file")
    print("3. Check your internet connection")
    print("4. Ensure your IP is whitelisted in MongoDB Atlas")
    print("5. Check if your MongoDB credentials are correct")
