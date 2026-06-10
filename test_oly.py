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
            body = resp.read().decode('utf-8')
            print("Status: 200 OK")
            print("Raw body sample (first 300 chars):")
            print(body[:300])
            try:
                data = json.loads(body)
                payload = data.get('payload', data)
                if isinstance(payload, dict):
                    print("Parsed payload keys:", list(payload.keys())[:5])
                else:
                    print("Parsed payload is not a dict:", type(payload))
            except Exception as j_err:
                print("Could not parse as JSON:", j_err)
    except urllib.error.HTTPError as e:
        print(f"Status: {e.code}")
        try:
            body = e.fp.read().decode('utf-8')
            print("Error body:", body[:300])
        except Exception as err:
            print("Could not read error body:", err)
    except Exception as e:
        print("Unexpected error:", e)

# Test with bare date parameters and standard date parameters
test_url('https://www.recreation.gov/api/permitoly/4098362/availability?start_date=2026-06-01&end_date=2026-06-30', 'permitoly with bare dates')
test_url('https://www.recreation.gov/api/permitoly/4098362/availability?start_date=2026-06-01T00:00:00.000Z&end_date=2026-06-30T00:00:00.000Z', 'permitoly with ISO dates')
test_url('https://www.recreation.gov/api/permitseki/4098362/availability?start_date=2026-06-01&end_date=2026-06-30', 'permitseki check (just in case)')
