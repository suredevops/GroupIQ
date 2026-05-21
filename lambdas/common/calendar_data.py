"""
GroupIQ Calendar Data — Seasonality multipliers, holidays, and major events.
Used by the pricing engine to adjust rates based on temporal demand patterns.
"""
from datetime import date


# Monthly seasonality multipliers (1.0 = baseline)
MONTHLY_SEASONALITY = {
    1: 0.85,   # January — post-holiday low
    2: 0.90,   # February — slow season
    3: 0.95,   # March — spring pickup
    4: 1.05,   # April — spring events season
    5: 1.10,   # May — conferences & weddings start
    6: 1.15,   # June — peak wedding season
    7: 1.20,   # July — summer peak
    8: 1.15,   # August — still strong
    9: 1.12,   # September — corporate events peak
    10: 1.10,  # October — fall conferences
    11: 1.05,  # November — pre-holiday
    12: 1.30,  # December — holiday parties & year-end events
}

# Day-of-week multipliers (0=Monday, 6=Sunday)
DAY_OF_WEEK_MULTIPLIER = {
    0: 1.05,   # Monday — corporate check-in
    1: 1.05,   # Tuesday — business peak
    2: 1.05,   # Wednesday — business peak
    3: 1.08,   # Thursday — events start
    4: 1.12,   # Friday — weekend events begin
    5: 1.15,   # Saturday — weddings & social events
    6: 0.95,   # Sunday — lowest demand
}

# US federal holidays and major event dates (month, day)
US_HOLIDAYS = {
    (1, 1): ("New Year's Day", 1.35),
    (1, 20): ("MLK Day", 1.10),
    (2, 14): ("Valentine's Day", 1.25),
    (2, 17): ("Presidents' Day", 1.10),
    (3, 17): ("St. Patrick's Day", 1.15),
    (5, 26): ("Memorial Day", 1.20),
    (6, 19): ("Juneteenth", 1.10),
    (7, 4): ("Independence Day", 1.40),
    (9, 1): ("Labor Day", 1.20),
    (10, 13): ("Columbus Day", 1.10),
    (10, 31): ("Halloween", 1.15),
    (11, 11): ("Veterans Day", 1.10),
    (11, 27): ("Thanksgiving", 1.30),
    (11, 28): ("Black Friday", 1.25),
    (12, 24): ("Christmas Eve", 1.40),
    (12, 25): ("Christmas Day", 1.45),
    (12, 31): ("New Year's Eve", 1.50),
}

# Major events / convention dates that drive hotel demand
MAJOR_EVENTS = {
    (1, 7): ("CES Las Vegas", 1.20),
    (2, 9): ("Super Bowl Weekend", 1.30),
    (3, 8): ("SXSW Austin", 1.25),
    (5, 15): ("Google I/O", 1.15),
    (6, 9): ("WWDC Apple", 1.15),
    (9, 10): ("Apple Event", 1.15),
    (9, 24): ("UN General Assembly NYC", 1.30),
    (10, 1): ("Diwali Week", 1.20),
    (11, 15): ("AWS re:Invent", 1.25),
}

# Lead time pricing tiers (days before event → multiplier)
LEAD_TIME_TIERS = [
    (0, 7, 1.30),       # Last minute (0-7 days) — premium
    (7, 14, 1.20),      # Very short notice (1-2 weeks)
    (14, 30, 1.10),     # Short notice (2-4 weeks)
    (30, 60, 1.05),     # Normal (1-2 months)
    (60, 120, 1.00),    # Standard (2-4 months)
    (120, 180, 0.95),   # Early bird (4-6 months)
    (180, 365, 0.90),   # Far advance (6-12 months)
    (365, 9999, 0.85),  # Very early (1+ year)
]


def get_seasonality_multiplier(event_date: date) -> tuple[float, str]:
    """Get the monthly seasonality multiplier."""
    multiplier = MONTHLY_SEASONALITY.get(event_date.month, 1.0)
    month_name = event_date.strftime("%B")
    return multiplier, f"{month_name} seasonality"


def get_day_of_week_multiplier(event_date: date) -> tuple[float, str]:
    """Get the day-of-week demand multiplier."""
    day_num = event_date.weekday()
    multiplier = DAY_OF_WEEK_MULTIPLIER.get(day_num, 1.0)
    day_name = event_date.strftime("%A")
    return multiplier, f"{day_name} demand"


def get_holiday_multiplier(event_date: date) -> tuple[float, str]:
    """Check if the date falls on or near a holiday/event."""
    key = (event_date.month, event_date.day)

    if key in US_HOLIDAYS:
        name, mult = US_HOLIDAYS[key]
        return mult, f"Holiday: {name}"

    if key in MAJOR_EVENTS:
        name, mult = MAJOR_EVENTS[key]
        return mult, f"Event: {name}"

    # Check proximity to holidays (±2 days)
    for (m, d), (name, mult) in US_HOLIDAYS.items():
        try:
            holiday = date(event_date.year, m, d)
            diff = abs((event_date - holiday).days)
            if 0 < diff <= 2:
                proximity_mult = 1.0 + (mult - 1.0) * 0.5
                return proximity_mult, f"Near {name} (±{diff}d)"
        except ValueError:
            continue

    return 1.0, "No holiday impact"


def get_lead_time_multiplier(event_date: date, booking_date: date = None) -> tuple[float, str]:
    """Calculate lead time premium/discount based on how far in advance."""
    if booking_date is None:
        booking_date = date.today()

    days_ahead = (event_date - booking_date).days
    if days_ahead < 0:
        days_ahead = 0

    for min_days, max_days, multiplier in LEAD_TIME_TIERS:
        if min_days <= days_ahead < max_days:
            if days_ahead <= 7:
                label = f"Last minute ({days_ahead}d ahead)"
            elif days_ahead <= 30:
                label = f"Short notice ({days_ahead}d ahead)"
            elif days_ahead <= 120:
                label = f"Standard lead time ({days_ahead}d)"
            else:
                label = f"Early booking discount ({days_ahead}d ahead)"
            return multiplier, label

    return 1.0, f"Standard lead ({days_ahead}d)"
