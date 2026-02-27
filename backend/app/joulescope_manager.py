"""
Joulescope Manager - Background capture service with window-based processing.
Runs continuously when started, saves data to CSV, notifies subscribers for live updates.
Sempre tenta reconectar em caso de falha. Timestamps em horário de São Paulo.
"""

import csv
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import joulescope
import numpy as np

TZ_SAO_PAULO = ZoneInfo("America/Sao_Paulo")
RETRY_DELAY_SEC = 10  # Espera entre tentativas de reconexão


class JoulescopeManager:
    """Manages Joulescope device connection and continuous data capture."""

    CSV_HEADERS = [
        'Timestamp', 'Window Start', 'Window End', 'Duration (s)', 'Samples',
        'Current Mean (A)', 'Current Std (A)', 'Current Min (A)', 'Current Max (A)',
        'Voltage Mean (V)', 'Voltage Std (V)', 'Voltage Min (V)', 'Voltage Max (V)',
        'Power Mean (W)', 'Power Std (W)', 'Power Min (W)', 'Power Max (W)',
        'Energy (J)', 'Energy (mWh)', 'Cumulative Energy (J)', 'Cumulative Energy (mWh)',
        'Data Gap Warning'
    ]

    def __init__(self, log_dir: str):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._subscribers: list[Callable] = []
        self._status = {
            'running': False,
            'output_file': None,
            'start_time': None,
            'window_count': 0,
            'total_energy': 0.0,
            'last_window': None,
            'reconnect_count': 0,
            'last_error': None,
        }

    def subscribe(self, callback: Callable):
        """Subscribe to window updates. Callback receives dict with window stats."""
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    def _notify(self, data: dict):
        with self._lock:
            callbacks = list(self._subscribers)
        for cb in callbacks:
            try:
                cb(data)
            except Exception:
                pass

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def get_devices(self) -> list[dict]:
        """Scan for Joulescope devices."""
        try:
            devices = joulescope.scan()
            return [
                {'id': str(d), 'name': str(d)}
                for d in devices
            ]
        except Exception as e:
            return [{'error': str(e)}]

    def _calculate_statistics(self, data: np.ndarray) -> Optional[dict]:
        if data is None or len(data) == 0:
            return None
        current = data[:, 0]
        voltage = data[:, 1]
        power = current * voltage
        return {
            'samples': len(data),
            'current_mean': float(np.mean(current, dtype=np.float64)),
            'current_std': float(np.std(current, dtype=np.float64)),
            'current_min': float(np.min(current)),
            'current_max': float(np.max(current)),
            'voltage_mean': float(np.mean(voltage, dtype=np.float64)),
            'voltage_std': float(np.std(voltage, dtype=np.float64)),
            'voltage_min': float(np.min(voltage)),
            'voltage_max': float(np.max(voltage)),
            'power_mean': float(np.mean(power, dtype=np.float64)),
            'power_std': float(np.std(power, dtype=np.float64)),
            'power_min': float(np.min(power)),
            'power_max': float(np.max(power)),
        }

    def _calculate_energy(self, data: np.ndarray, sampling_rate: float) -> tuple[float, float]:
        if data is None or len(data) == 0:
            return 0.0, 0.0
        current = data[:, 0]
        voltage = data[:, 1]
        power = current * voltage
        dt = 1.0 / sampling_rate
        energy_joules = float(np.sum(power) * dt)
        energy_mwh = energy_joules * (1000.0 / 3600.0)
        return energy_joules, energy_mwh

    def _initialize_csv(self, csv_path: Path):
        if not csv_path.exists():
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.CSV_HEADERS)
        else:
            try:
                with open(csv_path, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    existing = next(reader, None)
                    if existing is None or len(existing) != len(self.CSV_HEADERS) or existing[-1] != 'Data Gap Warning':
                        backup = csv_path.with_suffix('.csv.backup')
                        if csv_path.stat().st_size > 0:
                            shutil.copy2(csv_path, backup)
                        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                            writer = csv.writer(f)
                            writer.writerow(self.CSV_HEADERS)
            except Exception:
                pass

    def _now_sp(self) -> datetime:
        """Retorna datetime atual em horário de São Paulo."""
        return datetime.now(TZ_SAO_PAULO)

    def _log_to_csv(self, csv_path: Path, window_start: datetime, window_end: datetime,
                    duration: float, stats: dict, energy_joules: float, energy_mwh: float,
                    total_energy: float, gap_detected: bool):
        total_mwh = total_energy * (1000.0 / 3600.0)
        now = self._now_sp()
        # Alta resolução: 12 casas decimais para corrente/potência/energia, 9 para tensão, microsegundos em timestamps (SP)
        row = [
            now.strftime('%Y-%m-%d %H:%M:%S.%f'),
            window_start.strftime('%Y-%m-%d %H:%M:%S.%f'),
            window_end.strftime('%Y-%m-%d %H:%M:%S.%f'),
            f'{duration:.6f}', stats['samples'],
            f'{stats["current_mean"]:.12f}', f'{stats["current_std"]:.12f}',
            f'{stats["current_min"]:.12f}', f'{stats["current_max"]:.12f}',
            f'{stats["voltage_mean"]:.9f}', f'{stats["voltage_std"]:.9f}',
            f'{stats["voltage_min"]:.9f}', f'{stats["voltage_max"]:.9f}',
            f'{stats["power_mean"]:.12f}', f'{stats["power_std"]:.12f}',
            f'{stats["power_min"]:.12f}', f'{stats["power_max"]:.12f}',
            f'{energy_joules:.12f}', f'{energy_mwh:.12f}',
            f'{total_energy:.12f}', f'{total_mwh:.12f}',
            'GAP' if gap_detected else ''
        ]
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row)
            f.flush()
            os.fsync(f.fileno())

    def _capture_loop(self, window_duration: float, output_file: str,
                      sampling_rate: Optional[float], max_windows: int):
        """Main capture loop - nunca para, sempre tenta reconectar em caso de falha."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        csv_path = self.log_dir / Path(output_file).name
        self._initialize_csv(csv_path)

        total_energy = 0.0
        last_read_time = None
        window_num = 0
        sampling_rate = sampling_rate or 1000000.0

        while self._running:
            try:
                devices = joulescope.scan()
                if not devices:
                    with self._lock:
                        self._status['last_error'] = 'Nenhum dispositivo encontrado'
                        self._status['reconnect_count'] = self._status.get('reconnect_count', 0) + 1
                    print(f"[Joulescope] Dispositivo não encontrado. Reconectando em {RETRY_DELAY_SEC}s...")
                    for _ in range(RETRY_DELAY_SEC):
                        if not self._running:
                            break
                        time.sleep(1)
                    continue

                device = joulescope.scan_require_one(config='auto')

                with device:
                    buffer_duration = max(2.0, window_duration * 2)
                    try:
                        device.parameter_set('buffer_duration', buffer_duration)
                    except Exception:
                        pass

                    if sampling_rate is None or sampling_rate == 1000000.0:
                        try:
                            device.start()
                            time.sleep(0.2)
                            test_data = device.read(contiguous_duration=0.1)
                            device.stop()
                            if test_data is not None and len(test_data) > 0:
                                sampling_rate = len(test_data) / 0.1
                        except Exception:
                            pass

                    with self._lock:
                        self._status['start_time'] = self._now_sp().isoformat()
                        self._status['sampling_rate'] = sampling_rate
                        self._status['last_error'] = None

                    device.start()
                    time.sleep(0.5)

                    while self._running:
                        if max_windows > 0 and window_num >= max_windows:
                            break

                        window_num += 1
                        window_start = self._now_sp()

                        try:
                            data = device.read(contiguous_duration=window_duration)
                        except Exception as e:
                            with self._lock:
                                self._status['last_error'] = str(e)
                            raise  # Sai do inner loop, reconecta

                        if data is None or len(data) == 0:
                            time.sleep(0.1)
                            continue

                        window_end = self._now_sp()
                        actual_duration = (window_end - window_start).total_seconds()
                        actual_samples = len(data)
                        expected_samples = int(sampling_rate * actual_duration)
                        sample_tolerance = max(100, int(sampling_rate * 0.01))
                        gap_detected = abs(actual_samples - expected_samples) > sample_tolerance

                        if last_read_time and (window_start - last_read_time).total_seconds() > window_duration * 1.1:
                            gap_detected = True
                        last_read_time = window_end

                        stats = self._calculate_statistics(data)
                        if stats is None:
                            continue

                        energy_joules, energy_mwh = self._calculate_energy(data, sampling_rate)
                        total_energy += energy_joules

                        try:
                            self._log_to_csv(csv_path, window_start, window_end, actual_duration,
                                            stats, energy_joules, energy_mwh, total_energy, gap_detected)
                        except Exception:
                            pass

                        window_data = {
                            'window_num': window_num,
                            'window_start': window_start.isoformat(),
                            'window_end': window_end.isoformat(),
                            'duration': actual_duration,
                            'stats': stats,
                            'energy_joules': energy_joules,
                            'energy_mwh': energy_mwh,
                            'total_energy': total_energy,
                            'total_energy_mwh': total_energy * (1000.0 / 3600.0),
                            'samples': actual_samples,
                        }
                        with self._lock:
                            self._status['window_count'] = window_num
                            self._status['total_energy'] = total_energy
                            self._status['last_window'] = window_data
                        self._notify(window_data)

                    try:
                        device.stop()
                    except Exception:
                        pass
                    break  # max_windows atingido, sai do retry loop

            except Exception as e:
                with self._lock:
                    self._status['last_error'] = str(e)
                    self._status['reconnect_count'] = self._status.get('reconnect_count', 0) + 1
                print(f"[Joulescope] Erro: {e}. Reconectando em {RETRY_DELAY_SEC}s...")
                for _ in range(RETRY_DELAY_SEC):
                    if not self._running:
                        break
                    time.sleep(1)

        with self._lock:
            self._status['running'] = False
            self._status['output_file'] = str(csv_path)

    def start_capture(self, window_duration: float = 10.0, output_file: str = 'joulescope_log.csv',
                     sampling_rate: Optional[float] = None, max_windows: int = 0) -> dict:
        """Start continuous capture in background thread."""
        with self._lock:
            if self._status['running']:
                return {'error': 'Capture already running'}
            self._running = True
            self._status = {
                'running': True,
                'output_file': output_file,
                'start_time': None,
                'window_count': 0,
                'total_energy': 0.0,
                'last_window': None,
            }

        def run():
            self._capture_loop(window_duration, output_file, sampling_rate, max_windows)

        self._capture_thread = threading.Thread(target=run, daemon=True)
        self._capture_thread.start()
        return {'success': True, 'output_file': output_file}

    def stop_capture(self) -> dict:
        """Stop the capture loop."""
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=15)
            self._capture_thread = None
        with self._lock:
            self._status['running'] = False
        return {'success': True}
