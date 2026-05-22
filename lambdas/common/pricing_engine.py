"""
GroupIQ Dynamic Pricing Engine — Calculates optimal room rates based on
multiple market factors. Replaces the fixed base_rate with intelligent pricing.

Usage:
    from pricing_engine import calculate_dynamic_rate
    result = calculate_dynamic_rate(
        base_rate=299.0,
        floor_rate=254.0,
        event_date="2026-09-15",
        property_id="MRIOTT-NYC-001",
        num_rooms=75,
        num_nights=3,
    )
    # result["final_rate"] → dynamic rate
    # result["breakdown"] → list of factors applied
"""
from datetime import date, datetime
from calendar_data import (
    get_seasonality_multiplier,
    get_day_of_week_multiplier,
    get_holiday_multiplier,
    get_lead_time_multiplier,
    get_local_demand_multiplier,
)
from market_data import (
    get_occupancy_rate,
    get_competitor_rate,
    get_group_size_multiplier,
    get_length_of_stay_multiplier,
    get_marriott_strategy_multiplier,
)
from customer_intelligence import get_loyalty_multiplier


def calculate_dynamic_rate(
    base_rate: float,
    floor_rate: float,
    event_date: str,
    property_id: str,
    num_rooms: int = 10,
    num_nights: int = 1,
    peak_rate: float = None,
    booking_date: str = None,
    customer_email: str = None,
    event_type: str = "",
) -> dict:
    """
    Calculate the dynamic room rate using all pricing factors.
    
    Args:
        base_rate: Standard room rate from pricing rules
        floor_rate: Minimum allowed rate (never go below)
        event_date: Date of the event (YYYY-MM-DD)
        property_id: Property identifier
        num_rooms: Number of rooms requested
        num_nights: Number of nights
        peak_rate: Maximum ceiling rate (optional)
        booking_date: Date of booking (defaults to today)
        customer_email: Customer email for loyalty lookup
        event_type: Type of event (conference, wedding, etc.)
    
    Returns:
        dict with final_rate, breakdown, savings info, customer intelligence
    """
    # Parse dates
    if isinstance(event_date, str):
        evt_date = datetime.strptime(event_date[:10], "%Y-%m-%d").date()
    else:
        evt_date = event_date

    if booking_date:
        book_date = datetime.strptime(booking_date[:10], "%Y-%m-%d").date()
    else:
        book_date = date.today()

    if peak_rate is None:
        peak_rate = base_rate * 1.50

    # Collect all pricing factors
    factors = []
    combined_multiplier = 1.0

    # 1. Seasonality
    mult, desc = get_seasonality_multiplier(evt_date)
    factors.append({"factor": "Seasonality", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 2. Day of week
    mult, desc = get_day_of_week_multiplier(evt_date)
    factors.append({"factor": "Day of Week", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 3. Holiday / event proximity
    mult, desc = get_holiday_multiplier(evt_date)
    factors.append({"factor": "Holiday/Event", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 4. Lead time
    mult, desc = get_lead_time_multiplier(evt_date, book_date)
    factors.append({"factor": "Lead Time", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 5. Occupancy-based
    occ_pct, mult, desc = get_occupancy_rate(evt_date, property_id)
    factors.append({"factor": "Occupancy", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 6. Competitor benchmark
    comp_rate, mult, desc = get_competitor_rate(base_rate, evt_date, property_id)
    factors.append({"factor": "Competitor Rates", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 7. Group size discount
    mult, desc = get_group_size_multiplier(num_rooms)
    factors.append({"factor": "Group Size", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 8. Length of stay discount
    mult, desc = get_length_of_stay_multiplier(num_nights)
    factors.append({"factor": "Length of Stay", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 9. Local demand events (IPL cricket, city festivals, conferences)
    mult, desc = get_local_demand_multiplier(evt_date, property_id)
    factors.append({"factor": "Local Demand", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 10. Marriott pricing strategy (BAR/Premium/Value based on demand signals)
    mult, desc = get_marriott_strategy_multiplier(evt_date, property_id, event_type)
    factors.append({"factor": "Marriott Strategy", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
    combined_multiplier *= mult

    # 11. Customer loyalty & premium concession
    customer_details = None
    if customer_email:
        mult, desc, customer_details = get_loyalty_multiplier(customer_email)
        factors.append({"factor": "Loyalty Concession", "multiplier": mult, "description": desc, "impact": f"{(mult-1)*100:+.1f}%"})
        combined_multiplier *= mult

    # Calculate final rate
    raw_rate = base_rate * combined_multiplier
    final_rate = max(floor_rate, min(peak_rate, raw_rate))

    # Rate was clamped?
    clamped = None
    if raw_rate < floor_rate:
        clamped = "floor"
        factors.append({"factor": "Floor Protection", "multiplier": None, "description": f"Rate raised to floor: ${floor_rate:.0f}", "impact": "floor"})
    elif raw_rate > peak_rate:
        clamped = "ceiling"
        factors.append({"factor": "Rate Cap", "multiplier": None, "description": f"Rate capped at: ${peak_rate:.0f}", "impact": "cap"})

    # Calculate total revenue and savings vs. rack rate
    total_revenue = final_rate * num_rooms * num_nights
    rack_rate_total = peak_rate * num_rooms * num_nights
    savings_vs_rack = rack_rate_total - total_revenue
    savings_pct = (savings_vs_rack / rack_rate_total * 100) if rack_rate_total > 0 else 0

    return {
        "final_rate": round(final_rate, 2),
        "base_rate": base_rate,
        "floor_rate": floor_rate,
        "peak_rate": peak_rate,
        "combined_multiplier": round(combined_multiplier, 4),
        "raw_calculated_rate": round(raw_rate, 2),
        "clamped": clamped,
        "breakdown": factors,
        "market_data": {
            "occupancy_pct": round(occ_pct * 100, 1),
            "competitor_rate": round(comp_rate, 2),
        },
        "revenue_summary": {
            "rate_per_night": round(final_rate, 2),
            "num_rooms": num_rooms,
            "num_nights": num_nights,
            "total_revenue": round(total_revenue, 2),
            "savings_vs_rack_rate": round(savings_vs_rack, 2),
            "savings_pct": round(savings_pct, 1),
        },
        "customer_intelligence": customer_details,
        "pricing_explanation": _generate_explanation(base_rate, final_rate, factors, combined_multiplier),
    }


def _generate_explanation(base_rate, final_rate, factors, multiplier):
    """Generate a human-readable pricing explanation."""
    active_factors = [f for f in factors if f["multiplier"] is not None and f["multiplier"] != 1.0]
    increases = [f for f in active_factors if f["multiplier"] > 1.0]
    decreases = [f for f in active_factors if f["multiplier"] < 1.0]

    lines = [f"Base Rate: ${base_rate:.0f}/night"]

    if increases:
        lines.append("Rate increases:")
        for f in increases:
            lines.append(f"  + {f['factor']}: {f['impact']} ({f['description']})")

    if decreases:
        lines.append("Discounts applied:")
        for f in decreases:
            lines.append(f"  - {f['factor']}: {f['impact']} ({f['description']})")

    lines.append(f"Combined multiplier: ×{multiplier:.4f}")
    lines.append(f"Final Rate: ${final_rate:.0f}/night")

    return "\n".join(lines)
