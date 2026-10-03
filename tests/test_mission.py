from pathlib import Path
import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mission import MissionWriter, export_csv, export_kml, export_raw, packet_rows, read_database, read_metadata
from telemetry import MESSAGE_LIMIT, PacketParser, enrich_packet, sanitize_message
from replay import MissionReplay

FRAME = b'------- PT2UNB -------\nRSSI:-80 dBm | SNR:4.5 dB\nLat:-15\nLon:-47\nAlt:3000\nAltB:2900\nSat:12\nFix:3\nT:20\nP:850\nTime:23:59:59\nPitch:1\nRoll:2\nYaw:3\nAXavg:0\nAYavg:1\nAZavg:2\nBat:4.1\nAck:0\n----------------------\n'


def packet():
    return enrich_packet(PacketParser().feed(FRAME, '2026-09-19T23:59:59.000+00:00')[0],
                         {'latitude': -15.1, 'longitude': -47.1, 'altitude': 1000}, [350, 10])


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.writers = []

    def tearDown(self):
        for writer in self.writers:
            writer.abandon()
        self.temp.cleanup()

    def writer(self, **kwargs):
        writer = MissionWriter.create(self.temp.name, 'Teste', sync_interval=.01, retry_interval=.01, **kwargs)
        self.writers.append(writer)
        return writer

    def test_exact_bytes_structured_values_and_export(self):
        writer = self.writer()
        data = b'\xff\x00\r\n  ' + FRAME
        writer.submit('raw', data)
        writer.submit('packet', packet(), elapsed=1)
        self.assertTrue(writer.flush())
        output = Path(self.temp.name) / 'raw.bin'
        export_raw(writer.path, output)
        self.assertEqual(output.read_bytes(), data)
        output = Path(self.temp.name) / 'data.csv'
        self.assertEqual(export_csv(writer.path, output), 1)
        with output.open(encoding='utf-8-sig', newline='') as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[0]['Bat'], '4.1')
        self.assertEqual(rows[0]['U'], '')
        self.assertEqual(rows[0]['gps_time_utc'], '23:59:59')
        self.assertGreater(float(rows[0]['distance_m']), 0)
        self.assertEqual(rows[0]['tracker_altitude_msl_m'], '1000')

    def test_kml_export_track_markers_and_no_gps(self):
        writer = self.writer()
        writer.submit('packet', packet(), elapsed=1)
        self.assertTrue(writer.flush())
        output = Path(self.temp.name) / 'trajeto.kml'
        self.assertEqual(export_kml(writer.path, output), 1)
        ns = {'k': 'http://www.opengis.net/kml/2.2'}
        document = ET.parse(output).getroot().find('k:Document', ns)
        names = [node.findtext('k:name', namespaces=ns) for node in document.findall('k:Placemark', ns)]
        self.assertEqual(names, ['Primeiro ponto', 'Ponto mais alto', 'Último ponto', 'Tracker / antena', 'Trajeto da sonda'])
        route = document.find('k:Placemark/k:LineString/k:coordinates', ns).text.strip()
        self.assertEqual(route, '-47.0000000,-15.0000000,3000.0')
        tracker = document.findall('k:Placemark', ns)[3].find('k:Point/k:coordinates', ns).text
        self.assertEqual(tracker, '-47.1000000,-15.1000000,1000.0')

        empty = self.writer()
        empty.submit('packet', dict(packet(), gps_valid=False), elapsed=1)
        self.assertTrue(empty.flush())
        destination = Path(self.temp.name) / 'vazio.kml'
        with self.assertRaises(ValueError):
            export_kml(empty.path, destination)
        self.assertFalse(destination.exists())

    def test_resume_keeps_mission_and_closing_blocks_resume(self):
        writer = self.writer()
        path = writer.path
        writer.event('configuration', tracker={'latitude':0, 'longitude':0, 'altitude':1}, orientation=[2, 3])
        writer.submit('packet', packet())
        self.assertTrue(writer.close())
        resumed = MissionWriter(path, sync_interval=.01)
        self.writers.append(resumed)
        self.assertEqual(resumed.metadata['id'], writer.metadata['id'])
        self.assertEqual(resumed.metadata['orientation'], [2, 3])
        self.assertEqual(resumed.snapshot()['written_packets'], 1)
        resumed.submit('packet', packet())
        self.assertTrue(resumed.close(end_mission=True))
        self.assertEqual(read_metadata(path)['status'], 'closed')
        self.assertEqual(len(list(packet_rows(path))), 2)
        with self.assertRaises(ValueError):
            MissionWriter(path)

    def test_exclusive_writer_lock(self):
        writer = self.writer()
        with self.assertRaises(ValueError):
            MissionWriter(writer.path)

    def test_full_disk_bounded_queue_keeps_newest_and_recovers(self):
        writer = self.writer(max_buffer_bytes=3000)
        self.assertTrue(writer.flush())
        original = writer._commit
        failed = threading.Event()
        def failure(*args):
            failed.set()
            raise OSError('Disco cheio')
        writer._commit = failure
        writer.submit('raw', b'x' * 100)
        self.assertTrue(failed.wait(1))
        for n in range(100):
            writer.submit('raw', str(n).encode().ljust(100, b'.'))
        status = writer.snapshot()
        self.assertLessEqual(status['buffer_bytes'], 3000)
        self.assertGreater(status['dropped_records'], 0)
        self.assertFalse(writer.close(timeout=.03))
        self.assertTrue(writer.thread.is_alive())
        writer._commit = original
        self.assertTrue(writer.flush())
        with read_database(writer.path) as db:
            raw = [r[0] for r in db.execute("SELECT payload FROM records WHERE kind='raw' ORDER BY id")]
            losses = [json.loads(r[0]) for r in db.execute("SELECT payload FROM records WHERE kind='event'") if json.loads(r[0])['event']=='buffer_loss']
        self.assertTrue(raw[-1].startswith(b'99.'))
        self.assertFalse(any(r.startswith(b'0.') for r in raw))
        self.assertEqual(sum(e['records'] for e in losses), writer.snapshot()['dropped_records'])
        self.assertIsNone(writer.snapshot()['error'])
        final_dropped = writer.snapshot()['dropped_records']
        self.assertTrue(writer.close())
        resumed = MissionWriter(writer.path, sync_interval=.01)
        self.writers.append(resumed)
        self.assertEqual(resumed.snapshot()['dropped_records'], final_dropped)

    def test_transaction_failure_rolls_back_before_retry(self):
        writer = self.writer()
        self.assertTrue(writer.flush())
        original = writer._commit
        failed = threading.Event()
        def partial(db, batch, loss):
            with db:
                r = batch[0]
                db.execute('INSERT INTO records(kind,received_at,elapsed,payload) VALUES(?,?,?,?)', (r.kind,r.received_at,r.elapsed,r.payload))
                failed.set()
                raise sqlite3.OperationalError('simulated partial write')
        writer._commit = partial
        writer.submit('packet', packet())
        self.assertTrue(failed.wait(1))
        self.assertEqual(list(packet_rows(writer.path)), [])
        writer._commit = original
        self.assertTrue(writer.flush())
        self.assertEqual(len(list(packet_rows(writer.path))), 1)

    def test_periodic_commit_without_manual_flush(self):
        writer = self.writer()
        writer.submit('raw', b'hello')
        deadline = time.monotonic() + 1
        while writer.snapshot()['last_sync'] is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(writer.snapshot()['last_sync'])
        self.assertEqual(writer.snapshot()['pending'], 0)

    def test_uncertain_commit_retry_is_idempotent(self):
        writer = self.writer()
        self.assertTrue(writer.flush())
        original = writer._commit
        attempts = [0]
        def commit_then_error(db, batch, loss):
            original(db, batch, loss)
            attempts[0] += 1
            if attempts[0] == 1:
                raise OSError('simulated failure after durable commit')
        writer._commit = commit_then_error
        writer.submit('packet', packet())
        self.assertTrue(writer.flush())
        self.assertEqual(len(list(packet_rows(writer.path))), 1)
        self.assertEqual(writer.snapshot()['written_packets'], 1)
        with read_database(writer.path) as db:
            events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM records WHERE kind='event'")]
        self.assertTrue(any(e['event'] == 'storage_recovered' for e in events))

    def test_crash_after_commit_is_recoverable(self):
        writer = self.writer()
        path = writer.path
        self.assertTrue(writer.close())
        script = """
import os, sys
sys.path.insert(0, sys.argv[1])
from mission import MissionWriter
writer = MissionWriter(sys.argv[2], sync_interval=.01)
writer.submit('raw', b'crash-proof')
assert writer.flush()
os._exit(0)
"""
        result = subprocess.run([sys.executable, '-c', script, str(Path(__file__).resolve().parents[1] / 'src'), str(path)], timeout=10)
        self.assertEqual(result.returncode, 0)
        resumed = MissionWriter(path, sync_interval=.01)
        self.writers.append(resumed)
        with read_database(path) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute("SELECT payload FROM records WHERE kind='raw'").fetchone()[0], b'crash-proof')


class ParserTests(unittest.TestCase):
    def test_b_format_chunked_and_decimal_snr(self):
        parser, packets = PacketParser(), []
        for byte in FRAME:
            packets += parser.feed(bytes([byte]), 'now')
        self.assertEqual(len(packets), 1)
        self.assertTrue(packets[0]['complete'])
        self.assertEqual(packets[0]['fields']['SNR'], 4.5)

    def test_formatted_copy_is_suppressed_but_identical_rf_packets_are_kept(self):
        fields = b'Lat:0\nLon:0\nAlt:100\nFix:3\nMZ:0\n'
        reception = b'Pacote Recebido! RSSI:-80 | SNR:2\nTexto Bruto: PT2UNB\n' + fields + b'------- PT2UNB -------\n' + fields + b'======\n'
        packets = PacketParser().feed(reception * 2, 'now')
        self.assertEqual(len(packets), 2)
        self.assertTrue(all(p['gps_valid'] for p in packets))
        self.assertNotEqual(packets[0]['source_frame'], packets[1]['source_frame'])

    def test_incomplete_raw_is_not_replaced_with_formatted_stale_fields(self):
        raw = b'Pacote Recebido! RSSI:-80 | SNR:2\nTexto Bruto: PT2UNB\nLat:1\nLon:2\nFix:3\n'
        formatted = b'------- PT2UNB -------\nLat:1\nLon:2\nAlt:9999\nFix:3\nMZ:0\n======\n'
        packets = PacketParser().feed(raw + formatted, 'now')
        self.assertEqual(len(packets), 1)
        self.assertNotIn('Alt', packets[0]['fields'])
        self.assertFalse(packets[0]['gps_valid'])

    def test_missing_and_invalid_fields_never_inherit_values(self):
        packets = PacketParser().feed(FRAME + FRAME.replace(b'Alt:3000\n', b'').replace(b'Bat:4.1', b'Bat:nan'), 'now')
        self.assertNotIn('Alt', packets[-1]['fields'])
        self.assertIsNone(packets[-1]['fields']['Bat'])
        self.assertFalse(packets[-1]['gps_valid'])
        self.assertFalse(packets[-1]['complete'])

    def test_partial_final_packet_and_corrupt_serial_bytes(self):
        parser = PacketParser()
        packets = parser.feed(b'------- PT2UNB -------\nLat:\xff\nLon:0\nAlt:1\nFix:3', 'now')
        packets += parser.finish('later')
        self.assertEqual(len(packets), 1)
        self.assertFalse(packets[0]['complete'])
        self.assertIsNone(packets[0]['fields']['Lat'])

    def test_link_metrics_do_not_leak_into_unframed_next_packet(self):
        packets = PacketParser().feed(b'RSSI:-80 | SNR:5\nLat:0\nLon:0\nAlt:1\nFix:3\nAck:0\n'
                                     b'Lat:0\nLon:0\nAlt:2\nFix:3\nAck:0\n', 'now')
        self.assertEqual(packets[0]['fields']['RSSI'], -80)
        self.assertNotIn('RSSI', packets[1]['fields'])

    def test_message_and_station_lines_become_notices_without_touching_packets(self):
        lines = (b'[ESTACAO] ID:B N:3\n[MSG] ENVIADA Id:B7 Tentativa:2\n[MSG] DESCONHECIDA Id:B7\n[ACK] Ack:6\n'
                 b'[MSG] RECEBIDA Id:C4 De:C Hora:12:34:57 RSSI:-80 SNR:5 Texto:Pouso RSSI:-1 SNR:9 Lat:0\n')
        parser = PacketParser()
        packets = parser.feed(FRAME + lines + FRAME, 'now')
        self.assertEqual(len(packets), 2)
        self.assertTrue(all(p['complete'] for p in packets))
        self.assertEqual(packets[1]['fields']['Lat'], -15)
        notices = parser.take_notices()
        self.assertEqual(notices[0], {'kind': 'station', 'id': 'B', 'total': 3, 'received_at': 'now'})
        self.assertEqual(notices[1], {'kind': 'message', 'status': 'sent', 'id': 'B7', 'attempts': 2, 'received_at': 'now'})
        self.assertEqual(notices[2], {'kind': 'ack', 'value': 6, 'received_at': 'now'})
        self.assertEqual(len(notices), 4)
        self.assertEqual(notices[3]['text'], 'Pouso RSSI:-1 SNR:9 Lat:0')
        self.assertEqual((notices[3]['from'], notices[3]['time'], notices[3]['rssi']), ('C', '12:34:57', -80))
        self.assertEqual(parser.take_notices(), [])

    def test_message_text_is_ascii_single_line_and_limited(self):
        self.assertEqual(sanitize_message('  Pouso\tà  direção\n norte '), 'Pouso a direcao norte')
        self.assertEqual(len(sanitize_message('x' * 150)), MESSAGE_LIMIT)
        self.assertEqual(len(sanitize_message('x' * 150, limit=None)), 150)


# Reuse fixtures without duplicating the storage tests.
class ReplayTests(unittest.TestCase):
    setUp, tearDown, writer = WriterTests.setUp, WriterTests.tearDown, WriterTests.writer

    def test_timing_pause_speed_seek_gaps_and_recorded_settings(self):
        writer = self.writer()
        for elapsed in (0, 1, 30, 21600):
            item = packet()
            item['tracker']['altitude'] = 1000 + elapsed
            writer.submit('packet', item, elapsed=elapsed)
        self.assertTrue(writer.flush())
        clock = [100.0]
        replay = MissionReplay(writer.path, clock=lambda: clock[0])
        self.assertEqual(replay.current()['tracker']['altitude'], 1000)
        clock[0] += 100
        self.assertEqual(replay.tick(), 0)  # paused
        replay.toggle()
        clock[0] += 2
        self.assertEqual(replay.current()['elapsed'], 1)
        clock[0] += 15
        self.assertGreater(replay.packet_age() + (clock[0] - replay._anchor), 10)
        replay.tick()
        self.assertGreater(replay.packet_age(), 10)
        replay.set_speed(10)
        clock[0] += 2
        self.assertEqual(replay.current()['elapsed'], 30)
        replay.seek(21600)
        self.assertEqual(replay.current()['tracker']['altitude'], 22600)
        self.assertFalse(replay.playing)
        replay.seek(0)
        self.assertEqual(len(list(replay.history())), 1)
        replay.seek(21600)
        self.assertEqual(len(list(replay.history(limit=2))), 3)

    def test_empty_mission_refused(self):
        writer = self.writer()
        self.assertTrue(writer.flush())
        with self.assertRaises(ValueError):
            MissionReplay(writer.path)

if __name__ == '__main__':
    unittest.main()
