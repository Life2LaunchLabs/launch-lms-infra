import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LogRotationTest(unittest.TestCase):
    """Container logs hold request lines with IP addresses: they must roll over, not grow until the disk fills."""

    def test_every_production_service_caps_its_logs(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("x-logging: &default-logging", compose)
        self.assertRegex(compose, r"max-size: \d+m")
        self.assertRegex(compose, r'max-file: "\d+"')
        services = compose.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
        names = re.findall(r"^  ([a-z][\w-]*):\n", services, flags=re.M)
        self.assertIn("launch-lms", names)
        for name in names:
            block = services.split(f"\n  {name}:\n", 1)[1] if f"\n  {name}:\n" in services else services.split(f"  {name}:\n", 1)[1]
            block = re.split(r"\n  [a-z][\w-]*:\n", block, maxsplit=1)[0]
            self.assertIn("logging: *default-logging", block, name)


if __name__ == "__main__":
    unittest.main()
