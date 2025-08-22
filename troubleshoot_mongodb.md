# MongoDB Connection Troubleshooting Guide

## Issue: ServerSelectionTimeoutError when connecting to MongoDB Atlas

### 1. Check MongoDB Atlas Dashboard
- Log in to https://cloud.mongodb.com
- Check if your cluster is running (not paused)
- Verify your cluster name matches the one in your connection string

### 2. IP Whitelisting
- In MongoDB Atlas, go to Network Access
- Add your current IP address to the whitelist
- You can also add `0.0.0.0/0` to allow all IPs (less secure)

### 3. Check Connection String
- Verify your MONGO_URI in the .env file has the correct format:
  ```
  mongodb+srv://username:password@cluster.mongodb.net/database?retryWrites=true&w=majority
  ```
- Ensure username and password are correct

### 4. Network Testing
- Check if you can ping the MongoDB server:
  ```bash
  ping ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net
  ```

### 5. Test with MongoDB Compass
- Download MongoDB Compass and try connecting with your URI
- This will help isolate if the issue is with your code or the connection

### 6. Temporary Workaround (Local MongoDB)
If you can't fix the Atlas connection immediately, you can:
1. Install MongoDB locally
2. Update your .env file to use:
   ```
   MONGO_URI=mongodb://localhost:27017
   ```
3. Run the bot with local MongoDB

### 7. Check Firewall/Antivirus
- Temporarily disable firewall/antivirus to test if they're blocking the connection

## Quick Test Commands:
```bash
# Test basic connectivity
telnet ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net 27017

# Test DNS resolution
nslookup ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net
```

If the issue persists, check MongoDB Atlas status page for any ongoing outages.
