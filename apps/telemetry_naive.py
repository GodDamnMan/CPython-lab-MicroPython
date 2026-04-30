class TelemetryProcessor:
    def __init__(self):
        self.readings = []
        self.raw_log = ""

    def process_line(self, line):
        if isinstance(line, bytes):
            line = line.decode()
        line = line.strip()
        parts = line.split(",")
        record = {
            "ts": int(parts[0]),
            "temp": float(parts[1]),
            "hum": float(parts[2]),
            "status": parts[3],
        }
        self.readings.append(record)
        self.raw_log = self.raw_log + line + "\n"
        return record

    def summary(self):
        count = len(self.readings)
        if count == 0:
            return {
                "count": 0,
                "avg_temp": 0.0,
                "min_temp": 0.0,
                "max_temp": 0.0,
                "avg_hum": 0.0,
            }

        temps = [row["temp"] for row in self.readings]
        hums = [row["hum"] for row in self.readings]
        return {
            "count": count,
            "avg_temp": sum(temps) / count,
            "min_temp": min(temps),
            "max_temp": max(temps),
            "avg_hum": sum(hums) / count,
        }


def process_stream(lines):
    processor = TelemetryProcessor()
    for line in lines:
        processor.process_line(line)
    return processor.summary()
