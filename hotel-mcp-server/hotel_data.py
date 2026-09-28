"""Single source of truth for hotel data. Imported by tools.py.

Keeping all hardcoded room names, prices, menu items, and recommendations here
means the 10 scripted demo questions can never disagree across tool calls.
"""

HOTEL_NAME = "The Grand Meridian"

ROOMS = {
    "standard": {
        "name": "Standard Room",
        "price_per_night_usd": 280,
        "size_sqft": 380,
        "description": "King bed, marble bath, harbor or courtyard view.",
    },
    "deluxe": {
        "name": "Deluxe Suite",
        "price_per_night_usd": 340,
        "size_sqft": 620,
        "description": "Separate sitting area, soaking tub, full harbor view.",
    },
    "honeymoon": {
        "name": "Honeymoon Suite",
        "price_per_night_usd": 420,
        "size_sqft": 940,
        "description": "Private terrace, fireplace, butler service on request.",
    },
    "junior": {
        "name": "Junior Suite",
        "price_per_night_usd": 380,
        "size_sqft": 540,
        "description": "King bed, lounge nook, partial harbor view.",
    },
    "presidential": {
        "name": "Presidential Suite",
        "price_per_night_usd": 1200,
        "size_sqft": 2100,
        "description": "Two bedrooms, private dining room, wraparound terrace, dedicated butler.",
    },
}

MENU = [
    {
        "name": "Pan-seared sea bass",
        "price_usd": 48,
        "category": "main",
        "vegetarian": False,
        "description": "Local sea bass, saffron risotto, lemon beurre blanc.",
    },
    {
        "name": "Wild mushroom risotto",
        "price_usd": 36,
        "category": "main",
        "vegetarian": True,
        "description": "Arborio rice, seasonal mushrooms, aged parmesan, truffle oil.",
    },
    {
        "name": "Heritage tomato salad",
        "price_usd": 22,
        "category": "starter",
        "vegetarian": True,
        "description": "Heirloom tomatoes, burrata, basil, aged balsamic.",
    },
    {
        "name": "Dry-aged ribeye",
        "price_usd": 62,
        "category": "main",
        "vegetarian": False,
        "description": "10oz, peppercorn jus, hand-cut fries.",
    },
    {
        "name": "Garden vegetable curry",
        "price_usd": 32,
        "category": "main",
        "vegetarian": True,
        "description": "Seasonal vegetables, coconut, basmati rice, naan.",
    },
    {
        "name": "Chocolate soufflé",
        "price_usd": 18,
        "category": "dessert",
        "vegetarian": True,
        "description": "Valrhona chocolate, vanilla bean ice cream. Allow 20 minutes.",
    },
]

RECOMMENDATIONS = {
    "restaurants": [
        {
            "name": "L'Ardoise",
            "kind": "French bistro",
            "walk_minutes": 4,
            "note": "Chef's tasting menu is the move. Reserve a week ahead.",
        },
        {
            "name": "Ootoya",
            "kind": "Japanese kappo",
            "walk_minutes": 7,
            "note": "Counter seats only. Eight-course omakase.",
        },
        {
            "name": "Harbor House",
            "kind": "Seafood",
            "walk_minutes": 9,
            "note": "Raw bar and sunset views over the water.",
        },
    ],
    "family": [
        {
            "name": "Harbor Aquarium",
            "kind": "Family attraction",
            "walk_minutes": 12,
            "note": "Touch tanks and a tunnel through the shark reef.",
        },
        {
            "name": "Riverfront Carousel",
            "kind": "Outdoor",
            "walk_minutes": 6,
            "note": "Hand-carved horses, runs until 9pm in summer.",
        },
        {
            "name": "Children's Discovery Museum",
            "kind": "Indoor museum",
            "walk_minutes": 14,
            "note": "Three floors of hands-on exhibits, good for ages 3-10.",
        },
    ],
    "nightlife": [
        {
            "name": "The Vault",
            "kind": "Cocktail bar",
            "walk_minutes": 5,
            "note": "Classic cocktails, dress code after 8pm.",
        },
        {
            "name": "Birdland",
            "kind": "Live jazz",
            "walk_minutes": 11,
            "note": "Two sets nightly, reservations recommended.",
        },
    ],
    "outdoors": [
        {
            "name": "Harborfront Promenade",
            "kind": "Walking path",
            "walk_minutes": 1,
            "note": "Two miles of waterfront, stops for coffee and ice cream.",
        },
        {
            "name": "Wellington Park",
            "kind": "Park",
            "walk_minutes": 8,
            "note": "Botanical garden and a small lake with rentable boats.",
        },
    ],
}

POOL_HOURS = "7am-10pm daily"
LATE_CHECKOUT_POLICY = "Subject to availability, please confirm at check-in."
RESERVATION_HANDOFF = "I'll connect you with our concierge team to arrange this."

VIP_GUESTS = {
    "VIP-042": {
        "name": "Dr. Mei Tanaka",
        "tier": "Diamond",
        "previous_stays": 7,
        "preferences": {
            "room_floor": "high",
            "dietary": "vegetarian, no shellfish",
            "amenity_priority": ["quiet room", "yoga mat in room"],
            "dining_history": ["sushi counter twice", "rooftop bar once"],
        },
        "notes": "Speaks at design conferences; in town for IxDA Tokyo.",
    },
    "VIP-101": {
        "name": "Marcus Chen",
        "tier": "Platinum",
        "previous_stays": 14,
        "preferences": {
            "room_floor": "any",
            "dietary": "gluten-free",
            "amenity_priority": ["fast Wi-Fi", "espresso machine", "blackout curtains"],
            "dining_history": ["in-room breakfast every visit", "ribeye twice"],
        },
        "notes": "Series-B founder; usually in town for board meetings, rarely leaves the hotel before noon.",
    },
    "VIP-203": {
        "name": "Sofia Reyes",
        "tier": "Diamond",
        "previous_stays": 4,
        "preferences": {
            "room_floor": "ground or low",
            "dietary": "pescatarian",
            "amenity_priority": ["dog amenities", "patio access", "fresh flowers"],
            "dining_history": ["sea bass three times", "rooftop bar twice"],
        },
        "notes": "Filmmaker; travels with a small terrier named Goose. Prefers ground-floor for easy outdoor access.",
    },
}
