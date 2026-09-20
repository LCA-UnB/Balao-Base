"""Exercise six hours of mission timestamps at an accelerated input rate.

Run: python tools/soak_mission.py --packets 21600 --rate 100
This is not a six-hour wall-clock certification.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import resource
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mission import MissionWriter, packet_rows, read_database
from replay import MissionReplay
from telemetry import PacketParser, enrich_packet

parser = argparse.ArgumentParser()
parser.add_argument('--packets', type=int, default=21600)
parser.add_argument('--rate', type=float, default=100)
parser.add_argument('--output', type=Path)
args = parser.parse_args()
if args.packets < 1 or args.rate <= 0:
    parser.error('packets and rate must be positive')
folder = args.output or Path(tempfile.mkdtemp(prefix='balao-soak-'))
writer = MissionWriter.create(folder, 'Ensaio acelerado de 6 horas')
telemetry_parser = PacketParser()
started = time.monotonic()
baseline_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
origin = datetime.now(timezone.utc)
max_buffer = 0
for number in range(args.packets):
    timestamp = origin + timedelta(seconds=number)
    frame = (f'------- PT2UNB -------\nRSSI:-95 dBm | SNR:5 dB\nLat:{-15 + number / 1000000:.6f}\nLon:-47\n'
             f'Alt:{1000 + number % 30000}\nAltB:999\nSat:15\nFix:3\nT:15\nP:900\nTime:{timestamp:%H:%M:%S}\n'
             'Pitch:0\nRoll:0\nYaw:45\nAXavg:0\nAYavg:0\nAZavg:9.8\nBat:4\nAck:0\n----------------------\n').encode()
    received = timestamp.isoformat(timespec='milliseconds')
    writer.submit('raw', frame, received_at=received, elapsed=number)
    for packet in telemetry_parser.feed(frame, received):
        writer.submit('packet', enrich_packet(packet, {'latitude':-15, 'longitude':-47, 'altitude':1000}, [0, 0]),
                      received_at=received, elapsed=number)
    max_buffer = max(max_buffer, writer.snapshot()['buffer_bytes'])
    delay = started + (number + 1) / args.rate - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    if (number + 1) % 6000 == 0:
        print(f'{number + 1}/{args.packets} packets; queue={writer.snapshot()["buffer_bytes"]} bytes', flush=True)
assert writer.close(end_mission=True, timeout=10)
status = writer.snapshot()
assert status['written_packets'] == args.packets
assert status['dropped_records'] == 0
with read_database(writer.path) as db:
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert db.execute("SELECT count(*) FROM records WHERE kind='raw'").fetchone()[0] == args.packets
replay = MissionReplay(writer.path)
replay.seek(args.packets - 1)
assert replay.current()['fields']['Alt'] == 1000 + (args.packets - 1) % 30000
assert len(list(replay.history())) <= 2001
result = {'packets':args.packets, 'mission_hours':args.packets / 3600, 'wall_seconds':round(time.monotonic() - started, 2),
          'max_buffer_bytes':max_buffer, 'rss_growth_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - baseline_memory,
          'dropped_records':status['dropped_records'], 'database':str(writer.path), 'result':'PASS'}
print(json.dumps(result, indent=2), flush=True)
