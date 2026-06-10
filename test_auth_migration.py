import asyncio
from recsniper.database import get_db
from recsniper.models import Watch, ReservationType, WatchMode, WatchStatus
from datetime import date
import uuid

async def test_all():
    print("Initializing Database & running migrations...")
    async with get_db() as db:
        # 1. Verify tables exist
        cursor = await db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in await cursor.fetchall()]
        print("Tables in database:", tables)
        assert "users" in tables, "users table missing"
        assert "otps" in tables, "otps table missing"
        assert "watches" in tables, "watches table missing"
        
        # 2. Verify user_id column in watches table
        cursor = await db._conn.execute("PRAGMA table_info(watches)")
        columns = [row["name"] for row in await cursor.fetchall()]
        print("Watches table columns:", columns)
        assert "user_id" in columns, "user_id column missing from watches table"

        # 3. Clean up any existing test users to run test cleanly
        await db._conn.execute("DELETE FROM otps WHERE user_id IN (SELECT id FROM users WHERE username IN ('user_a', 'user_b'))")
        await db._conn.execute("DELETE FROM watches WHERE user_id IN (SELECT id FROM users WHERE username IN ('user_a', 'user_b'))")
        await db._conn.execute("DELETE FROM users WHERE username IN (?, ?)", ("user_a", "user_b"))
        await db._conn.commit()

        # 4. Create User A and User B
        print("Creating User A and User B...")
        user_a_id = str(uuid.uuid4())
        user_b_id = str(uuid.uuid4())
        
        await db.create_user(
            user_id=user_a_id,
            username="user_a",
            phone_number="@user_a_tg",
            carrier_gateway="telegram",
            callmebot_key="apikey_a"
        )
        await db.create_user(
            user_id=user_b_id,
            username="user_b",
            phone_number="@user_b_tg",
            carrier_gateway="telegram",
            callmebot_key="apikey_b"
        )
        
        # Verify get_user_by_username
        user_a = await db.get_user_by_username("user_a")
        assert user_a["id"] == user_a_id
        
        # 5. Test OTP generation and verification
        print("Testing OTP flow...")
        await db.create_otp(user_a_id, "123456", expires_in_minutes=5)
        # Verify valid OTP
        is_valid = await db.verify_otp(user_a_id, "123456")
        assert is_valid is True, "Valid OTP verification failed"
        
        # Verify it got deleted (replay protection)
        is_valid_retry = await db.verify_otp(user_a_id, "123456")
        assert is_valid_retry is False, "Replay protection failed"

        # Test expired/invalid OTP
        await db.create_otp(user_a_id, "654321", expires_in_minutes=-1) # expired
        is_valid_expired = await db.verify_otp(user_a_id, "654321")
        assert is_valid_expired is False, "Expired OTP verification should fail"

        # 6. Test Watch Isolation
        print("Testing Watch Isolation...")
        # Create watch for User A
        watch_a = Watch(
            user_id=user_a_id,
            name="User A Watch",
            facility_id="123",
            reservation_type=ReservationType.CAMPGROUND,
            date_start=date(2026, 7, 1),
            mode=WatchMode.CANCELLATION
        )
        await db.create_watch(watch_a)
        
        # Create watch for User B
        watch_b = Watch(
            user_id=user_b_id,
            name="User B Watch",
            facility_id="456",
            reservation_type=ReservationType.PERMIT,
            date_start=date(2026, 7, 15),
            mode=WatchMode.CANCELLATION
        )
        await db.create_watch(watch_b)

        # List watches for User A
        watches_a = await db.list_watches(user_id=user_a_id)
        assert len(watches_a) == 1, "User A should have exactly 1 watch"
        assert watches_a[0].name == "User A Watch"

        # List watches for User B
        watches_b = await db.list_watches(user_id=user_b_id)
        assert len(watches_b) == 1, "User B should have exactly 1 watch"
        assert watches_b[0].name == "User B Watch"

        # Clean up test watches and users
        await db.delete_watch(watch_a.id, user_id=user_a_id)
        await db.delete_watch(watch_b.id, user_id=user_b_id)
        await db._conn.execute("DELETE FROM otps WHERE user_id IN (?, ?)", (user_a_id, user_b_id))
        await db._conn.execute("DELETE FROM users WHERE id IN (?, ?)", (user_a_id, user_b_id))
        await db._conn.commit()
        
    print("All tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(test_all())
