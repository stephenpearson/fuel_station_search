#!/usr/bin/env python3

# Copyright 2026 Stephen Pearson <stephen.pearson@live.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from urllib import error, parse, request

EARTH_RADIUS_MILES = 3958.8
TOKEN_URL = "https://www.fuel-finder.service.gov.uk/api/v1/oauth/generate_access_token"
PFS_URL = "https://www.fuel-finder.service.gov.uk/api/v1/pfs?batch-number={}"
PRICES_URL = "https://www.fuel-finder.service.gov.uk/api/v1/pfs/fuel-prices?batch-number={}"
CACHE_FILE = Path("fuel_api_cache.json")
DEFAULT_OAUTH_FILE = Path.home() / ".fuel_station_oath.yml"
CACHE_TTL_SECONDS = 2 * 60 * 60  # 2 hours


def supports_fancy_progress() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "") not in {"", "dumb"}


def render_progress_bar(current: int, width: int = 30) -> str:
    if current <= 0:
        return "[" + (" " * width) + "]"
    position = (current - 1) % width
    bar = ["-"] * width
    bar[position] = "#"
    return "[" + "".join(bar) + "]"


def parse_coordinate(value: str) -> float:
    cleaned = value.strip().strip(",")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid coordinate '{value}'. Expected a decimal number."
        ) from exc


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_MILES * c


def parse_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def sort_value(station: dict, sort_by: str):
    if sort_by == "distance":
        return station["distance"]
    value = parse_float(station.get(sort_by))
    return value if value is not None else float("inf")


def parse_simple_yaml(path: Path) -> dict:
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line == "---":
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def http_json(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    body: bytes | None = None,
    timeout: int = 90,
    retries: int = 3,
):
    req = request.Request(url=url, method=method, headers=headers or {}, data=body)
    for attempt in range(retries):
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read().decode("utf-8")
                if not payload:
                    return None
                return json.loads(payload)
        except (error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise e


def get_access_token(client_id: str, client_secret: str) -> str:
    payload = parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    data = http_json(
        TOKEN_URL,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        body=payload,
    )
    if isinstance(data, dict):
        token = data.get("access_token") or data.get("token")
        if not token and isinstance(data.get("data"), dict):
            token = data["data"].get("access_token") or data["data"].get("token")
        if token:
            return token
    raise RuntimeError("Could not retrieve OAuth access token from token endpoint response")


def fetch_batches(url_template: str, token: str, dataset_name: str = "data") -> list[dict]:
    results: list[dict] = []
    batch = 1
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    fancy = supports_fancy_progress()

    while True:
        url = url_template.format(batch)
        try:
            data = http_json(url, headers=headers)
        except error.HTTPError as e:
            if e.code == 404:
                break
            raise

        if not data:
            break
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected API response for batch {batch}: expected list")

        results.extend(data)
        if fancy:
            bar = render_progress_bar(batch)
            print(
                f"\rDownloading {dataset_name}: {bar} batches={batch} records={len(results)}",
                file=sys.stderr,
                end="",
                flush=True,
            )
        else:
            print(
                f"Downloaded {dataset_name} batch {batch} ({len(data)} records)",
                file=sys.stderr,
                flush=True,
            )
        batch += 1

    if fancy:
        final_message = (
            f"\rFinished downloading {dataset_name}: "
            f"{batch - 1} batches, {len(results)} total records"
        )
        print(
            final_message,
            file=sys.stderr,
            flush=True,
        )
    else:
        print(
            f"Finished downloading {dataset_name}: {len(results)} total records",
            file=sys.stderr,
            flush=True,
        )
    return results


def load_cache(cache_path: Path):
    if not cache_path.exists():
        return None
    try:
        obj = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    ts = obj.get("timestamp")
    if not isinstance(ts, (int, float)):
        return None
    if time.time() - ts > CACHE_TTL_SECONDS:
        return None
    return obj


def save_cache(cache_path: Path, pfs_data: list[dict], prices_data: list[dict]):
    obj = {
        "timestamp": time.time(),
        "pfs": pfs_data,
        "prices": prices_data,
    }
    cache_path.write_text(json.dumps(obj), encoding="utf-8")


def fuel_map(fuel_prices: list[dict]) -> dict:
    out = {}
    for item in fuel_prices or []:
        fuel_type = item.get("fuel_type")
        price = item.get("price")
        if fuel_type:
            out[fuel_type] = price
    return out


def pick_b7(prices: dict):
    return prices.get("B7_STANDARD") or prices.get("B7_PREMIUM") or prices.get("B7")


def build_joined_data(pfs_rows: list[dict], price_rows: list[dict]):
    price_by_id = {row.get("node_id"): row for row in price_rows if row.get("node_id")}
    joined = []
    for p in pfs_rows:
        node_id = p.get("node_id")
        if not node_id:
            continue
        loc = p.get("location") or {}
        lat = parse_float(loc.get("latitude"))
        lon = parse_float(loc.get("longitude"))
        if lat is None or lon is None:
            continue

        fuel_prices = fuel_map((price_by_id.get(node_id) or {}).get("fuel_prices") or [])

        joined.append(
            {
                "node_id": node_id,
                "trading_name": p.get("trading_name", ""),
                "brand_name": p.get("brand_name", ""),
                "postcode": loc.get("postcode", ""),
                "latitude": lat,
                "longitude": lon,
                "e5": fuel_prices.get("E5"),
                "e10": fuel_prices.get("E10"),
                "b7": pick_b7(fuel_prices),
            }
        )
    return joined


def format_price(value: object) -> str:
    return "" if value is None else str(value)


def render_text_table(matches: list[dict]) -> str:
    headers = ["distance_miles", "trading_name", "brand_name", "postcode", "E5", "E10", "B7"]
    rows = [
        [
            f"{station['distance']:.2f}",
            str(station["trading_name"]),
            str(station["brand_name"]),
            str(station["postcode"]),
            format_price(station["e5"]),
            format_price(station["e10"]),
            format_price(station["b7"]),
        ]
        for station in matches
    ]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    header_row = " | ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
    separator = "-+-".join("-" * width for width in widths)
    body = [" | ".join(value.ljust(widths[i]) for i, value in enumerate(row)) for row in rows]
    return "\n".join([header_row, separator, *body])


def render_csv(matches: list[dict]) -> str:
    lines = ["distance_miles,trading_name,brand_name,postcode,E5,E10,B7"]
    for station in matches:
        lines.append(
            f"{station['distance']:.2f},"
            f"{station['trading_name']},"
            f"{station['brand_name']},"
            f"{station['postcode']},"
            f"{format_price(station['e5'])},"
            f"{format_price(station['e10'])},"
            f"{format_price(station['b7'])}"
        )
    return "\n".join(lines)


def yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def render_yaml(matches: list[dict]) -> str:
    lines: list[str] = []
    for station in matches:
        lines.append("-")
        lines.append(f"  distance: {station['distance']:.2f}")
        lines.append(f"  trading_name: {yaml_scalar(station['trading_name'])}")
        lines.append(f"  brand_name: {yaml_scalar(station['brand_name'])}")
        lines.append(f"  postcode: {yaml_scalar(station['postcode'])}")
        lines.append(f"  e5: {yaml_scalar(station['e5'])}")
        lines.append(f"  e10: {yaml_scalar(station['e10'])}")
        lines.append(f"  b7: {yaml_scalar(station['b7'])}")
    return "[]" if not lines else "\n".join(lines)


def print_output(matches: list[dict], output_format: str) -> None:
    if output_format == "csv":
        print(render_csv(matches))
        return
    if output_format == "json":
        print(json.dumps(matches, indent=2))
        return
    if output_format == "yaml":
        print(render_yaml(matches))
        return
    print(render_text_table(matches))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find petrol stations within radius using UK Gov Fuel Finder API (OAuth)."
    )
    parser.add_argument("latitude", type=parse_coordinate, help="Reference latitude")
    parser.add_argument("longitude", type=parse_coordinate, help="Reference longitude")
    parser.add_argument(
        "--miles", type=float, default=10.0, help="Search radius in miles (default: 10)"
    )
    parser.add_argument("--sort-by", choices=["distance", "e5", "e10", "b7"], default="distance")
    parser.add_argument(
        "--oauth-file",
        default=str(DEFAULT_OAUTH_FILE),
        help="Path to oauth yml credentials",
    )
    parser.add_argument("--cache-file", default=str(CACHE_FILE), help="Path to cache json")
    parser.add_argument(
        "--refresh-cache", action="store_true", help="Ignore cache and fetch fresh API data"
    )
    parser.add_argument(
        "--output",
        choices=["text", "csv", "json", "yaml"],
        default="text",
        help="Output format (default: text)",
    )
    return parser.parse_args()


def load_or_fetch_rows(args: argparse.Namespace) -> tuple[list[dict], list[dict]]:
    cache_path = Path(args.cache_file)
    cache_obj = None if args.refresh_cache else load_cache(cache_path)

    if cache_obj:
        return cache_obj.get("pfs", []), cache_obj.get("prices", [])

    oauth = parse_simple_yaml(Path(args.oauth_file))
    client_id = oauth.get("client_id")
    client_secret = oauth.get("client_secret")
    if not client_id or not client_secret:
        raise RuntimeError("OAuth credentials file must contain client_id and client_secret")

    token = get_access_token(client_id, client_secret)
    print(
        "Fetching latest data from the Fuel Finder API. This may take a few minutes...",
        file=sys.stderr,
        flush=True,
    )
    pfs_rows = fetch_batches(PFS_URL, token, dataset_name="stations")
    price_rows = fetch_batches(PRICES_URL, token, dataset_name="prices")
    save_cache(cache_path, pfs_rows, price_rows)
    return pfs_rows, price_rows


def build_matches(
    stations: list[dict], latitude: float, longitude: float, miles: float
) -> list[dict]:
    matches = []
    for station in stations:
        distance = haversine_miles(latitude, longitude, station["latitude"], station["longitude"])
        if distance <= miles:
            matches.append(
                {
                    "distance": distance,
                    "trading_name": station["trading_name"],
                    "brand_name": station["brand_name"],
                    "postcode": station["postcode"],
                    "e5": station["e5"],
                    "e10": station["e10"],
                    "b7": station["b7"],
                }
            )
    return matches


def render_matches(matches: list[dict], output_format: str) -> None:
    if not matches:
        if output_format in {"json", "yaml"}:
            print("[]")
            return
        if output_format == "csv":
            print("distance_miles,trading_name,brand_name,postcode,E5,E10,B7")
            return
        print("No petrol stations found within the given radius.")
        return
    print_output(matches, output_format)


def main() -> int:
    args = parse_args()

    try:
        pfs_rows, price_rows = load_or_fetch_rows(args)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    stations = build_joined_data(pfs_rows, price_rows)
    matches = build_matches(stations, args.latitude, args.longitude, args.miles)

    matches.sort(key=lambda x: sort_value(x, args.sort_by))

    try:
        render_matches(matches, args.output)
    except BrokenPipeError:
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
