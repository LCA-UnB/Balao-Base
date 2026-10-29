import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from antenna import Position
from prediction import predict


class PredictionTest(unittest.TestCase):
    def test_request_and_longitude_conversion(self):
        response = Mock()
        response.json.return_value = {'request': {'dataset': 'model'}, 'prediction': [
            {'stage': 'ascent', 'trajectory': [{'latitude': -15, 'longitude': 312, 'altitude': 30000, 'datetime': 't'}]},
            {'stage': 'descent', 'trajectory': [{'latitude': -16, 'longitude': 311, 'altitude': 1000, 'datetime': 't'}]},
        ]}
        get = Mock(return_value=response)
        stages, dataset = predict(Position(-15, -48, 1000), '2026-10-28T12:00:00Z', 5, 30000, 5, get=get)
        self.assertEqual(get.call_args.kwargs['params']['launch_longitude'], 312)
        self.assertEqual(stages['descent'][0][1], -49)
        self.assertEqual(dataset, 'model')
        stages, _ = predict(Position(-15, -48, 10000), 'time', 5, 30000, 5, descending=True, get=get)
        self.assertEqual(get.call_args.kwargs['params']['burst_altitude'], 10001)
        self.assertEqual(stages['ascent'], [])

    def test_bad_parameters_and_missing_descent(self):
        with self.assertRaises(ValueError):
            predict(Position(-15, -48, 1000), 'time', 5, 900, 5)
        response = Mock()
        response.json.return_value = {'prediction': []}
        with self.assertRaises(ValueError):
            predict(Position(-15, -48, 1000), 'time', 5, 30000, 5, get=Mock(return_value=response))

class PredictionArchiveTest(unittest.TestCase):
    def test_save_and_compare_closed_mission(self):
        import csv
        import tempfile
        from mission import MissionWriter, export_prediction_comparison, read_database
        with tempfile.TemporaryDirectory() as directory:
            writer = MissionWriter.create(directory, 'voo', sync_interval=.01)
            response = {'request': {'dataset': '2026-10-24T12:00:00Z'}, 'prediction': [
                {'stage': 'descent', 'trajectory': [
                    {'latitude': -15.0, 'longitude': 312.0, 'altitude': 1000, 'datetime': '2026-10-24T15:00:00Z'}]}]}
            writer.event('prediction', input={'position': {'latitude': -15.1, 'longitude': -48.0, 'altitude': 10000},
                                             'position_received_at': '2026-10-24T14:00:00Z', 'descending': True},
                         request={'ascent_rate': 5, 'burst_altitude': 10001, 'descent_rate': 6},
                         response=response, completed_at='2026-10-24T14:01:00Z')
            path = writer.path
            self.assertTrue(writer.close(end_mission=True))
            output = Path(directory) / 'comparacao.csv'
            self.assertEqual(export_prediction_comparison(path, output, -15.0, -48.0), 1)
            with output.open(encoding='utf-8-sig', newline='') as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(float(row['erro_m']), 0)
            self.assertEqual(row['fase'], 'descida')
            self.assertEqual(row['dataset_utc'], '2026-10-24T12:00:00Z')
            self.assertEqual(float(row['longitude_prevista']), -48)
            with read_database(path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM records WHERE kind='event' AND json_extract(payload, '$.event')='prediction'").fetchone()[0], 1)

    def test_no_predictions_does_not_create_file(self):
        import tempfile
        from mission import MissionWriter, export_prediction_comparison
        with tempfile.TemporaryDirectory() as directory:
            writer = MissionWriter.create(directory, 'voo', sync_interval=.01)
            path = writer.path
            self.assertTrue(writer.close(end_mission=True))
            output = Path(directory) / 'empty.csv'
            with self.assertRaises(ValueError):
                export_prediction_comparison(path, output, -15, -48)
            self.assertFalse(output.exists())

if __name__ == '__main__':
    unittest.main()
