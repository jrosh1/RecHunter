import urllib.request
import urllib.error
import urllib.parse
import json

headers = {'User-Agent': 'Mozilla/5.0'}
url = 'https://www.recreation.gov/api/permitinyo/4098362/availabilityv2?start_date=2026-06-01&end_date=2026-06-30&commercial_acct=false'
req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req) as resp:
        print("Success!")
except urllib.error.HTTPError as e:
    print("Status:", e.code)
    print("Body:", e.fp.read().decode('utf-8'))
