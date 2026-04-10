# Fuel Station Search (UK Gov Fuel Finder API)

This is a small command-line Python tool that finds UK petrol stations within a radius of a given latitude/longitude using the UK Government Fuel Finder API.

It can:
- fetch station and fuel price data from the API,
- cache API responses locally,
- sort nearby stations by distance or fuel price,
- output results as a human-friendly table (default), CSV, JSON, or YAML.

---

## What this tool does

Given a coordinate pair (`latitude` and `longitude`), the tool:

1. Reads your API client credentials from a local credentials file.
2. Requests an OAuth access token from the UK Gov endpoint.
3. Downloads station + fuel price batch data.
4. Joins station and price records.
5. Computes distance from your input location.
6. Filters stations within the selected mile radius.
7. Sorts and prints results in your chosen output format.

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) installed

Install dependencies:

```bash
uv sync
```

---

## Getting UK Gov OAuth credentials ("OATH creds")

This tool needs **OAuth client credentials** (`client_id`, `client_secret`) for the UK Gov Fuel Finder API.

1. Go to: https://www.developer.fuel-finder.service.gov.uk/public-api
2. Register to get a **GOV.UK One Login** (if you do not already have one), then sign in.
3. Click the link to **"Access Public API"**.
4. This will generate your **Client ID** and **Client secret**.

### Store credentials locally

By default, this tool reads credentials from:

`~/.fuel_station_oath.yml`

Add your generated values to that file:

Example file contents:
```yaml
client_id: <your Client ID>
client_secret: <your Client secret>
```

You can override this path with `--oauth-file`.

---

## Usage

### Basic

```bash
uv run fuel_station_search.py 51.5074 -0.1278
```

### Getting latitude/longitude from Google Maps

1. Open Google Maps and find your location.
2. Right-click the exact point on the map.
3. In the pop-up menu, click the coordinate pair shown at the top.
4. Google Maps copies the coordinates to your clipboard.

You can then paste those values directly into this tool.

Coordinates with trailing commas (for example pasted from Google Maps) are accepted:

```bash
uv run fuel_station_search.py 51.5074, -0.1278,
```

### Options

- `--miles <float>`: search radius in miles (default `10`)
- `--sort-by {distance,e5,e10,b7}`: sort key (default `distance`)
- `--oauth-file <path>`: path to OAuth credential YAML
- `--cache-file <path>`: path to JSON cache file
- `--refresh-cache`: ignore cache and fetch fresh API data
- `--output {text,csv,json,yaml}`: output format (default `text`)

### Output examples

```bash
# Default table output
uv run fuel_station_search.py 51.5074 -0.1278

# CSV output
uv run fuel_station_search.py 51.5074 -0.1278 --output csv

# JSON output
uv run fuel_station_search.py 51.5074 -0.1278 --output json

# YAML output
uv run fuel_station_search.py 51.5074 -0.1278 --output yaml
```

---

## Development

Run tests:

```bash
uv run pytest -q
```

Run lint/type checks:

```bash
uv run ruff check .
uv run ty check .
```
