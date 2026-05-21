"""
GroupIQ Market Data Simulator — Generates deterministic occupancy and competitor rates.
Uses seeded hashing so the same date always produces the same values (consistency across calls).
"""
import hashlib
from datetime import date


def _date_hash(event_date: date, property_id: str, seed: str = "") -> float:
    """Generate a deterministic float [0, 1) from date + property + seed."""
    raw = f"{event_date.isoformat()}:{property_id}:{seed}"
    h = hashlib.sha256(raw.encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def get_occupancy_rate(event_date: date, property_id: str) -> tuple[float, float, str]:
    """
    Simulate occupancy rate for a property on a given date.
    Returns: (occupancy_pct 0-1, multiplier, description)
    
    Occupancy ranges from 60-95% depending on date factors.
    Higher occupancy = higher rate multiplier.
    """
    base_occupancy = 0.60
    date_factor = _date_hash(event_date, property_id, "occupancy")

    # Month-based occupancy bump (summer/December higher)
    month_bump = {
        1: 0.0, 2: 0.02, 3: 0.05, 4: 0.08, 5: 0.10,
        6: 0.15, 7: 0.18, 8: 0.15, 9: 0.12, 10: 0.10,
        11: 0.08, 12: 0.20,
    }.get(event_date.month, 0.05)

    # Weekend bump
    weekend_bump = 0.08 if event_date.weekday() >= 4 else 0.0

    # Random variance ±10%
    random_variance = (date_factor - 0.5) * 0.20

    occupancy = base_occupancy + month_bump + weekend_bump + random_variance
    occupancy = max(0.45, min(0.98, occupancy))

    # Occupancy → pricing multiplier
    if occupancy >= 0.90:
        multiplier = 1.15
        label = f"Very high occupancy ({occupancy*100:.0f}%)"
    elif occupancy >= 0.80:
        multiplier = 1.08
        label = f"High occupancy ({occupancy*100:.0f}%)"
    elif occupancy >= 0.70:
        multiplier = 1.03
        label = f"Moderate occupancy ({occupancy*100:.0f}%)"
    elif occupancy >= 0.60:
        multiplier = 1.00
        label = f"Normal occupancy ({occupancy*100:.0f}%)"
    else:
        multiplier = 0.92
        label = f"Low occupancy ({occupancy*100:.0f}%)"

    return occupancy, multiplier, label


def get_competitor_rate(base_rate: float, event_date: date, property_id: str) -> tuple[float, float, str]:
    """
    Simulate competitor hotel rates based on ±15% variance from base.
    Returns: (competitor_rate, adjustment_multiplier, description)
    
    If competitors are higher, we can price up slightly.
    If competitors are lower, we adjust down to stay competitive.
    """
    variance_factor = _date_hash(event_date, property_id, "competitor")

    # Competitor variance: -15% to +15% from our base
    variance_pct = (variance_factor - 0.5) * 0.30  # ±15%
    competitor_rate = base_rate * (1.0 + variance_pct)

    # Our adjustment based on competitor positioning
    if competitor_rate > base_rate * 1.10:
        # Competitors are 10%+ higher — we can push our rate up
        multiplier = 1.05
        label = f"Competitors higher (${competitor_rate:.0f}/night) — rate opportunity"
    elif competitor_rate > base_rate * 1.03:
        # Competitors slightly higher
        multiplier = 1.02
        label = f"Competitors at ${competitor_rate:.0f}/night — slight premium"
    elif competitor_rate < base_rate * 0.90:
        # Competitors are 10%+ lower — we need to adjust down
        multiplier = 0.95
        label = f"Competitors lower (${competitor_rate:.0f}/night) — competitive adjustment"
    elif competitor_rate < base_rate * 0.97:
        # Competitors slightly lower
        multiplier = 0.98
        label = f"Competitors at ${competitor_rate:.0f}/night — minor adjustment"
    else:
        multiplier = 1.00
        label = f"Competitors aligned (${competitor_rate:.0f}/night)"

    return competitor_rate, multiplier, label


def get_group_size_multiplier(num_rooms: int) -> tuple[float, str]:
    """
    Volume discount for larger group bookings.
    More rooms = better per-room rate (encourages large bookings).
    """
    if num_rooms >= 200:
        return 0.88, f"Large group discount ({num_rooms} rooms: -12%)"
    elif num_rooms >= 150:
        return 0.90, f"Large group discount ({num_rooms} rooms: -10%)"
    elif num_rooms >= 100:
        return 0.93, f"Volume discount ({num_rooms} rooms: -7%)"
    elif num_rooms >= 75:
        return 0.95, f"Volume discount ({num_rooms} rooms: -5%)"
    elif num_rooms >= 50:
        return 0.97, f"Group rate ({num_rooms} rooms: -3%)"
    elif num_rooms >= 25:
        return 0.98, f"Small group rate ({num_rooms} rooms: -2%)"
    else:
        return 1.00, f"Standard rate ({num_rooms} rooms)"


def get_length_of_stay_multiplier(num_nights: int) -> tuple[float, str]:
    """
    Discount for longer stays (encourages extended bookings).
    """
    if num_nights >= 7:
        return 0.90, f"Extended stay discount ({num_nights} nights: -10%)"
    elif num_nights >= 5:
        return 0.93, f"Long stay discount ({num_nights} nights: -7%)"
    elif num_nights >= 3:
        return 0.96, f"Multi-night rate ({num_nights} nights: -4%)"
    elif num_nights >= 2:
        return 0.98, f"2-night rate (-2%)"
    else:
        return 1.00, f"Single night rate"
