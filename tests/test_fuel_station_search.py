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
import sys
from email.message import Message
from pathlib import Path
from urllib import error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fuel_station_search as fss


class _FakeResponse:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_parse_coordinate_accepts_trailing_comma_and_whitespace() -> None:
    assert fss.parse_coordinate(" 51.5074, ") == pytest.approx(51.5074)
    assert fss.parse_coordinate("-0.1278,") == pytest.approx(-0.1278)


def test_parse_coordinate_rejects_invalid() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        fss.parse_coordinate("north")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("sw1a1aa", "SW1A 1AA"),
        ("SW1A 1AA", "SW1A 1AA"),
        (" ec1a 1bb ", "EC1A 1BB"),
        ("GIR0AA", "GIR 0AA"),
        ("not a postcode", None),
        ("", None),
    ],
)
def test_normalize_postcode(value: str, expected: str | None) -> None:
    assert fss.normalize_postcode(value) == expected


def test_parse_reference_location_detects_coordinates_and_postcode() -> None:
    latitude, longitude, postcode = fss.parse_reference_location(["51.5074,", "-0.1278,"])
    assert latitude == pytest.approx(51.5074)
    assert longitude == pytest.approx(-0.1278)
    assert postcode is None

    assert fss.parse_reference_location(["sw1a", "1aa"]) == (None, None, "SW1A 1AA")

    with pytest.raises(argparse.ArgumentTypeError):
        fss.parse_reference_location(["north"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        (12, 12.0),
        ("3.14", 3.14),
        ("nope", None),
    ],
)
def test_parse_float_branches(value, expected) -> None:
    assert fss.parse_float(value) == expected


def test_sort_value_distance_and_missing_price() -> None:
    station = {"distance": 2.3}
    assert fss.sort_value(station, "distance") == 2.3
    assert fss.sort_value(station, "e5") == float("inf")


def test_parse_simple_yaml_ignores_comments_and_garbage(tmp_path: Path) -> None:
    content = """---
# comment
client_id: abc
garbage_line
client_secret: 'xyz'
"""
    path = tmp_path / "oauth.yml"
    path.write_text(content, encoding="utf-8")
    assert fss.parse_simple_yaml(path) == {"client_id": "abc", "client_secret": "xyz"}


def test_http_json_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fss.request, "urlopen", lambda req, timeout: _FakeResponse('{"ok": true}'))
    assert fss.http_json("https://example.test") == {"ok": True}


def test_http_json_empty_payload_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fss.request, "urlopen", lambda req, timeout: _FakeResponse(""))
    assert fss.http_json("https://example.test") is None


def test_http_json_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("temporary")
        return _FakeResponse('{"v": 1}')

    monkeypatch.setattr(fss.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fss.time, "sleep", lambda s: sleeps.append(s))

    assert fss.http_json("https://example.test", retries=3) == {"v": 1}
    assert calls["n"] == 3
    assert sleeps == [1.5, 3.0]


def test_http_json_raises_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fss.request,
        "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(TimeoutError()),
    )
    monkeypatch.setattr(fss.time, "sleep", lambda s: None)
    with pytest.raises(TimeoutError):
        fss.http_json("https://example.test", retries=1)


def test_http_json_does_not_retry_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        raise error.HTTPError(req.full_url, 403, "forbidden", hdrs=Message(), fp=None)

    monkeypatch.setattr(fss.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fss.time, "sleep", lambda s: pytest.fail("HTTP errors are not retried"))

    with pytest.raises(error.HTTPError):
        fss.http_json("https://example.test", retries=3)

    assert calls["n"] == 1


def test_lookup_postcode_coordinates_success(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_http_json(url: str, timeout: int, retries: int):
        seen["url"] = url
        seen["timeout"] = timeout
        seen["retries"] = retries
        return {"status": 200, "result": {"latitude": "51.501009", "longitude": -0.141588}}

    monkeypatch.setattr(fss, "http_json", fake_http_json)

    latitude, longitude = fss.lookup_postcode_coordinates("sw1a 1aa")

    assert latitude == pytest.approx(51.501009)
    assert longitude == pytest.approx(-0.141588)
    assert seen == {
        "url": "https://api.postcodes.io/postcodes/SW1A%201AA",
        "timeout": 30,
        "retries": 1,
    }


def test_lookup_postcode_coordinates_rejects_invalid_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fss,
        "http_json",
        lambda *a, **k: pytest.fail("invalid postcode should not call API"),
    )

    with pytest.raises(RuntimeError, match="Invalid UK postcode"):
        fss.lookup_postcode_coordinates("north")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"status": 404, "result": None},
        {"status": 200, "result": []},
        {"status": 200, "result": {"latitude": None, "longitude": -0.141588}},
    ],
)
def test_lookup_postcode_coordinates_validates_response(
    payload: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fss, "http_json", lambda *a, **k: payload)

    with pytest.raises(RuntimeError):
        fss.lookup_postcode_coordinates("SW1A 1AA")


def test_lookup_postcode_coordinates_converts_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_http_json(*a, **k):
        raise error.HTTPError("https://example.test", 404, "not found", hdrs=Message(), fp=None)

    monkeypatch.setattr(fss, "http_json", fake_http_json)

    with pytest.raises(RuntimeError, match="was not found"):
        fss.lookup_postcode_coordinates("SW1A 1AA")


def test_get_access_token_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fss, "http_json", lambda *a, **k: {"access_token": "t1"})
    assert fss.get_access_token("id", "secret") == "t1"

    monkeypatch.setattr(fss, "http_json", lambda *a, **k: {"data": {"token": "t2"}})
    assert fss.get_access_token("id", "secret") == "t2"


def test_get_access_token_forbidden_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_http_json(*a, **k):
        raise error.HTTPError("https://example.test", 403, "forbidden", hdrs=Message(), fp=None)

    monkeypatch.setattr(fss, "http_json", fake_http_json)

    with pytest.raises(RuntimeError, match="OAuth token request was denied"):
        fss.get_access_token("id", "secret")


def test_get_access_token_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fss, "http_json", lambda *a, **k: {"x": 1})
    with pytest.raises(RuntimeError):
        fss.get_access_token("id", "secret")


def test_fetch_batches_stops_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_http_json(url, headers=None):
        if url.endswith("1"):
            return [{"a": 1}]
        raise error.HTTPError(url, 404, "not found", hdrs=Message(), fp=None)

    monkeypatch.setattr(fss, "http_json", fake_http_json)
    assert fss.fetch_batches("https://x/{}", "token") == [{"a": 1}]


def test_fetch_batches_raises_for_non_404_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_http_json(url, headers=None):
        raise error.HTTPError(url, 500, "boom", hdrs=Message(), fp=None)

    monkeypatch.setattr(fss, "http_json", fake_http_json)
    with pytest.raises(error.HTTPError):
        fss.fetch_batches("https://x/{}", "token")


def test_fetch_batches_validates_list_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fss, "http_json", lambda url, headers=None: {"not": "a list"})
    with pytest.raises(RuntimeError):
        fss.fetch_batches("https://x/{}", "token")


def test_load_and_save_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(fss.time, "time", lambda: 1_000.0)
    fss.save_cache(cache, [{"p": 1}], [{"q": 2}])
    assert fss.load_cache(cache)["pfs"] == [{"p": 1}]


def test_load_cache_missing_expired_bad(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.json"
    assert fss.load_cache(missing) is None

    expired = tmp_path / "expired.json"
    expired.write_text(json.dumps({"timestamp": 1, "pfs": [], "prices": []}), encoding="utf-8")
    monkeypatch.setattr(fss.time, "time", lambda: 1 + fss.CACHE_TTL_SECONDS + 1)
    assert fss.load_cache(expired) is None

    bad = tmp_path / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    assert fss.load_cache(bad) is None


def test_fuel_map_and_pick_b7() -> None:
    assert fss.fuel_map([]) == {}
    prices = fss.fuel_map(
        [{"fuel_type": "E5", "price": 149.9}, {"fuel_type": "B7", "price": 155.1}]
    )
    assert prices["E5"] == 149.9
    assert fss.pick_b7({"B7_PREMIUM": 160}) == 160


def test_build_joined_data_filters_invalid_and_joins() -> None:
    pfs_rows = [
        {"node_id": "1", "location": {"latitude": "51", "longitude": "-0.1", "postcode": "A"}},
        {"node_id": None, "location": {"latitude": "51", "longitude": "-0.1"}},
        {"node_id": "3", "location": {"latitude": None, "longitude": "-0.1"}},
    ]
    price_rows = [{"node_id": "1", "fuel_prices": [{"fuel_type": "E10", "price": 140.0}]}]
    joined = fss.build_joined_data(pfs_rows, price_rows)
    assert len(joined) == 1
    assert joined[0]["e10"] == 140.0


def test_renderers_and_empty_paths(capsys: pytest.CaptureFixture[str]) -> None:
    matches = [
        {
            "distance": 1.2,
            "trading_name": "T",
            "brand_name": "B",
            "postcode": "P",
            "e5": None,
            "e10": 140,
            "b7": 150,
        }
    ]
    assert "distance_miles" in fss.render_text_table(matches)
    assert "distance_miles" in fss.render_csv(matches)
    assert "trading_name" in fss.render_yaml(matches)

    fss.render_matches([], "json")
    assert capsys.readouterr().out.strip() == "[]"

    fss.render_matches([], "csv")
    assert "distance_miles" in capsys.readouterr().out

    fss.render_matches([], "text")
    assert "No petrol stations found" in capsys.readouterr().out


def test_parse_args_and_build_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "fuel_station_search.py",
            "--output",
            "json",
            "--miles",
            "5",
            "51.5,",
            "--",
            "-0.12,",
        ],
    )
    args = fss.parse_args()
    assert args.latitude == pytest.approx(51.5)
    assert args.longitude == pytest.approx(-0.12)
    assert args.postcode is None
    assert args.output == "json"

    stations = [
        {
            "latitude": 51.5,
            "longitude": -0.12,
            "trading_name": "T",
            "brand_name": "B",
            "postcode": "P",
            "e5": 1,
            "e10": 2,
            "b7": 3,
        }
    ]
    assert len(fss.build_matches(stations, 51.5, -0.12, 0.001)) == 1
    assert fss.build_matches(stations, 0.0, 0.0, 0.001) == []


def test_parse_args_accepts_unquoted_postcode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "fuel_station_search.py",
            "sw1a",
            "1aa",
            "--output",
            "json",
        ],
    )

    args = fss.parse_args()

    assert args.latitude is None
    assert args.longitude is None
    assert args.postcode == "SW1A 1AA"
    assert args.output == "json"


def test_resolve_reference_location_uses_postcode_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fss, "lookup_postcode_coordinates", lambda postcode: (51.5, -0.12))
    args = argparse.Namespace(latitude=None, longitude=None, postcode="SW1A 1AA")

    assert fss.resolve_reference_location(args) == (51.5, -0.12)


def test_main_accepts_postcode_without_live_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "fuel_station_search.py",
            "SW1A",
            "1AA",
            "--output",
            "json",
        ],
    )
    calls: list[tuple[str, str]] = []

    def fake_lookup(postcode: str) -> tuple[float, float]:
        calls.append(("lookup", postcode))
        return 51.5, -0.12

    def fake_load(args: argparse.Namespace) -> tuple[list[dict], list[dict]]:
        calls.append(("load", args.postcode))
        return (
            [
                {
                    "node_id": "1",
                    "trading_name": "T",
                    "brand_name": "B",
                    "location": {"latitude": "51.5", "longitude": "-0.12", "postcode": "P"},
                }
            ],
            [{"node_id": "1", "fuel_prices": [{"fuel_type": "E10", "price": 140.0}]}],
        )

    monkeypatch.setattr(fss, "lookup_postcode_coordinates", fake_lookup)
    monkeypatch.setattr(fss, "load_or_fetch_rows", fake_load)

    assert fss.main() == 0

    assert calls == [("lookup", "SW1A 1AA"), ("load", "SW1A 1AA")]
    output = json.loads(capsys.readouterr().out)
    assert output[0]["postcode"] == "P"
    assert output[0]["e10"] == 140.0


def test_load_or_fetch_rows_cache_hit_and_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"timestamp": 1_000_000, "pfs": [{"a": 1}], "prices": [{"b": 2}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(fss.time, "time", lambda: 1_000_000)

    args = argparse.Namespace(
        cache_file=str(cache),
        refresh_cache=False,
        oauth_file=str(tmp_path / "oauth.yml"),
    )
    pfs_rows, price_rows = fss.load_or_fetch_rows(args)
    assert pfs_rows == [{"a": 1}]
    assert price_rows == [{"b": 2}]

    oauth = tmp_path / "oauth.yml"
    oauth.write_text("client_id: id\nclient_secret: secret\n", encoding="utf-8")
    args.refresh_cache = True
    args.oauth_file = str(oauth)

    monkeypatch.setattr(fss, "get_access_token", lambda cid, sec: "token")
    monkeypatch.setattr(
        fss,
        "fetch_batches",
        lambda tmpl, token, dataset_name="data": (
            [{"kind": "pfs"}] if "fuel-prices" not in tmpl else [{"kind": "prices"}]
        ),
    )
    pfs_rows2, price_rows2 = fss.load_or_fetch_rows(args)
    assert pfs_rows2 == [{"kind": "pfs"}]
    assert price_rows2 == [{"kind": "prices"}]


def test_load_or_fetch_rows_missing_creds_raises(tmp_path: Path) -> None:
    oauth = tmp_path / "oauth.yml"
    oauth.write_text("client_id: only\n", encoding="utf-8")
    args = argparse.Namespace(
        cache_file=str(tmp_path / "cache.json"),
        refresh_cache=True,
        oauth_file=str(oauth),
    )
    with pytest.raises(RuntimeError):
        fss.load_or_fetch_rows(args)
