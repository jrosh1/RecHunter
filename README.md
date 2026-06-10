# 🌲 RecHunter

**Automated Recreation.gov availability monitoring and SMS alerting agent.**

RecHunter watches for campground, permit, and timed-entry openings on [Recreation.gov](https://www.recreation.gov) and sends you an instant SMS the moment a slot becomes available. Whether you're hunting for a Yosemite cancellation or trying to snag a Half Dome permit at the exact drop time, RecHunter has you covered.

---

## ✨ Features

- **Three watch modes** – Drop-time burst, cancellation polling, and one-shot checks
- **SMS alerts** – Instant notifications via email-to-SMS (no Twilio account needed)
- **Real-time dashboard** – Live event stream, watch management, and search
- **Smart deduplication** – Never get spammed with the same opening twice
- **Drop-time burst scheduling** – Checks every 3 seconds for 90 seconds at the exact release time
- **Facility search** – Search Recreation.gov facilities directly from the dashboard
- **Zero cloud dependencies** – Runs entirely on your local machine or a VPS

---

## 🚀 Quick Start

### 1. Install dependencies

```bash
# Python 3.11+ recommended
pip install -r requirements.txt
```

### 2. Configure environment

```bash
# Copy the example files
cp .env.example .env
cp config.example.yaml config.yaml

# Edit .env with your credentials
# At minimum you need: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, PHONE_NUMBER
```

### 3. Run

```bash
python run.py
```

The dashboard will open automatically at [http://127.0.0.1:8080](http://127.0.0.1:8080).

---

## 🔐 Gmail App Password Setup

RecHunter uses Gmail's SMTP server to send email-to-SMS messages. You need an **App Password** (not your regular Gmail password).

1. **Enable 2-Step Verification** on your Google Account  
   → [myaccount.google.com/security](https://myaccount.google.com/security)

2. **Generate an App Password**  
   → [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)  
   - Select **Mail** as the app  
   - Select **Other** and name it "RecHunter"  
   - Copy the 16-character password

3. **Paste into `.env`**  
   ```env
   GMAIL_ADDRESS=you@gmail.com
   GMAIL_APP_PASSWORD=abcd-efgh-ijkl-mnop
   ```

> **Note:** If you don't see the App Passwords option, make sure 2-Step Verification is enabled first.

---

## ⚙️ Configuration Reference

### Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `GMAIL_ADDRESS` | ✅ | Gmail address for sending SMS |
| `GMAIL_APP_PASSWORD` | ✅ | Gmail app password (16 chars) |
| `PHONE_NUMBER` | ✅ | Your phone number (digits only) |
| `CARRIER_GATEWAY` | ✅ | Email-to-SMS gateway domain |
| `RIDB_API_KEY` | ❌ | RIDB API key for facility search |
| `HOST` | ❌ | Server bind address (default: `127.0.0.1`) |
| `PORT` | ❌ | Server port (default: `8080`) |

### Carrier Gateways

| Carrier | Gateway |
|---|---|
| T-Mobile / Mint Mobile | `tmomail.net` |
| AT&T | `txt.att.net` |
| Verizon | `vtext.com` |
| Sprint | `messaging.sprintpcs.com` |
| Google Fi | `msg.fi.google.com` |
| US Cellular | `email.uscc.net` |
| Cricket | `sms.cricketwireless.net` |

### Watch Configuration (`config.yaml`)

Pre-configure watches in `config.yaml` under the `watches` key:

```yaml
watches:
  - name: "Upper Pines Dec 4"
    facility_id: "232447"
    type: campground           # campground | permit | timed_entry
    date_start: "2026-12-04"
    date_end: "2026-12-05"     # optional for permits
    mode: drop_time            # drop_time | cancellation | one_shot
    drop_time: "10:00 ET"      # required for drop_time mode
    poll_interval_minutes: 10  # fallback interval after burst
```

---

## 🏕️ Supported Reservation Types

| Type | Description | Example Facilities |
|---|---|---|
| **Campground** | Individual campsites | Yosemite Upper Pines, Glacier NP |
| **Permit** | Day-use / wilderness permits | Half Dome, Enchantments, Mt. Whitney |
| **Timed Entry** | Timed-entry reservations | Arches NP, Rocky Mountain NP |

---

## 📡 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/watches` | List all watches |
| `POST` | `/api/watches` | Create a new watch |
| `GET` | `/api/watches/{id}` | Get a specific watch |
| `PUT` | `/api/watches/{id}` | Update a watch |
| `DELETE` | `/api/watches/{id}` | Delete a watch |
| `POST` | `/api/watches/{id}/check` | Trigger an immediate check |
| `GET` | `/api/search?q=query` | Search facilities (RIDB proxy) |
| `GET` | `/api/logs` | Recent event logs |
| `POST` | `/api/notifications/test` | Send a test SMS |
| `GET` | `/api/settings` | Get current settings |
| `PUT` | `/api/settings` | Update settings (runtime only) |
| `GET` | `/api/status` | Engine status |
| `GET` | `/api/events` | SSE event stream |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Dashboard (frontend/)             │
│          HTML/CSS/JS  ←  SSE  ←  REST API           │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│                  FastAPI  (app.py)                   │
│         Routes · SSE · CORS · Static Files          │
└──────┬──────────────┬──────────────┬────────────────┘
       │              │              │
┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼──────┐
│  Monitor    │ │ Database  │ │  Notifier  │
│  Engine     │ │ (SQLite)  │ │  (SMS)     │
│ APScheduler │ │ aiosqlite │ │  Gmail SMTP│
└──────┬──────┘ └───────────┘ └────────────┘
       │
┌──────▼──────────────────────────────────────────────┐
│              Providers                              │
│   Campground · Permit · Timed Entry                 │
│          ↓  httpx  ↓                                │
│     Recreation.gov API                              │
└─────────────────────────────────────────────────────┘
```

---

## 🧪 Testing

Send a test SMS from the dashboard or via the API:

```bash
curl -X POST http://127.0.0.1:8080/api/notifications/test
```

Check engine status:

```bash
curl http://127.0.0.1:8080/api/status
```

For the full test plan and integration tests, see the `tests/` directory.

---

## 📸 Dashboard

<!-- TODO: Add dashboard screenshot -->
*Dashboard screenshot coming soon.*

---

## ⚠️ Disclaimer

RecHunter is an **unofficial tool** and is not affiliated with, endorsed by, or connected to Recreation.gov or the U.S. Department of the Interior.

**Important considerations:**

- This tool is intended for **personal, educational use only**
- Automated access to Recreation.gov may violate their [Terms of Service](https://www.recreation.gov/terms-and-conditions)
- Use reasonable polling intervals (≥ 10 minutes for cancellation watches) to avoid overloading their servers
- The authors assume **no liability** for any consequences of using this software
- **Do not use this tool for commercial purposes** or to resell reservations

By using RecHunter, you acknowledge that you understand these limitations and accept full responsibility for your usage.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
