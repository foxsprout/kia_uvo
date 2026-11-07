import logging
import sys

# Enable detailed logging to inspect outgoing URLs and response bodies
logging.basicConfig(level=logging.DEBUG)

from custom_components.kia_uvo.api_id import HyundaiBlueLinkApiID

# Region 10 is Indonesia, Brand 1 is Hyundai
client = HyundaiBlueLinkApiID(
    region=10,
    brand=1,
    language="en",  # or "id"
)

EMAIL = "username@email.com"
PASSWORD = "password"
PIN = "1234"

print("[*] Attempting login against Indonesian Bluelink gateway...")
try:
    token = client.login(EMAIL, PASSWORD)
    print(f"[+] Login SUCCESS! Token: {token}")
except Exception as err:
    print(f"[-] Login FAILED: {err}")
    import traceback

    traceback.print_exc()