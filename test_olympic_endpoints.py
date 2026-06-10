import urllib.request
import urllib.error
import json

headers = {'User-Agent': 'Mozilla/5.0'}

def test_url(url, label):
    print(f"\n=== Testing {label} ===")
    print(f"URL: {url}")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print("Status: 200 OK")
            payload = data.get('payload', data)
            if isinstance(payload, dict):
                print("Payload keys:", list(payload.keys())[:5])
            else:
                print("Payload is not a dict:", type(payload))
    except urllib.error.HTTPError as e:
        print(f"Status: {e.code}")
        try:
            body = e.fp.read().decode('utf-8')
            print("Error body:", body[:200])
        except Exception as err:
            print("Could not read error body:", err)
    except Exception as e:
        print("Unexpected error:", e)

# Test different variations of endpoints for Olympic
test_url('https://www.recreation.gov/api/permits/4098362/availability/divisions?start_date=2026-06-01T00:00:00.000Z&end_date=2026-06-30T00:00:00.000Z', 'divisions sub-path')
test_url('https://www.recreation.gov/api/permits/4098362/divisions/availability?start_date=2026-06-01T00:00:00.000Z&end_date=2026-06-30T00:00:00.000Z', 'divisions/availability path')
test_url('https://www.recreation.gov/api/permits/4098362/availability?start_date=2026-06-01T00:00:00.000Z&end_date=2026-06-30T00:00:00.000Z&commercial=false', 'availability with commercial=false')
test_url('https://www.recreation.gov/api/permits/4098362/availability?start_date=2026-06-01T00:00:00.000Z&end_date=2026-06-30T00:00:00.000Z&permit_type_id=4098362', 'availability with permit_type_id')
test_url('https://www.recreation.gov/api/permits/4098362/availability?start_date=2026-06-01T00:00:00.000Z&end_date=2026-06-30T00:00:00.000Z&division_id=4098362036', 'availability with division_id')
test_url('https://www.recreation.gov/api/permits/4098362/availability?start_date=2026-06-01T00:00:00.000Z&end_date=2026-06-30T00:00:00.000Z&is_national_park=true', 'availability with is_national_park')
