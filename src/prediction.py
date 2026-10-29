"""Cliente Tawhiri e controle de predição da interface de solo."""
import math
import queue
import threading
import time
from collections import deque
from datetime import datetime, timezone
import tkinter as tk

import requests

from antenna import Position, calculate_pointing, position_from_packet

URL = "https://api.v2.sondehub.org/tawhiri"


def predict(position, timestamp, ascent, burst, descent, descending=False, get=requests.get, with_details=False):
    values = (ascent, burst, descent)
    if not all(math.isfinite(v) for v in values) or ascent <= 0 or descent <= 0:
        raise ValueError("As taxas devem ser números positivos e finitos.")
    if not descending and burst <= position.altitude:
        raise ValueError("A altitude de estouro deve ser maior que a altitude atual.")
    params = dict(launch_latitude=position.latitude, launch_longitude=position.longitude % 360,
                  launch_altitude=position.altitude, launch_datetime=timestamp,
                  ascent_rate=ascent, burst_altitude=position.altitude + 1 if descending else burst,
                  descent_rate=descent, profile="standard_profile")
    response = get(URL, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise ValueError(data["error"].get("description", "Erro de predição"))
    stages = {}
    for stage in data.get("prediction", []):
        if stage.get("stage") not in ("ascent", "descent"):
            continue
        points = []
        for item in stage.get("trajectory", []):
            lat, lon, alt = (float(item[key]) for key in ("latitude", "longitude", "altitude"))
            if not all(math.isfinite(x) for x in (lat, lon, alt)) or not -90 <= lat <= 90 or not 0 <= lon <= 360:
                raise ValueError("O serviço retornou coordenadas inválidas.")
            points.append((lat, (lon + 180) % 360 - 180, alt, item["datetime"]))
        stages[stage["stage"]] = points
    if not stages.get("descent"):
        raise ValueError("O serviço não retornou uma trajetória de descida.")
    if descending:
        stages["ascent"] = []
    result = (stages, data.get("request", {}).get("dataset", "—"))
    return (*result, params, data) if with_details else result


class PredictionControls:
    def init_prediction(self):
        self.prediction_samples = deque()
        self.prediction_results = queue.Queue()
        self.prediction_busy = False
        self.prediction_generation = 0
        self.prediction_last_attempt = 0
        self.prediction_objects = []
        self.prediction_last_success = None

    def build_prediction_card(self):
        card = self._card(self.sidebar_content, "Previsão de queda", "SondeHub")
        card.pack(fill=tk.X, pady=(0, 9))
        self.pred_burst = tk.StringVar(value="30000")
        self.pred_ascent = tk.StringVar(value="5")
        self.pred_descent = tk.StringVar(value="5")
        for label, var in (("Estouro previsto (m)", self.pred_burst),
                           ("Subida inicial (m/s)", self.pred_ascent),
                           ("Descida ao nível do mar (m/s)", self.pred_descent)):
            row = tk.Frame(card, bg="#151e29")
            row.pack(fill=tk.X, pady=2)
            self._label(row, label, 9, "#94a3b5").pack(side=tk.LEFT)
            tk.Entry(row, textvariable=var, width=8, bg="#1b2633", fg="#f2f5f8", insertbackground="white", relief=tk.FLAT).pack(side=tk.RIGHT)
        self.pred_auto = tk.BooleanVar(value=False)
        tk.Checkbutton(card, text="Atualizar a cada 60 s", variable=self.pred_auto,
                       command=self._prediction_auto_changed, bg="#151e29", fg="#f2f5f8",
                       selectcolor="#1b2633", activebackground="#151e29", activeforeground="white").pack(anchor="w", pady=(5, 0))
        tk.Button(card, text="Calcular agora", command=self.start_prediction, bg="#1b2633", fg="#f2f5f8",
                  activebackground="#263547", activeforeground="white", relief=tk.FLAT, padx=10, pady=4).pack(anchor="w", pady=4)
        self.lbl_prediction = self._label(card, "Aguardando GPS 3D. Requer internet.", 9, "#94a3b5", wraplength=285, justify=tk.LEFT)
        self.lbl_prediction.pack(anchor="w")
        self.root.after(1000, self._prediction_tick)

    def _prediction_auto_changed(self):
        if self.pred_auto.get():
            self.start_prediction()

    def reset_prediction(self):
        self.prediction_generation += 1
        self.prediction_samples.clear()
        self.prediction_last_success = None
        for item in self.prediction_objects:
            item.delete()
        self.prediction_objects.clear()
        self.lbl_prediction.config(text="Aguardando GPS 3D. Requer internet.")

    def record_prediction_sample(self, record):
        if self.replay or not record.get("gps_valid"):
            return
        try:
            position = position_from_packet(record["fields"])
            stamp = float(record["elapsed"])
        except (ValueError, TypeError, KeyError):
            return
        if self.prediction_samples and stamp <= self.prediction_samples[-1][0]:
            self.prediction_samples.clear()
        self.prediction_samples.append((stamp, position.altitude))
        while self.prediction_samples and stamp - self.prediction_samples[0][0] > 60:
            self.prediction_samples.popleft()

    def start_prediction(self):
        if self.prediction_busy:
            return
        self.prediction_last_attempt = time.monotonic()
        if self.replay:
            self.lbl_prediction.config(text="Predição disponível apenas ao vivo, não na reprodução.")
            return
        if not self.is_connected or not self.current_record or not self.current_record.get("gps_valid") or self._packet_age() is None or self._packet_age() > 15:
            self.lbl_prediction.config(text="Aguardando telemetria recente com GPS 3D." + (f" Última previsão: {self.prediction_last_success}." if self.prediction_last_success else ""))
            return
        try:
            position = position_from_packet(self.current_record["fields"])
            ascent, burst, descent = (float(v.get().replace(",", ".")) for v in (self.pred_ascent, self.pred_burst, self.pred_descent))
            if not all(math.isfinite(v) for v in (ascent, burst, descent)) or ascent <= 0 or descent <= 0:
                raise ValueError("Informe taxas positivas e finitas.")
            rate = None
            if len(self.prediction_samples) > 1:
                first, last = self.prediction_samples[0], self.prediction_samples[-1]
                if last[0] - first[0] >= 15:
                    rate = (last[1] - first[1]) / (last[0] - first[0])
            descending = rate is not None and rate < -1
            if rate is not None and rate > 0.5:
                ascent = rate
            if not descending and burst <= position.altitude:
                raise ValueError("Estouro previsto deve ser maior que a altitude atual.")
        except ValueError as error:
            self.lbl_prediction.config(text=str(error))
            return
        timestamp = self.current_record.get("received_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        generation = self.prediction_generation
        origin_mission = self.mission
        context = {"position": {"latitude": position.latitude, "longitude": position.longitude, "altitude": position.altitude},
                   "position_received_at": timestamp, "vertical_rate": rate,
                   "configured_ascent_rate": float(self.pred_ascent.get().replace(",", ".")),
                   "configured_burst_altitude": burst, "configured_descent_rate": descent,
                   "descending": descending}
        self.prediction_busy = True
        self.lbl_prediction.config(text="Calculando no SondeHub…")
        def worker():
            try:
                result = predict(position, timestamp, ascent, burst, descent, descending, with_details=True)
                self.prediction_results.put((generation, result, None, descending, origin_mission, context))
            except Exception as error:
                self.prediction_results.put((generation, None, str(error), descending, origin_mission, context))
        threading.Thread(target=worker, daemon=True).start()

    def _prediction_tick(self):
        if self.closing:
            return
        while not self.prediction_results.empty():
            generation, result, error, descending, origin_mission, context = self.prediction_results.get_nowait()
            self.prediction_busy = False
            if generation != self.prediction_generation:
                continue
            if error:
                old = f" Última previsão mantida: {self.prediction_last_success}." if self.prediction_last_success else ""
                self.lbl_prediction.config(text=f"Falha: {error}.{old}")
                continue
            stages, dataset, parameters, response = result
            if origin_mission is not None and origin_mission is self.mission:
                try:
                    origin_mission.event("prediction", input=context, request=parameters, response=response,
                                         completed_at=datetime.now(timezone.utc).isoformat())
                except (RuntimeError, ValueError, TypeError) as exc:
                    self.lbl_prediction.config(text=f"Previsão não foi salva: {exc}")
                    continue
            for item in self.prediction_objects:
                item.delete()
            self.prediction_objects.clear()
            for name, color in (("ascent", "#4aa8ff"), ("descent", "#ffb547")):
                points = stages.get(name, [])
                if len(points) > 1:
                    self.prediction_objects.append(self.map_widget.set_path([(p[0], p[1]) for p in points], color=color, width=3))
            if stages.get("ascent"):
                point = stages["ascent"][-1]
                self.prediction_objects.append(self.map_widget.set_marker(point[0], point[1], text="Estouro previsto"))
            point = stages["descent"][-1]
            self.prediction_objects.append(self.map_widget.set_marker(point[0], point[1], text="Queda prevista"))
            try:
                landing = datetime.fromisoformat(point[3].replace("Z", "+00:00")).astimezone().strftime("%d/%m %H:%M")
            except ValueError:
                landing = point[3]
            distance = ""
            if self.tracker_position:
                distance = f" · {calculate_pointing(self.tracker_position, Position(point[0], point[1], point[2])).surface_distance / 1000:.1f} km do tracker"
            self.prediction_last_success = datetime.now().strftime("%H:%M:%S")
            phase = "descida" if descending else "subida"
            self.lbl_prediction.config(text=f"Queda estimada: {landing}{distance}\n{point[0]:.5f}, {point[1]:.5f}\nAtualizada às {self.prediction_last_success} · {phase}\nModelo: {dataset}\nDescida configurada ao nível do mar; estimativa sujeita a erro.")
        if self.pred_auto.get() and not self.prediction_busy and time.monotonic() - self.prediction_last_attempt >= 60:
            self.start_prediction()
        self.root.after(1000, self._prediction_tick)
