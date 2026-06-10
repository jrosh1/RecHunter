import asyncio
from fastapi.testclient import TestClient
from recsniper.app import app
from recsniper.database import get_singleton_db
from recsniper.config import settings
from recsniper.utils import sign_token
import uuid

async def run_tests():
    # Initialise the database so we can insert a user
    db = await get_singleton_db(settings.db_path)
    
    # Clean up existing test user if any
    await db._conn.execute("DELETE FROM users WHERE username = ?", ("test_routing_user",))
    await db._conn.commit()
    
    # Insert test user
    user_id = str(uuid.uuid4())
    await db.create_user(
        user_id=user_id,
        username="test_routing_user",
        phone_number="1234567890",
        carrier_gateway="telegram",
        callmebot_key="testkey"
    )
    
    # Create valid session token
    token = sign_token({"user_id": user_id, "username": "test_routing_user"})
    
    with TestClient(app) as client:
        # 1. Unauthenticated root redirect to /login
        resp1 = client.get("/", follow_redirects=False)
        print("GET / (unauthenticated): status", resp1.status_code, "Location:", resp1.headers.get("location"))
        assert resp1.status_code == 307
        assert resp1.headers.get("location") == "/login"

        # 2. Unauthenticated /login serving 200 OK
        resp2 = client.get("/login", follow_redirects=False)
        print("GET /login (unauthenticated): status", resp2.status_code)
        assert resp2.status_code == 200
        assert "RecHunter — Log In" in resp2.text

        # 3. Authenticated root serving 200 OK
        client.cookies.set("recsniper_session", token)
        resp3 = client.get("/", follow_redirects=False)
        print("GET / (authenticated): status", resp3.status_code)
        assert resp3.status_code == 200
        assert "RecHunter — Recreation.gov Monitor" in resp3.text

        # 4. Authenticated /login redirect to /
        resp4 = client.get("/login", follow_redirects=False)
        print("GET /login (authenticated): status", resp4.status_code, "Location:", resp4.headers.get("location"))
        assert resp4.status_code == 307
        assert resp4.headers.get("location") == "/"
        
        # Clean up test user while connection is still open
        await db._conn.execute("DELETE FROM users WHERE username = ?", ("test_routing_user",))
        await db._conn.commit()
        
    print("All routing tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_tests())
