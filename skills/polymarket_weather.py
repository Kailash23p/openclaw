import requests
from datetime import datetime, timedelta
import re

CITIES = {
    "New York": {"search": ["nyc", "new york", "lga", "laguardia"], "lat": 40.7769, "lon": -73.8740, "unit": "fahrenheit"},  # LGA
    "Chicago": {"search": ["chicago", "ord", "o'hare"], "lat": 41.9742, "lon": -87.9073, "unit": "fahrenheit"},  # ORD
    "Seattle": {"search": ["seattle", "sea"], "lat": 47.4502, "lon": -122.3088, "unit": "fahrenheit"},  # SEA
    "Atlanta": {"search": ["atlanta", "atl"], "lat": 33.6407, "lon": -84.4277, "unit": "fahrenheit"},  # ATL
    "Miami": {"search": ["miami", "mia"], "lat": 25.7959, "lon": -80.2870, "unit": "fahrenheit"},  # MIA
    "London": {"search": ["london", "lcy", "london city"], "lat": 51.5048, "lon": 0.0495, "unit": "celsius"},  # LCY
    "Seoul": {"search": ["seoul", "icn", "incheon"], "lat": 37.4602, "lon": 126.4407, "unit": "celsius"},  # ICN
    "Wellington": {"search": ["wellington", "wlg"], "lat": -41.3272, "lon": 174.8053, "unit": "celsius"},  # WLG
}

API_URL = "https://gamma-api.polymarket.com/markets?active=true&closed=false"

def parse_market_date(question):
    question_low = question.lower()
    month_map = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    # Pattern: "February 8", "Feb 8th", etc.
    match = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s.,-]*(\d{1,2})(st|nd|rd|th)?', question_low)
    if match:
        month_str, day_str = match.group(1), match.group(2)
        month = month_map.get(month_str)
        day = int(day_str)
        if month:
            year = datetime.now().year
            try:
                date = datetime(year, month, day).date()
                if date < datetime.now().date() - timedelta(days=1):
                    date = date.replace(year=year + 1)
                return date
            except ValueError:
                pass
    # Pattern: 2026-02-07 or similar
    match = re.search(r'(\d{4})[/-/](\d{1,2})[/-/](\d{1,2})', question_low)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return datetime(year, month, day).date()
        except ValueError:
            pass
    return None

def get_forecasts(city_name):
    city_data = CITIES.get(city_name)
    if not city_data:
        return None
    unit = 'fahrenheit' if city_data["unit"] == "fahrenheit" else 'celsius'
    url = f"https://api.open-meteo.com/v1/forecast?latitude={city_data['lat']}&longitude={city_data['lon']}&daily=temperature_2m_max&temperature_unit={unit}&forecast_days=7&timezone=auto"
    try:
        data = requests.get(url).json()
        if 'daily' not in data:
            return None
        times = data['daily']['time']
        highs = data['daily']['temperature_2m_max']
        return dict(zip(times, highs))
    except Exception:
        return None

def parse_bracket(outcome_name):
    outcome_low = outcome_name.lower()
    nums = [int(n) for n in re.findall(r'\d+', outcome_name) if n.isdigit()]
    unit = 'fahrenheit' if '°f' in outcome_low or ' f' in outcome_low else 'celsius'
    if 'under' in outcome_low or 'below' in outcome_low or 'less than' in outcome_low:
        return None, nums[0] if nums else None, unit  # Under X°F
    if 'above' in outcome_low or 'over' in outcome_low or 'or higher' in outcome_low:
        return nums[0] if nums else None, None, unit  # X°F and above
    if len(nums) >= 2:
        return nums[0], nums[1], unit
    return None, None, unit

def monitor_weather_markets():
    markets = requests.get(API_URL).json()
    alerts = []
    current_date = datetime.utcnow().date()
    tomorrow_date = current_date + timedelta(days=1)

    for market in markets:
        question_low = market['question'].lower()
        if 'highest temperature' not in question_low and 'high temperature' not in question_low:
            continue
        
        city_match = next((c for c in CITIES if any(s in question_low for s in CITIES[c]["search"])), None)
        if not city_match:
            continue
        
        forecasts = get_forecasts(city_match)
        if not forecasts:
            continue
        
        target_date = parse_market_date(market['question'])
        if not target_date or target_date not in (current_date, tomorrow_date):
            continue
        
        forecast_date_str = target_date.strftime('%Y-%m-%d')
        if forecast_date_str not in forecasts:
            continue
        
        raw_forecast = forecasts[forecast_date_str]
        unit = CITIES[city_match]["unit"]
        conservative_forecast = raw_forecast - (1 if unit == "fahrenheit" else 0.5)
        display_temp = f"{conservative_forecast:.1f}°{'F' if unit=='fahrenheit' else 'C'} (conservative from {raw_forecast:.1f}°)"

        outcomes = market.get('outcomes', [])
        prices = [float(p) for p in market.get('outcomePrices', []) if p]

        tolerance = 2 if unit == "fahrenheit" else 1
        strict_margin = tolerance * 2

        for i, outcome in enumerate(outcomes):
            if i >= len(prices):
                continue
            price = prices[i]
            low, high, bracket_unit = parse_bracket(outcome)
            if bracket_unit != unit or low is None and high is None:
                continue

            bracket_upper = high if high is not None else float('inf')

            # Is bracket impossible AND low (quick resolution)?
            is_impossible_low = conservative_forecast > bracket_upper + strict_margin

            # Is forecast tightly in bracket (for rare Yes)?
            in_bracket = False
            if low is not None and high is not None:
                in_bracket = low - tolerance <= conservative_forecast <= high + tolerance
            elif low is None and high is not None:  # Under
                in_bracket = conservative_forecast <= high + tolerance
            elif high is None and low is not None:  # Above
                in_bracket = conservative_forecast >= low - tolerance

            link = f"https://polymarket.com/event/{market['slug']}"

            # Primary: Safe quick No on low impossible brackets
            if price >= 0.97 and is_impossible_low:
                alerts.append(f"🚨 SAFE NO (QUICK RESOLUTION LIKELY)!\nSell Yes at {price*100:.1f}¢ on impossible low bracket\n{market['question']}\nOutcome: {outcome}\nForecast: {display_temp}\nLink: {link}")

            # Rare: Ultra-safe Yes
            if price >= 0.99 and in_bracket:
                alerts.append(f"🚨 RARE SAFE YES!\nBuy at {price*100:.1f}¢ (ultra high certainty)\n{market['question']}\nOutcome: {outcome}\nForecast: {display_temp}\nLink: {link}")

    return "\n\n".join(alerts) if alerts else "No high-certainty quick/safe opportunities right now."

monitor_weather_markets.description = "Monitors only your airports for today/tomorrow high-temp bracket markets on Polymarket. Prioritizes quick-resolving safe No bets on impossible low brackets (97+¢ mispricing), rare Yes only at 99+¢."
