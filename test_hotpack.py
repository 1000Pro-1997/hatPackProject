import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import hotpack
import math


class HotpackTests(unittest.TestCase):
    def test_remaining_time_format(self):
        self.assertEqual(hotpack.format_remaining(65), "01:05")
        self.assertEqual(hotpack.format_remaining(3661), "1:01:01")
        self.assertEqual(hotpack.format_remaining(math.inf), "무제한")

    def test_model_selection_leaves_headroom(self):
        self.assertEqual(hotpack.choose_model(8), "gemma3:1b")
        self.assertEqual(hotpack.choose_model(16), "gemma3:4b")
        self.assertEqual(hotpack.choose_model(32), "gemma3:12b")

    def test_workers_have_independent_compute_targets(self):
        cpu_payload = hotpack.Worker("gemma3:1b", 1.0, 0).request_payload()
        gpu_payload = hotpack.Worker("gemma3:1b", 1.0, hotpack.GPU_MAX_OFFLOAD_LAYERS).request_payload()
        self.assertEqual(cpu_payload["options"]["num_gpu"], 0)
        self.assertEqual(gpu_payload["options"]["num_gpu"], hotpack.GPU_MAX_OFFLOAD_LAYERS)

    def test_linux_millidegree_sensor(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "temp"
            path.write_text("52500\n")
            self.assertEqual(hotpack.LinuxSensor(path).read_celsius(), 52.5)

    def test_command_sensor_parses_degrees(self):
        sensor = hotpack.CommandSensor(["printf", "61.25 C\n"], "test")
        self.assertEqual(sensor.read_celsius(), 61.25)

    def test_command_sensor_rejects_fake_zero(self):
        sensor = hotpack.CommandSensor(["printf", "0.0 C\n"], "broken")
        with self.assertRaisesRegex(RuntimeError, "비정상 온도"):
            sensor.read_celsius()

    def test_macmon_uses_cpu_gpu_average(self):
        output = json.dumps({"temp": {"cpu_temp_avg": 54.2, "gpu_temp_avg": 48.1}})
        self.assertAlmostEqual(hotpack.MacmonSensor.parse(output), 51.15)
        self.assertEqual(hotpack.MacmonSensor.parse_temperatures(output), (54.2, 48.1))

    def test_macmon_ignores_unavailable_gpu_but_rejects_all_zero(self):
        output = json.dumps({"temp": {"cpu_temp_avg": 51.0, "gpu_temp_avg": 0.0}})
        self.assertEqual(hotpack.MacmonSensor.parse(output), 51.0)
        with self.assertRaisesRegex(RuntimeError, "비정상 온도"):
            hotpack.MacmonSensor.parse(json.dumps({"temp": {"cpu_temp_avg": 0, "gpu_temp_avg": 0}}))

    def test_live_config_only_accepts_safe_values(self):
        config = hotpack.Config(55, 70, 3, 3, 30)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            path.write_text(json.dumps({"target": 50}))
            hotpack.refresh_config(config, str(path))
            self.assertEqual((config.target, config.maximum), (50, 85))
            path.write_text(json.dumps({"target": 99}))
            hotpack.refresh_config(config, str(path))
            self.assertEqual((config.target, config.maximum), (50, 85))
            path.write_text(json.dumps({"target": 80, "maximum": 20}))
            hotpack.refresh_config(config, str(path))
            self.assertEqual((config.target, config.maximum), (80, 85))


if __name__ == "__main__":
    unittest.main()
