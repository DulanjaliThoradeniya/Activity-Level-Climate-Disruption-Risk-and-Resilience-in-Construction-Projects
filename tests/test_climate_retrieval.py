import io
import json
import unittest
from datetime import date
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from update_climate_data import complete_station_readings, fetch_daily_readings, preserve_estimated_provenance, preserve_nearby_provenance, refresh_recent_observation_day


class ClimateRetrievalTests(unittest.TestCase):
    def test_pagination_fetches_every_page(self):
        pages = [
            {"code": 0, "data": {"readings": [{"timestamp": "first"}], "paginationToken": "a+b"}},
            {"code": 0, "data": {"readings": [{"timestamp": "second"}]}},
        ]
        urls = []

        def fake_open(request, timeout):
            urls.append(request.full_url)
            return io.BytesIO(json.dumps(pages.pop(0)).encode())

        with patch("update_climate_data.urlopen", side_effect=fake_open):
            readings = fetch_daily_readings("https://example.test?date={observed_date}", date(2026, 11, 10))
        self.assertEqual(len(readings), 2)
        self.assertEqual(parse_qs(urlparse(urls[1]).query)["paginationToken"], ["a+b"])

    def test_only_complete_station_days_are_used(self):
        readings = [
            {"timestamp": f"2026-11-10T{minute // 60:02d}:{minute % 60:02d}:00+08:00",
             "data": [{"stationId": "S111", "value": 0.1}]}
            for minute in range(0, 1440, 5)
        ]
        complete = complete_station_readings(readings, date(2026, 11, 10), {"S111"}, 5)
        self.assertEqual(len(complete["S111"]), 288)
        self.assertEqual(complete_station_readings(readings[:-1], date(2026, 11, 10), {"S111"}, 5), {})
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            complete_station_readings(readings + readings[:1], date(2026, 11, 10), {"S111"}, 5)

    def test_preserves_official_daily_values_and_never_adds_empty_rows(self):
        payload = {"stations": {"S111": [["2026-11-10", 12, 27, 30, 25]], "S24": []}}
        rain = [{"timestamp": f"2026-11-10T{m // 60:02d}:{m % 60:02d}:00+08:00",
                 "data": [{"stationId": "S111", "value": 0.1}]} for m in range(0, 1440, 5)]
        with patch("update_climate_data.fetch_daily_readings", side_effect=[rain, []]):
            refresh_recent_observation_day(payload, date(2026, 11, 10))
        self.assertEqual(payload["stations"]["S111"], [["2026-11-10", 12, 27, 30, 25]])
        self.assertEqual(payload["stations"]["S24"], [])

    def test_preserves_provenance_only_for_retained_estimates(self):
        previous = {
            "stations": {"S111": [["2026-11-02", 1.4, 28.6, 32.7, 24.8]]},
            "estimatedValues": {"S111": {"2026-11-02": ["rainfall", "mean_temperature"]}},
            "estimatedValuesSource": {"name": "Open-Meteo"},
        }
        payload = {"stations": {"S111": [["2026-11-02", 2.0, 28.6, 32.7, 24.8]]}}
        preserve_estimated_provenance(previous, payload)
        self.assertEqual(payload["estimatedValues"], {"S111": {"2026-11-02": ["mean_temperature"]}})
        self.assertEqual(payload["estimatedValuesSource"]["name"], "Open-Meteo")

    def test_preserves_nearby_source_only_for_retained_values(self):
        previous = {
            "stations": {"S1": [["2026-11-18", 1.0, None, None, None], ["2026-11-19", 2.0, None, None, None]]},
            "nearbyStationValues": {"S1": {"2026-11-18": {"station": "S2"}, "2026-11-19": {"station": "S2"}}},
            "nearbyStationValuesSource": {"name": "NEA"},
        }
        payload = {"stations": {"S1": [["2026-11-18", 1.0, None, None, None], ["2026-11-19", 3.0, None, None, None]]}}
        preserve_nearby_provenance(previous, payload)
        self.assertEqual(payload["nearbyStationValues"], {"S1": {"2026-11-18": {"station": "S2"}}})


if __name__ == "__main__":
    unittest.main()
