"""Indexed replay of new missions, including real gaps and recorded tracker settings."""
import bisect
import json
import time

from mission import read_database, read_metadata


class MissionReplay:
    def __init__(self, path, clock=time.monotonic):
        self.path = path
        self.metadata = read_metadata(path)
        self.clock = clock
        with read_database(path) as db:
            self.index = db.execute("SELECT id, elapsed FROM records WHERE kind='packet' ORDER BY elapsed,id").fetchall()
        if not self.index:
            raise ValueError("Esta missão ainda não tem pacotes gravados para reproduzir.")
        self.origin = self.index[0][1]
        self.times = [elapsed - self.origin for _, elapsed in self.index]
        self.duration = self.times[-1]
        self.position = 0.0
        self.speed = 1.0
        self.playing = False
        self._anchor = clock()
        self._last_index = -1

    def seek(self, seconds):
        self.position = min(self.duration, max(0.0, float(seconds)))
        self._anchor = self.clock()
        self._last_index = -1

    def set_speed(self, speed):
        speed = float(speed)
        if speed not in {.5, 1, 2, 5, 10, 30, 60}:
            raise ValueError("Velocidade de reprodução inválida")
        self.tick()
        self.speed = speed

    def toggle(self):
        self.tick()
        if not self.playing and self.position >= self.duration:
            self.seek(0)
        self.playing = not self.playing
        self._anchor = self.clock()

    def tick(self):
        now = self.clock()
        if self.playing:
            self.position = min(self.duration, self.position + (now - self._anchor) * self.speed)
            if self.position >= self.duration:
                self.playing = False
        self._anchor = now
        return bisect.bisect_right(self.times, self.position) - 1

    def current(self):
        index = self.tick()
        if index == self._last_index:
            return None
        self._last_index = index
        with read_database(self.path) as db:
            row = db.execute("SELECT received_at, elapsed, payload FROM records WHERE id=?", (self.index[index][0],)).fetchone()
        return {"received_at": row[0], "elapsed": row[1], **json.loads(row[2])}

    def history(self, limit=2000):
        """Bounded plotting sample up to the playhead (never preload six hours into UI)."""
        index = bisect.bisect_right(self.times, self.position) - 1
        yield from self.records_between(0, index, limit)

    def records_between(self, first, last, limit=2000):
        if first > last:
            return
        step = max(1, (last - first + 1 + limit - 1) // limit)
        ids = [self.index[i][0] for i in range(first, last + 1, step)]
        if last > first and self.index[last - 1][0] not in ids:
            ids.append(self.index[last - 1][0])
        if not ids or ids[-1] != self.index[last][0]:
            ids.append(self.index[last][0])
        with read_database(self.path) as db:
            for start in range(0, len(ids), 900):
                batch = ids[start:start + 900]
                placeholders = ",".join("?" for _ in batch)
                for row in db.execute(f"SELECT received_at, elapsed, payload FROM records WHERE id IN ({placeholders}) ORDER BY elapsed,id", batch):
                    yield {"received_at": row[0], "elapsed": row[1], **json.loads(row[2])}

    def packet_age(self):
        index = bisect.bisect_right(self.times, self.position) - 1
        return self.position - self.times[index]
