"""
GroupIQ Market Data Simulator — Generates deterministic occupancy and competitor rates.
Uses seeded hashing so the same date always produces the same values (consistency across calls).

Includes:
- Marriott pricing strategies (BAR, BRG, Revenue Management)
- Competitor benchmark pricing (Hilton, Hyatt, ITC, Taj, Oberoi)
- Occupancy-based dynamic pricing
- Group size & length-of-stay discounts
"""
import hashlib
from datetime import date


# ─── Marriott Pricing Strategy (BAR = Best Available Rate) ─────────────────────
# Marriott uses a tiered rate strategy:
#   BAR (Best Available Rate) — standard dynamic rate
#   BRG (Best Rate Guarantee) — lowest public rate matched
#   Group Rate — negotiated for 10+ rooms
#   Corporate Rate — contracted annual rate for businesses
#   Government Rate — fixed rate for govt bookings

MARRIOTT_RATE_STRATEGIES = {
    "BAR": {"label": "Best Available Rate", "base_multiplier": 1.00},
    "PREMIUM": {"label": "Peak/Event Premium", "base_multiplier": 1.15},
    "VALUE": {"label": "Value Season Rate", "base_multiplier": 0.88},
    "CORPORATE": {"label": "Corporate Contract Rate", "base_multiplier": 0.85},
    "GOVERNMENT": {"label": "Government Rate", "base_multiplier": 0.75},
}

# Competitor hotel brands with their typical rate positioning vs Marriott
COMPETITOR_BRANDS = {
    "Hilton": {"rate_vs_marriott": 0.97, "brand_tier": "full-service"},
    "Hyatt": {"rate_vs_marriott": 1.03, "brand_tier": "full-service"},
    "ITC Hotels": {"rate_vs_marriott": 1.05, "brand_tier": "luxury-india"},
    "Taj Hotels": {"rate_vs_marriott": 1.10, "brand_tier": "luxury-india"},
    "Oberoi": {"rate_vs_marriott": 1.15, "brand_tier": "luxury-india"},
    "Radisson": {"rate_vs_marriott": 0.88, "brand_tier": "upper-midscale"},
    "Holiday Inn": {"rate_vs_marriott": 0.80, "brand_tier": "midscale"},
    "Lemon Tree": {"rate_vs_marriott": 0.65, "brand_tier": "midscale-india"},
}


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
    Incorporates Marriott's revenue management positioning against known competitors.
    Returns: (competitor_rate, adjustment_multiplier, description)
    """
    variance_factor = _date_hash(event_date, property_id, "competitor")

    # Determine which competitor set to benchmark against
    location = property_id.split("-")[1] if "-" in property_id else "NYC"
    india_locations = {"HYD", "BLR", "BOM", "DEL", "MAA", "GOA", "JAI", "PNQ", "CCU"}

    if location in india_locations:
        competitors = ["ITC Hotels", "Taj Hotels", "Oberoi", "Hyatt", "Radisson"]
    else:
        competitors = ["Hilton", "Hyatt", "Radisson", "Holiday Inn"]

    # Pick a primary competitor based on date hash
    competitor_idx = int(variance_factor * len(competitors)) % len(competitors)
    primary_competitor = competitors[competitor_idx]
    comp_positioning = COMPETITOR_BRANDS[primary_competitor]["rate_vs_marriott"]

    # Competitor variance: positioned relative to Marriott with ±8% daily fluctuation
    daily_variance = (variance_factor - 0.5) * 0.16
    competitor_rate = base_rate * comp_positioning * (1.0 + daily_variance)

    # Marriott's response strategy (never undercut by >5%, capture upside at 3%)
    if competitor_rate > base_rate * 1.10:
        multiplier = 1.05
        label = f"{primary_competitor} higher (${competitor_rate:.0f}) — Marriott premium opportunity"
    elif competitor_rate > base_rate * 1.03:
        multiplier = 1.02
        label = f"{primary_competitor} at ${competitor_rate:.0f} — slight premium"
    elif competitor_rate < base_rate * 0.90:
        multiplier = 0.95
        label = f"{primary_competitor} lower (${competitor_rate:.0f}) — BRG competitive match"
    elif competitor_rate < base_rate * 0.97:
        multiplier = 0.98
        label = f"{primary_competitor} at ${competitor_rate:.0f} — rate parity adjustment"
    else:
        multiplier = 1.00
        label = f"{primary_competitor} aligned (${competitor_rate:.0f}) — BAR maintained"

    return competitor_rate, multiplier, label


def get_marriott_strategy_multiplier(event_date: date, property_id: str, event_type: str = "") -> tuple[float, str]:
    """
    Apply Marriott-specific pricing strategy based on demand signals.
    Implements BAR (Best Available Rate) revenue management rules.
    """
    hash_val = _date_hash(event_date, property_id, "strategy")
    location = property_id.split("-")[1] if "-" in property_id else ""

    # Determine demand level from multiple signals
    demand_score = 0.0

    # Season-based demand (India wedding/cricket vs US convention season)
    india_locations = {"HYD", "BLR", "BOM", "DEL", "MAA", "GOA", "JAI", "PNQ", "CCU"}
    if location in india_locations:
        # India: Oct-Feb = wedding season, Mar-May = IPL, Jun-Sep = monsoon low
        if event_date.month in (11, 12, 1, 2):
            demand_score += 0.30
        elif event_date.month in (3, 4, 5):
            demand_score += 0.25
        elif event_date.month in (6, 7, 8):
            demand_score -= 0.15
    else:
        # US: Sep-Nov = conference, Jun-Aug = leisure, Jan = low
        if event_date.month in (9, 10, 11):
            demand_score += 0.25
        elif event_date.month in (6, 7, 8):
            demand_score += 0.15
        elif event_date.month == 1:
            demand_score -= 0.10

    # Weekend vs weekday corporate demand
    if event_date.weekday() >= 4:
        demand_score += 0.10

    # Deterministic random factor for market noise
    demand_score += (hash_val - 0.5) * 0.15

    # Marriott strategy decision
    if demand_score >= 0.35:
        strategy = "PREMIUM"
        multiplier = 1.08
        label = f"Marriott Premium Rate — Very high demand period"
    elif demand_score >= 0.15:
        strategy = "BAR"
        multiplier = 1.03
        label = f"Marriott BAR — Above-average demand"
    elif demand_score <= -0.10:
        strategy = "VALUE"
        multiplier = 0.95
        label = f"Marriott Value Rate — Low demand, stimulate bookings"
    else:
        strategy = "BAR"
        multiplier = 1.00
        label = f"Marriott BAR — Standard demand"

    return multiplier, label


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
