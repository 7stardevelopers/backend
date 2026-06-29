"""
Seed sub-categories and sub-services for Electrical (s4) and Plumbing (s3)
from Urban Company Vizag rate card CSVs.

Usage:
    python seed_services.py <ADMIN_ACCESS_TOKEN>

The token must belong to a user with role=ADMIN.
To get a token:
  1. Connect to RDS via MySQL Workbench and run:
       UPDATE users SET role = 'ADMIN' WHERE phone = 'YOUR_PHONE';
  2. Run: python send_otp.py (or call POST /auth/send-otp manually)
  3. Verify OTP and copy the access_token from the response.
"""
import sys
import json
import urllib.request
import urllib.error

BASE_URL = "https://1ipuylc4mh.execute-api.ap-south-1.amazonaws.com/Prod"

# ─── Seed data from CSVs ─────────────────────────────────────────────────────
# Prices are in INR (rupees). Durations in minutes.

ELECTRICAL = {
    "service_id": "s4",
    "sub_categories": [
        {
            "name": "Consultation & Inspection",
            "sort_order": 1,
            "items": [
                {"name": "Consultation / Inspection", "price": 147, "duration_minutes": 25, "description": "Issue check & diagnosis"},
            ],
        },
        {
            "name": "Switches & Sockets",
            "sort_order": 2,
            "items": [
                {"name": "Switch / Socket Repair",       "price": 177, "duration_minutes": 22, "description": "Loose or broken switch fix"},
                {"name": "Switch / Socket Replacement",  "price": 207, "duration_minutes": 22, "description": "New switch installation"},
                {"name": "New Switch Installation",      "price": 367, "duration_minutes": 30, "description": "New wiring connection"},
            ],
        },
        {
            "name": "Fans",
            "sort_order": 3,
            "items": [
                {"name": "Fan Installation",      "price": 207, "duration_minutes": 45, "description": "Ceiling fan fitting"},
                {"name": "Fan Repair",            "price": 207, "duration_minutes": 30, "description": "Speed / noise issue fix"},
                {"name": "Fan Regulator Repair",  "price": 147, "duration_minutes": 22, "description": "Regulator fixing"},
                {"name": "Exhaust Fan Installation", "price": 197, "duration_minutes": 30, "description": "Bathroom / kitchen fan fitting"},
            ],
        },
        {
            "name": "Lights",
            "sort_order": 4,
            "items": [
                {"name": "Light Installation",        "price": 247, "duration_minutes": 20, "description": "Ceiling light fitting"},
                {"name": "Tube Light Installation",   "price": 247, "duration_minutes": 30, "description": "Tube light setup"},
                {"name": "Chandelier Installation",   "price": 608, "duration_minutes": 90, "description": "Upto 6-bulb chandelier"},
                {"name": "Outdoor Light Installation","price": 242, "duration_minutes": 45, "description": "Garden / balcony lights"},
                {"name": "Smart Device Installation", "price": 217, "duration_minutes": 45, "description": "Smart switches / home automation"},
            ],
        },
        {
            "name": "MCB & Wiring",
            "sort_order": 5,
            "items": [
                {"name": "MCB Installation",       "price": 197, "duration_minutes": 60, "description": "Single-pole MCB installation"},
                {"name": "MCB Repair",             "price": 177, "duration_minutes": 22, "description": "Fault fixing"},
                {"name": "Wiring Repair (Minor)",  "price": 277, "duration_minutes": 45, "description": "Small wiring fixes (per 5 m)"},
                {"name": "Short Circuit Fix",      "price": 117, "duration_minutes": 60, "description": "Fault detection & repair"},
            ],
        },
        {
            "name": "Geyser & Appliances",
            "sort_order": 6,
            "items": [
                {"name": "Geyser Installation",         "price": 537, "duration_minutes": 45, "description": "Water heater setup"},
                {"name": "Inverter Installation",       "price": 507, "duration_minutes": 90, "description": "Home inverter setup (1 battery)"},
                {"name": "Inverter Repair",             "price": 328, "duration_minutes": 60, "description": "Basic inverter repair"},
            ],
        },
    ],
}

PLUMBING = {
    "service_id": "s3",
    "sub_categories": [
        {
            "name": "Consultation",
            "sort_order": 1,
            "items": [
                {"name": "Plumber Inspection", "price": 147, "duration_minutes": 45, "description": "Issue check & diagnosis"},
            ],
        },
        {
            "name": "Shower & Bath",
            "sort_order": 2,
            "items": [
                {"name": "Shower Installation",           "price": 167, "duration_minutes": 30, "description": "Basic shower fitting"},
                {"name": "Shower Wall Mount",             "price": 217, "duration_minutes": 30, "description": "Wall-mounted shower installation"},
                {"name": "Shower Ceiling Mount",          "price": 217, "duration_minutes": 30, "description": "Ceiling-mounted shower installation"},
                {"name": "Shower Filter Installation",    "price": 247, "duration_minutes": 10, "description": "Water filter for shower"},
                {"name": "Washing Machine Filter Install","price": 177, "duration_minutes": 10, "description": "Filter installation for washing machine"},
            ],
        },
        {
            "name": "Wash Basin & Taps",
            "sort_order": 3,
            "items": [
                {"name": "Tap Installation / Replacement","price": 147, "duration_minutes": 10, "description": "New tap fitting"},
                {"name": "Tap Repair",                   "price": 157, "duration_minutes": 5,  "description": "Fix dripping or stiff tap"},
                {"name": "Sink Pipe Replacement",        "price": 217, "duration_minutes": 30, "description": "Waste pipe below sink"},
                {"name": "Drain Removal",                "price": 217, "duration_minutes": 30, "description": "Sink drain clearing"},
                {"name": "Waste Coupling Installation",  "price": 267, "duration_minutes": 20, "description": "Under-sink waste coupling"},
                {"name": "Drainage Cover Installation",  "price": 197, "duration_minutes": 10, "description": "Drain grate fitting"},
            ],
        },
        {
            "name": "Toilet & Flush",
            "sort_order": 4,
            "items": [
                {"name": "Flush Tank Repair (PVC)",       "price": 197, "duration_minutes": 30, "description": "External PVC flush tank repair"},
                {"name": "Flush Tank Repair (Concealed)", "price": 317, "duration_minutes": 45, "description": "Concealed flush tank repair"},
                {"name": "Toilet Seat Cover Replacement", "price": 177, "duration_minutes": 30, "description": "Toilet seat cover swap"},
                {"name": "Toilet Blockage Removal",       "price": 628, "duration_minutes": 90, "description": "Western toilet pot unblocking"},
                {"name": "Flush Tank Replacement",        "price": 547, "duration_minutes": 60, "description": "Full flush tank swap"},
                {"name": "Jet Spray Installation",        "price": 187, "duration_minutes": 30, "description": "Toilet jet spray fitting"},
            ],
        },
        {
            "name": "Drainage",
            "sort_order": 5,
            "items": [
                {"name": "Bathroom Drainage Removal",     "price": 427, "duration_minutes": 120, "description": "Full bathroom drain clearing"},
                {"name": "Balcony Drainage Removal",      "price": 377, "duration_minutes": 30,  "description": "Balcony drain clearing"},
            ],
        },
        {
            "name": "Tiling",
            "sort_order": 6,
            "items": [
                {"name": "Bathroom Tile Grouting", "price": 1418, "duration_minutes": 120, "description": "Full bathroom tile re-grouting"},
                {"name": "Kitchen Tile Grouting",  "price": 968,  "duration_minutes": 60,  "description": "Kitchen tile grouting"},
            ],
        },
    ],
}

# ─── API helper ───────────────────────────────────────────────────────────────
def api_post(path, body, token):
    url = BASE_URL + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"  ERROR {e.code}: {err}")
        return None

# ─── Main ─────────────────────────────────────────────────────────────────────
def seed(token):
    for svc in [ELECTRICAL, PLUMBING]:
        sid = svc["service_id"]
        print(f"\n{'='*50}")
        print(f"Seeding service: {sid}")
        for i, sc in enumerate(svc["sub_categories"]):
            print(f"  [{i+1}/{len(svc['sub_categories'])}] Sub-category: {sc['name']} ({len(sc['items'])} items)")
            payload = {
                "name":       sc["name"],
                "sort_order": sc["sort_order"],
                "items":      sc["items"],
            }
            result = api_post(f"/admin/services/{sid}/sub-categories", payload, token)
            if result and result.get("status") in ("created", "success"):
                d = result.get("data", {})
                print(f"     ✓ Created sub_category_id={d.get('sub_category_id', '?')} items={d.get('items_created', '?')}")
            else:
                print(f"     ✗ Failed: {result}")
    print("\nDone! Now call GET /services/s3 and GET /services/s4 to verify.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    token = sys.argv[1]
    seed(token)
