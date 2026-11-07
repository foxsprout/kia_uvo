from custom_components.kia_uvo.const import REGIONS, REGION_INDONESIA
import custom_components.kia_uvo.api_id  # Triggers monkey-patch
from hyundai_kia_connect_api import VehicleManager

print("Available Regions:", REGIONS)
assert REGION_INDONESIA in REGIONS, "Indonesia missing from REGIONS"

# Test factory returns the correct class for Indonesia
id_client = VehicleManager(region=10, brand=1, username="test", password="pw", pin="1234").api
print("Region 10 API class:", type(id_client).__name__)
assert type(id_client).__name__ == "HyundaiBlueLinkApiID"

print("Multi-region registration verified successfully!")