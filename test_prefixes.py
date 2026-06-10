import urllib.request
import urllib.error
import json

headers = {'User-Agent': 'Mozilla/5.0'}

prefixes = [
    'permitinyo',
    'permitseki',
    'permitoly',
    'permitolympic',
    'permitpnw',
    'permitwa',
    'permitwest',
    'permitshasta',
    'permitboundarywaters',
    'permitbwca',
    'permitdetailed',
]

for prefix in prefixes:
    url = f'https://www.recreation.gov/api/{prefix}/4098362/availability?start_date=2026-06-01&end_date=2026-06-30'
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode('utf-8')
            # If it returns HTML, it's just the fallback route, skip it
            if body.strip().startswith('<!doctype html>'):
                # print(f"{prefix}: Serves index.html fallback (ignored)")
                continue
            print(f"=== Found active endpoint for {prefix}! ===")
            print("Status: 200 OK")
            print("Body sample:", body[:200])
    except urllib.error.HTTPError as e:
        body = e.fp.read().decode('utf-8')
        if "Page Not Found" in body:
            # 404 Page Not Found means the prefix doesn't exist at all on the gateway
            continue
        print(f"=== Found matching prefix {prefix} but got error === ")
        print(f"Status: {e.code}")
        print("Body:", body[:200])
    except Exception as e:
        pass
