"""Sea conditions near key Black Sea ports from Open-Meteo (free, no key, CC BY 4.0).

Writes data/weather.json. On any failure the previous values for that port are kept.
"""
from __future__ import annotations

import urllib.parse

from common import CONFIG, DATA, http_json, iso, load_json, now_utc, save_json

OUT = DATA / "weather.json"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
FORECAST = "https://api.open-meteo.com/v1/forecast"


def fetch(url: str, params: dict) -> dict:
    return http_json(url + "?" + urllib.parse.urlencode(params), timeout=30)


def max_next_24h(hourly: dict, key: str) -> float | None:
    vals = [v for v in (hourly or {}).get(key, [])[:24] if v is not None]
    return round(max(vals), 1) if vals else None


def port_conditions(lat: float, lon: float) -> dict:
    out = {}
    m = fetch(MARINE, {"latitude": lat, "longitude": lon, "timezone": "UTC", "forecast_days": 2,
                       "current": "wave_height,wave_direction,wave_period,sea_surface_temperature",
                       "hourly": "wave_height"})
    cur = m.get("current", {})
    out.update(wave_height=cur.get("wave_height"), wave_direction=cur.get("wave_direction"),
               wave_period=cur.get("wave_period"), sea_temp=cur.get("sea_surface_temperature"),
               wave_max_24h=max_next_24h(m.get("hourly"), "wave_height"))
    w = fetch(FORECAST, {"latitude": lat, "longitude": lon, "timezone": "UTC", "forecast_days": 2,
                         "wind_speed_unit": "kn",
                         "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m",
                         "hourly": "wind_gusts_10m"})
    cur = w.get("current", {})
    out.update(wind_speed=cur.get("wind_speed_10m"), wind_direction=cur.get("wind_direction_10m"),
               wind_gusts=cur.get("wind_gusts_10m"), air_temp=cur.get("temperature_2m"),
               gusts_max_24h=max_next_24h(w.get("hourly"), "wind_gusts_10m"))
    return out


def main():
    ports = load_json(CONFIG / "ports.json")["ports"]
    old = (load_json(OUT, {}) or {}).get("ports", {})
    result, ok = {}, 0
    for p in ports:
        try:
            result[p["id"]] = dict(port_conditions(p["sea_lat"], p["sea_lon"]), updated=iso(now_utc()))
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"[weather] {p['id']}: {e}")
            if p["id"] in old:
                result[p["id"]] = old[p["id"]]
    save_json(OUT, {"updated": iso(now_utc()) if ok else (load_json(OUT, {}) or {}).get("updated"),
                    "source": "Open-Meteo.com", "ports": result})
    print(f"[weather] {ok}/{len(ports)} ports updated")


if __name__ == "__main__":
    main()
