"""
GroupIQ Customer Intelligence — Identifies premium customers, checks loyalty status,
analyzes booking history, and adjusts pricing with concessions for high-value guests.

Marriott Bonvoy Loyalty Tiers:
    Member        → No discount
    Silver Elite  → 2% rate concession
    Gold Elite    → 5% rate concession
    Platinum Elite → 8% rate concession
    Titanium Elite → 10% rate concession
    Ambassador Elite → 12% rate concession + dedicated support

Additional concessions for:
    - Repeat customers (booked 2+ times before)
    - High-value customers (lifetime revenue > thresholds)
    - Corporate accounts with contracted rates
"""
import hashlib
import os
from datetime import date, datetime


# ─── Marriott Bonvoy Loyalty Tiers ────────────────────────────────────────────

LOYALTY_TIERS = {
    "MEMBER": {
        "label": "Bonvoy Member",
        "discount_pct": 0.0,
        "nights_required": 0,
        "perks": [],
    },
    "SILVER": {
        "label": "Silver Elite",
        "discount_pct": 2.0,
        "nights_required": 10,
        "perks": ["Priority late checkout", "Bonus points (10%)"],
    },
    "GOLD": {
        "label": "Gold Elite",
        "discount_pct": 5.0,
        "nights_required": 25,
        "perks": ["Room upgrade", "Welcome gift", "Late checkout 2pm", "Bonus points (25%)"],
    },
    "PLATINUM": {
        "label": "Platinum Elite",
        "discount_pct": 8.0,
        "nights_required": 50,
        "perks": ["Suite upgrade", "Lounge access", "Choice amenity", "Bonus points (50%)", "48hr guarantee"],
    },
    "TITANIUM": {
        "label": "Titanium Elite",
        "discount_pct": 10.0,
        "nights_required": 75,
        "perks": ["Suite upgrade", "Lounge access", "United Silver status", "Bonus points (75%)", "Dedicated line"],
    },
    "AMBASSADOR": {
        "label": "Ambassador Elite",
        "discount_pct": 12.0,
        "nights_required": 100,
        "perks": ["Personal Ambassador", "Your24 flexible check-in", "Suite guarantee", "Bonus points (75%)"],
    },
}

# ─── Simulated Customer Database ──────────────────────────────────────────────
# In production, this queries DynamoDB/CRM. For demo, deterministic simulation.

KNOWN_CUSTOMERS = {
    "sarah@techcorp.com": {
        "name": "Sarah Johnson",
        "tier": "PLATINUM",
        "lifetime_nights": 62,
        "lifetime_revenue": 485000,
        "bookings_count": 8,
        "last_booking_date": "2026-03-15",
        "company": "TechCorp Inc.",
        "preferred_properties": ["MRIOTT-NYC-001", "MRIOTT-BLR-001"],
        "is_corporate_account": True,
        "notes": "Frequent conference organizer, prefers upper floors",
    },
    "sneha@test.com": {
        "name": "Sneha Planner",
        "tier": "GOLD",
        "lifetime_nights": 35,
        "lifetime_revenue": 220000,
        "bookings_count": 5,
        "last_booking_date": "2026-04-20",
        "company": "Event Masters",
        "preferred_properties": ["MRIOTT-HYD-001", "MRIOTT-BOM-001"],
        "is_corporate_account": False,
        "notes": "Wedding planner, books large blocks",
    },
    "raj@enterprise.com": {
        "name": "Raj Patel",
        "tier": "TITANIUM",
        "lifetime_nights": 88,
        "lifetime_revenue": 720000,
        "bookings_count": 12,
        "last_booking_date": "2026-05-01",
        "company": "Global Enterprise Solutions",
        "preferred_properties": ["MRIOTT-DEL-001", "MRIOTT-BOM-001", "MRIOTT-NYC-001"],
        "is_corporate_account": True,
        "notes": "CXO-level, hosts quarterly offsites, price-sensitive on large bookings",
    },
    "priya@startup.io": {
        "name": "Priya Sharma",
        "tier": "SILVER",
        "lifetime_nights": 15,
        "lifetime_revenue": 95000,
        "bookings_count": 3,
        "last_booking_date": "2026-02-10",
        "company": "StartupIO",
        "preferred_properties": ["MRIOTT-BLR-001"],
        "is_corporate_account": False,
        "notes": "Growing account, tech startup team events",
    },
    "krishna@corp.com": {
        "name": "Krishna Kumar",
        "tier": "AMBASSADOR",
        "lifetime_nights": 145,
        "lifetime_revenue": 1250000,
        "bookings_count": 22,
        "last_booking_date": "2026-05-18",
        "company": "Global Corp India",
        "preferred_properties": ["MRIOTT-HYD-001", "MRIOTT-BLR-001", "MRIOTT-MAA-001"],
        "is_corporate_account": True,
        "notes": "Top-tier customer, hosts large events, expects personalized service",
    },
    "test@test.com": {
        "name": "Test User",
        "tier": "GOLD",
        "lifetime_nights": 28,
        "lifetime_revenue": 180000,
        "bookings_count": 4,
        "last_booking_date": "2026-04-01",
        "company": "Test Corp",
        "preferred_properties": ["MRIOTT-NYC-001"],
        "is_corporate_account": False,
        "notes": "Regular customer",
    },
}


def _email_hash(email: str) -> float:
    """Deterministic hash for unknown customers to simulate tier."""
    h = hashlib.sha256(email.lower().encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def get_customer_profile(email: str) -> dict:
    """
    Look up customer profile by email.
    Returns tier, history, and loyalty status.
    For unknown customers, generates a consistent simulated profile.
    """
    email_lower = email.lower().strip()

    if email_lower in KNOWN_CUSTOMERS:
        profile = KNOWN_CUSTOMERS[email_lower].copy()
        profile["source"] = "CRM"
        return profile

    # Simulate a profile for unknown customers (deterministic from email)
    hash_val = _email_hash(email_lower)

    if hash_val > 0.92:
        tier = "PLATINUM"
        nights = int(50 + hash_val * 30)
        revenue = int(300000 + hash_val * 200000)
        bookings = int(6 + hash_val * 8)
    elif hash_val > 0.75:
        tier = "GOLD"
        nights = int(25 + hash_val * 20)
        revenue = int(150000 + hash_val * 100000)
        bookings = int(4 + hash_val * 5)
    elif hash_val > 0.50:
        tier = "SILVER"
        nights = int(10 + hash_val * 15)
        revenue = int(60000 + hash_val * 80000)
        bookings = int(2 + hash_val * 3)
    else:
        tier = "MEMBER"
        nights = int(hash_val * 8)
        revenue = int(hash_val * 40000)
        bookings = int(hash_val * 2)

    return {
        "name": email_lower.split("@")[0].title(),
        "tier": tier,
        "lifetime_nights": nights,
        "lifetime_revenue": revenue,
        "bookings_count": bookings,
        "last_booking_date": None,
        "company": "",
        "preferred_properties": [],
        "is_corporate_account": False,
        "notes": "",
        "source": "simulated",
    }


def get_loyalty_multiplier(email: str) -> tuple[float, str, dict]:
    """
    Calculate loyalty-based rate concession.
    Returns: (multiplier, description, customer_details)
    
    Premium customers get better rates as reward for loyalty.
    """
    profile = get_customer_profile(email)
    tier = profile["tier"]
    tier_info = LOYALTY_TIERS.get(tier, LOYALTY_TIERS["MEMBER"])

    discount_pct = tier_info["discount_pct"]
    multiplier = 1.0 - (discount_pct / 100.0)

    # Additional concessions for repeat high-value customers
    repeat_bonus = 0.0
    if profile["bookings_count"] >= 10:
        repeat_bonus = 3.0
    elif profile["bookings_count"] >= 5:
        repeat_bonus = 2.0
    elif profile["bookings_count"] >= 3:
        repeat_bonus = 1.0

    # Lifetime revenue bonus
    revenue_bonus = 0.0
    if profile["lifetime_revenue"] >= 1000000:
        revenue_bonus = 3.0
    elif profile["lifetime_revenue"] >= 500000:
        revenue_bonus = 2.0
    elif profile["lifetime_revenue"] >= 200000:
        revenue_bonus = 1.0

    # Corporate account bonus
    corporate_bonus = 2.0 if profile["is_corporate_account"] else 0.0

    total_bonus = repeat_bonus + revenue_bonus + corporate_bonus
    total_discount = discount_pct + total_bonus
    multiplier = 1.0 - (total_discount / 100.0)

    # Build description
    parts = [f"{tier_info['label']} (-{discount_pct}%)"]
    if repeat_bonus > 0:
        parts.append(f"Repeat guest (-{repeat_bonus}%)")
    if revenue_bonus > 0:
        parts.append(f"High-value (-{revenue_bonus}%)")
    if corporate_bonus > 0:
        parts.append(f"Corporate (-{corporate_bonus}%)")

    label = " | ".join(parts)

    customer_details = {
        "tier": tier,
        "tier_label": tier_info["label"],
        "loyalty_discount_pct": discount_pct,
        "repeat_bonus_pct": repeat_bonus,
        "revenue_bonus_pct": revenue_bonus,
        "corporate_bonus_pct": corporate_bonus,
        "total_concession_pct": total_discount,
        "bookings_count": profile["bookings_count"],
        "lifetime_revenue": profile["lifetime_revenue"],
        "lifetime_nights": profile["lifetime_nights"],
        "is_corporate_account": profile["is_corporate_account"],
        "perks": tier_info["perks"],
        "source": profile.get("source", "unknown"),
    }

    return multiplier, label, customer_details
