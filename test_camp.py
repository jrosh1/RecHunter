import urllib.request
import urllib.error
import urllib.parse
import json

headers = {'User-Agent': 'Mozilla/5.0'}
params = {'start_date': '2026-06-01T00:00:00.000Z'}
query_string = urllib.parse.urlencode(params)
url = f'https://www.recreation.gov/api/camps/availability/campground/4098362/month?{query_string}'

req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req) as resp:
        print("Success!")
        body = resp.read().decode('utf-8')
        print(body[:200])
except urllib.error.HTTPError as e:
    print("Status:", e.code)
    print("Body:", e.fp.read().decode('utf-8'))
