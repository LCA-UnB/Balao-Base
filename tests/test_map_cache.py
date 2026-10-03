import io
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import map_cache
from map_cache import (
    MAX_CONSECUTIVE_FAILURES, OfflineMapView, RegionDownload, TileCache, region_tile_count, region_tiles,
)

SERVER = "https://tiles.example/{z}/{x}/{y}.png"
OTHER_SERVER = "https://outro.example/{z}/{x}/{y}.png"
# Retângulo de ~20 km sobre Brasília (lat, lon).
TOP_LEFT, BOTTOM_RIGHT = (-15.70, -47.95), (-15.85, -47.80)


def png_bytes(color=(10, 20, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buffer, format="PNG")
    return buffer.getvalue()


def wait(download, timeout=10):
    deadline = time.monotonic() + timeout
    while download.running and time.monotonic() < deadline:
        time.sleep(0.01)
    return download


class CacheTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "mapa_cache.db"
        self.cache = TileCache(self.path)

    def tearDown(self):
        self.cache.close()
        self.temp.cleanup()


class TileCacheTests(CacheTestCase):
    def test_stores_tiles_per_server_and_survives_reopen(self):
        self.cache.put(SERVER, 10, 1, 2, b"abc")
        self.assertEqual(self.cache.get(SERVER, 10, 1, 2), b"abc")
        self.assertTrue(self.cache.has(SERVER, 10, 1, 2))
        self.assertIsNone(self.cache.get(OTHER_SERVER, 10, 1, 2))
        self.assertFalse(self.cache.has(SERVER, 10, 2, 1))
        self.cache.close()
        self.cache = TileCache(self.path)
        self.assertEqual(self.cache.get(SERVER, 10, 1, 2), b"abc")

    def test_closed_database_degrades_to_cache_miss(self):
        self.cache.close()
        self.assertIsNone(self.cache.get(SERVER, 1, 0, 0))
        self.cache.put(SERVER, 1, 0, 0, b"abc")  # não levanta
        self.cache = TileCache(self.path)

    def test_open_tile_cache_returns_none_when_path_is_unusable(self):
        with patch("builtins.print"):
            self.assertIsNone(map_cache.open_tile_cache(Path(self.temp.name) / "nao-existe" / "x.db"))


class RegionTests(unittest.TestCase):
    def test_count_matches_listed_tiles_in_zoom_order(self):
        tiles = list(region_tiles(TOP_LEFT, BOTTOM_RIGHT, 3, 15))
        self.assertEqual(region_tile_count(TOP_LEFT, BOTTOM_RIGHT, 3, 15), len(tiles))
        self.assertEqual([zoom for zoom, _, _ in tiles], sorted(zoom for zoom, _, _ in tiles))
        self.assertEqual(len(set(tiles)), len(tiles))
        self.assertEqual(region_tile_count(TOP_LEFT, BOTTOM_RIGHT, 3, 3), 1)
        self.assertLess(len(tiles), map_cache.REGION_MAX_TILES)

    def test_corner_order_does_not_matter_and_tiles_stay_on_the_globe(self):
        self.assertEqual(region_tile_count(BOTTOM_RIGHT, TOP_LEFT, 3, 12), region_tile_count(TOP_LEFT, BOTTOM_RIGHT, 3, 12))
        world = list(region_tiles((85, -180), (-85, 179.999), 2, 2))
        self.assertEqual(len(world), 16)
        self.assertTrue(all(0 <= x < 4 and 0 <= y < 4 for _, x, y in world))


class RegionDownloadTests(CacheTestCase):
    def test_downloads_missing_tiles_and_skips_cached_ones(self):
        tiles = list(region_tiles(TOP_LEFT, BOTTOM_RIGHT, 3, 10))
        self.cache.put(SERVER, *tiles[0], b"ja-salvo")
        fetched = []
        def fetch(server, zoom, x, y, session):
            fetched.append((zoom, x, y))
            return png_bytes()
        with patch.object(map_cache, "fetch_tile", side_effect=fetch):
            download = wait(RegionDownload(self.cache, SERVER, tiles).start())
        self.assertEqual((download.done, download.failed, download.total), (len(tiles), 0, len(tiles)))
        self.assertFalse(download.cancelled or download.offline)
        self.assertNotIn(tiles[0], fetched)
        self.assertEqual(len(fetched), len(tiles) - 1)
        self.assertTrue(all(self.cache.has(SERVER, *tile) for tile in tiles))
        self.assertEqual(self.cache.get(SERVER, *tiles[0]), b"ja-salvo")

    def test_missing_tiles_on_server_and_invalid_images(self):
        tiles = [(5, 0, 0), (5, 0, 1)]
        responses = {(5, 0, 0): None, (5, 0, 1): b"<html>bloqueado</html>"}
        with patch.object(map_cache, "fetch_tile", side_effect=lambda server, z, x, y, session: responses[(z, x, y)]):
            download = wait(RegionDownload(self.cache, SERVER, tiles, workers=1).start())
        self.assertEqual((download.done, download.failed), (2, 1))
        self.assertFalse(any(self.cache.has(SERVER, *tile) for tile in tiles))

    def test_stops_when_the_connection_drops(self):
        tiles = list(region_tiles(TOP_LEFT, BOTTOM_RIGHT, 3, 12))
        with patch.object(map_cache, "fetch_tile", side_effect=requests.ConnectionError):
            download = wait(RegionDownload(self.cache, SERVER, tiles).start())
        self.assertTrue(download.offline)
        self.assertFalse(download.cancelled)
        self.assertLess(download.done, MAX_CONSECUTIVE_FAILURES + 2)

    def test_cancel_stops_the_workers(self):
        tiles = list(region_tiles(TOP_LEFT, BOTTOM_RIGHT, 3, 12))
        def slow_fetch(*args):
            time.sleep(0.01)
            return png_bytes()
        with patch.object(map_cache, "fetch_tile", side_effect=slow_fetch):
            download = RegionDownload(self.cache, SERVER, tiles).start()
            download.cancel()
            wait(download)
        self.assertTrue(download.cancelled)
        self.assertLess(download.done, len(tiles))


@unittest.skipUnless(os.environ.get("DISPLAY"), "PhotoImage requires a display (Xvfb supported)")
class OfflineMapViewTests(CacheTestCase):
    def setUp(self):
        super().setUp()
        import tkinter as tk
        self.root = tk.Tk()
        self.empty = object()
        self.view = SimpleNamespace(
            cache=self.cache, tile_server=SERVER, running=True, empty_tile_image=self.empty, tile_image_cache={},
        )

    def tearDown(self):
        self.root.destroy()
        super().tearDown()

    def request(self, *tile):
        return OfflineMapView.request_image(self.view, *tile)

    def test_cached_tile_loads_without_network(self):
        self.cache.put(SERVER, 12, 1, 2, png_bytes())
        with patch.object(map_cache, "fetch_tile", side_effect=AssertionError("não deveria acessar a rede")):
            image = self.request(12, 1, 2)
        self.assertIsNot(image, self.empty)
        self.assertIs(self.view.tile_image_cache["1212"], image)

    def test_downloaded_tile_is_written_to_the_cache(self):
        data = png_bytes()
        with patch.object(map_cache, "fetch_tile", return_value=data):
            image = self.request(12, 3, 4)
        self.assertIsNot(image, self.empty)
        self.assertEqual(self.cache.get(SERVER, 12, 3, 4), data)

    def test_offline_miss_shows_empty_tile_and_retries_later(self):
        with patch.object(map_cache, "fetch_tile", side_effect=requests.Timeout):
            self.assertIs(self.request(12, 5, 6), self.empty)
        self.assertNotIn("1256", self.view.tile_image_cache)
        self.assertFalse(self.cache.has(SERVER, 12, 5, 6))

    def test_works_without_cache(self):
        self.view.cache = None
        with patch.object(map_cache, "fetch_tile", return_value=png_bytes()):
            self.assertIsNot(self.request(12, 7, 8), self.empty)


if __name__ == "__main__":
    unittest.main()
