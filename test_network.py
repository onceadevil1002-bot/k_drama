import socket
import subprocess
import sys

def test_connectivity(host, port):
    """Test if we can connect to a host:port"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        return False

def main():
    mongodb_host = "ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net"
    mongodb_port = 27017
    
    print("🔍 Testing network connectivity to MongoDB...")
    print(f"Host: {mongodb_host}")
    print(f"Port: {mongodb_port}")
    print()
    
    # Test DNS resolution
    try:
        ip_address = socket.gethostbyname(mongodb_host)
        print(f"✅ DNS Resolution: {mongodb_host} -> {ip_address}")
    except socket.gaierror:
        print("❌ DNS Resolution: Failed to resolve hostname")
        print("   Check your internet connection or DNS settings")
        return
    
    # Test basic connectivity
    if test_connectivity(mongodb_host, mongodb_port):
        print("✅ Network Connectivity: Can connect to MongoDB server")
        print("   The issue might be with MongoDB authentication or configuration")
    else:
        print("❌ Network Connectivity: Cannot connect to MongoDB server")
        print("   Possible issues:")
        print("   - Firewall blocking port 27017")
        print("   - MongoDB server not running")
        print("   - Network connectivity issues")
        print("   - IP not whitelisted in MongoDB Atlas")
    
    print()
    print("💡 Next steps:")
    print("1. Check MongoDB Atlas dashboard (https://cloud.mongodb.com)")
    print("2. Verify your IP is whitelisted in Network Access")
    print("3. Check if cluster is running and not paused")
    print("4. Verify MongoDB connection string credentials")

if __name__ == "__main__":
    main()
