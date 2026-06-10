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
                if 'availability' in payload:
                    print("Has availability key. Inner keys:", list(payload['availability'].keys())[:5])
            else:
                print("Payload is not a dict:", type(payload))
    except urllib.error.HTTPError as e:
        print(f"Status: {e.code}")
        try:
            body = e.fp.read().decode('utf-8')
            print("Error body:", body)
        except Exception as err:
            print("Could not read error body:", err)
    except Exception as e:
        print("Unexpected error:", e)

# Test standard permit endpoint
test_url('https://www.recreation.gov/api/permits/4098362/availability?start_date=2026-06-01T00:00:00.000Z&end_date=2026-06-30T00:00:00.000Z', 'Standard Endpoint (first day to last day)')
test_url('https://www.recreation.gov/api/permits/4098362/availability?start_date=2026-06-01T00:00:00.000Z&end_date=2026-07-01T00:00:00.000Z', 'Standard Endpoint (start to next month first)')

# Test permitinyo endpoint
test_url('https://www.recreation.gov/api/permitinyo/4098362/availability?start_date=2026-06-01T00:00:00.000Z&end_date=2026-06-30', 'PermitInyo Endpoint (with T00 start)')
test_url('https://www.recreation.gov/api/permitinyo/4098362/availability?start_date=2026-06-01&end_date=2026-06-30', 'PermitInyo Endpoint (bare dates)')
