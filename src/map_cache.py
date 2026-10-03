"""Cache do mapa em SQLite, para o tracker funcionar sem internet.

Todo tile (imagem de 256×256 px) que o mapa baixa é gravado em `mapa_cache.db` e, nas
próximas vezes, vem do arquivo, com ou sem rede. `RegionDownload` baixa com antecedência
os tiles de uma área inteira, numa faixa de zoom, para a missão de campo.
"""
import io
import sqlite3
import threading
from pathlib import Path

import requests
import tkintermapview
from PIL import Image, ImageTk
from tkintermapview.utility_functions import decimal_to_osm

CACHE_PATH = Path(__file__).with_name("mapa_cache.db")
USER_AGENT = "Balao-Base/1.0 (+https://github.com/LCA-UnB/Balao-Base)"
TIMEOUT = (3.05, 5)  # conexão e leitura, em segundos
DOWNLOAD_WORKERS = 2  # a política de uso do OpenStreetMap pede no máximo 2 conexões
MAX_CONSECUTIVE_FAILURES = 20  # o download da região para quando a internet cai
REGION_MIN_ZOOM = 3
REGION_MAX_TILES = 20_000
TILE_SIZE_KB = 15  # média aproximada de um tile, só para a estimativa de espaço


class TileCache:
    """Tiles por (servidor, zoom, x, y), com uma conexão compartilhada entre as threads do mapa.

    Erros de SQLite nunca derrubam o mapa: leitura vira "não está no cache" e gravação é ignorada.
    """

    def __init__(self, path=CACHE_PATH):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tiles (server TEXT, zoom INTEGER, x INTEGER, y INTEGER, "
            "image BLOB NOT NULL, PRIMARY KEY (server, zoom, x, y)) WITHOUT ROWID"
        )
        self._db.commit()

    def _query(self, sql, args):
        try:
            with self._lock:
                return self._db.execute(sql, args).fetchone()
        except sqlite3.Error:
            return None

    def get(self, server, zoom, x, y):
        row = self._query("SELECT image FROM tiles WHERE server=? AND zoom=? AND x=? AND y=?", (server, zoom, x, y))
        return row[0] if row else None

    def has(self, server, zoom, x, y):
        return self._query("SELECT 1 FROM tiles WHERE server=? AND zoom=? AND x=? AND y=?", (server, zoom, x, y)) is not None

    def put(self, server, zoom, x, y, image):
        try:
            with self._lock:
                self._db.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?, ?)", (server, zoom, x, y, image))
                self._db.commit()
        except sqlite3.Error:
            pass

    def close(self):
        with self._lock:
            self._db.close()


def open_tile_cache(path=CACHE_PATH):
    """Abre o cache; sem ele (pasta sem permissão, arquivo corrompido) o mapa segue só online."""
    try:
        return TileCache(path)
    except sqlite3.Error as error:
        print(f"Cache do mapa indisponível ({path}): {error}")
        return None


def tile_url(server, zoom, x, y):
    return server.replace("{x}", str(x)).replace("{y}", str(y)).replace("{z}", str(zoom))


def fetch_tile(server, zoom, x, y, session=requests):
    """Baixa um tile; None se o servidor não tem imagem ali. Falha de rede levanta RequestException."""
    response = session.get(tile_url(server, zoom, x, y), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def decode_tile(data):
    """Imagem do tile, ou None se os bytes não forem uma imagem (ex.: página de erro do servidor)."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except OSError:
        return None


class OfflineMapView(tkintermapview.TkinterMapView):
    """TkinterMapView que procura cada tile no cache antes da internet e grava o que baixar."""

    def __init__(self, *args, cache=None, **kwargs):
        self.cache = cache  # antes do super(): as threads de carga começam a pedir tiles lá dentro
        super().__init__(*args, **kwargs)

    def request_image(self, zoom, x, y, db_cursor=None):
        server, key = self.tile_server, f"{zoom}{x}{y}"
        data = self.cache.get(server, zoom, x, y) if self.cache else None
        fetched = data is None
        if fetched:
            try:
                data = fetch_tile(server, zoom, x, y)
            except requests.RequestException:
                return self.empty_tile_image  # sem rede: não guarda, para tentar de novo depois
        image = decode_tile(data) if data is not None else None
        if image is None or not self.running:
            if self.running and server == self.tile_server:
                self.tile_image_cache[key] = self.empty_tile_image
            return self.empty_tile_image
        if fetched and self.cache:
            self.cache.put(server, zoom, x, y, data)
        image_tk = ImageTk.PhotoImage(image)
        if server == self.tile_server:
            self.tile_image_cache[key] = image_tk
        return image_tk


def _tile_ranges(top_left, bottom_right, zoom):
    x0, y0 = decimal_to_osm(*top_left, zoom)
    x1, y1 = decimal_to_osm(*bottom_right, zoom)
    last = 2 ** zoom - 1
    xs = range(max(0, int(min(x0, x1))), min(last, int(max(x0, x1))) + 1)
    ys = range(max(0, int(min(y0, y1))), min(last, int(max(y0, y1))) + 1)
    return xs, ys


def region_tile_count(top_left, bottom_right, min_zoom, max_zoom):
    """Quantos tiles cobrem o retângulo entre dois pontos (lat, lon), sem listá-los."""
    return sum(len(xs) * len(ys) for xs, ys in
               (_tile_ranges(top_left, bottom_right, zoom) for zoom in range(min_zoom, max_zoom + 1)))


def region_tiles(top_left, bottom_right, min_zoom, max_zoom):
    """Tiles (zoom, x, y) do retângulo, do zoom menor para o maior."""
    for zoom in range(min_zoom, max_zoom + 1):
        xs, ys = _tile_ranges(top_left, bottom_right, zoom)
        for x in xs:
            for y in ys:
                yield zoom, x, y


class RegionDownload:
    """Baixa em segundo plano os tiles de uma região que ainda não estão no cache.

    O progresso (`done`, `failed`, `total`) é lido pela interface; tiles já salvos contam como feitos.
    """

    def __init__(self, cache, server, tiles, workers=DOWNLOAD_WORKERS):
        self.cache, self.server = cache, server
        self.total, self.done, self.failed = len(tiles), 0, 0
        self.offline = False  # parou por falhas seguidas (sem internet ou servidor recusando)
        self._pending = list(reversed(tiles))  # pop() do fim: zooms menores primeiro
        self._consecutive_failures = 0
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._threads = [threading.Thread(target=self._work, daemon=True) for _ in range(workers)]

    def start(self):
        for thread in self._threads:
            thread.start()
        return self

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self):
        return self._cancel.is_set() and not self.offline

    @property
    def running(self):
        return any(thread.is_alive() for thread in self._threads)

    def _download(self, session, zoom, x, y):
        if self.cache.has(self.server, zoom, x, y):
            return True
        try:
            data = fetch_tile(self.server, zoom, x, y, session)
        except requests.RequestException:
            return False
        if data is None:
            return True  # o servidor não tem imagem ali; não há o que salvar
        if decode_tile(data) is None:
            return False
        self.cache.put(self.server, zoom, x, y, data)
        return True

    def _work(self):
        session = requests.Session()
        while not self._cancel.is_set():
            with self._lock:
                if not self._pending:
                    return
                tile = self._pending.pop()
            ok = self._download(session, *tile)
            with self._lock:
                self.done += 1
                self.failed += not ok
                self._consecutive_failures = 0 if ok else self._consecutive_failures + 1
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self.offline = True
                    self._cancel.set()
