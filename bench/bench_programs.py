from __future__ import print_function

import gc
import sys
import time

try:
    import ujson as json
except ImportError:
    import json


COUNTS = (64, 256, 1024)
CAPACITY = 128


def add_import_paths():
    for path in (".", ".."):
        if path not in sys.path:
            sys.path.append(path)


add_import_paths()

try:
    from apps import telemetry_naive
    from apps import telemetry_optimized
except ImportError:
    import telemetry_naive
    import telemetry_optimized


def is_micropython():
    return getattr(sys.implementation, "name", "") == "micropython"


def implementation_version():
    impl = getattr(sys, "implementation", None)
    name = getattr(impl, "name", "python")
    version = getattr(impl, "version", None)
    if version is not None:
        return "%s %s.%s.%s" % (name, version[0], version[1], version[2])
    return sys.version.split()[0]


def clock_us():
    if hasattr(time, "ticks_us"):
        return time.ticks_us()
    return int(time.perf_counter() * 1000000)


def elapsed_us(start, end):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(end, start)
    return end - start


def parse_list(value, default, cast):
    if value is None or value == "":
        return default
    out = []
    for part in value.split(","):
        part = part.strip()
        if part:
            out.append(cast(part))
    return tuple(out)


def parse_args(argv):
    options = {
        "out": None,
        "counts": COUNTS,
        "capacity": CAPACITY,
        "target": "host",
        "heap_size": None,
    }
    for arg in argv[1:]:
        if arg == "--quick":
            options["counts"] = (32, 128)
        elif arg.startswith("--out="):
            options["out"] = arg.split("=", 1)[1]
        elif arg.startswith("--counts="):
            options["counts"] = parse_list(arg.split("=", 1)[1], COUNTS, int)
        elif arg.startswith("--capacity="):
            options["capacity"] = int(arg.split("=", 1)[1])
        elif arg.startswith("--target="):
            options["target"] = arg.split("=", 1)[1]
        elif arg.startswith("--heap-size="):
            options["heap_size"] = arg.split("=", 1)[1]
    return options


def make_line(index, as_bytes):
    temp = 2000 + (index * 37) % 1500
    hum = 4500 + (index * 19) % 3000
    line = "%d,%d.%02d,%d.%02d,OK" % (
        index,
        temp // 100,
        temp % 100,
        hum // 100,
        hum % 100,
    )
    if as_bytes:
        return line.encode()
    return line


def make_lines(count, as_bytes):
    return [make_line(index, as_bytes) for index in range(count)]


def run_program(case_name, lines, capacity):
    if case_name == "telemetry_naive":
        processor = telemetry_naive.TelemetryProcessor()
    else:
        processor = telemetry_optimized.TelemetryProcessor(capacity)
    for line in lines:
        processor.process_line(line)
    return processor.summary()


def measure_micropython(case_name, lines, capacity):
    gc.collect()
    free_before = gc.mem_free() if hasattr(gc, "mem_free") else None
    alloc_before = gc.mem_alloc() if hasattr(gc, "mem_alloc") else None
    start = clock_us()
    summary = run_program(case_name, lines, capacity)
    end = clock_us()
    gc.collect()
    free_after = gc.mem_free() if hasattr(gc, "mem_free") else None
    alloc_after = gc.mem_alloc() if hasattr(gc, "mem_alloc") else None
    delta = None
    if free_before is not None and free_after is not None:
        delta = free_before - free_after
    elif alloc_before is not None and alloc_after is not None:
        delta = alloc_after - alloc_before
    return summary, delta, free_before, free_after, elapsed_us(start, end), None


def measure_cpython(case_name, lines, capacity):
    try:
        import tracemalloc
    except ImportError:
        tracemalloc = None

    gc.collect()
    if tracemalloc is None:
        start = clock_us()
        summary = run_program(case_name, lines, capacity)
        end = clock_us()
        return summary, None, None, None, elapsed_us(start, end), None

    tracemalloc.start()
    start = clock_us()
    summary = run_program(case_name, lines, capacity)
    end = clock_us()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return summary, current, None, None, elapsed_us(start, end), peak


def measure_case(case_name, count, options):
    lines = make_lines(count, case_name == "telemetry_optimized")
    row = {
        "impl": getattr(sys.implementation, "name", "python"),
        "target": options["target"],
        "version": implementation_version(),
        "heap_size": options["heap_size"],
        "case": case_name,
        "type": "program",
        "n": count,
        "memory_delta": None,
        "free_before": None,
        "free_after": None,
        "time_us": None,
        "status": "ok",
    }
    try:
        if is_micropython():
            summary, delta, free_before, free_after, time_us, peak = measure_micropython(
                case_name, lines, options["capacity"]
            )
        else:
            summary, delta, free_before, free_after, time_us, peak = measure_cpython(
                case_name, lines, options["capacity"]
            )
        row["summary"] = summary
        row["memory_delta"] = delta
        row["free_before"] = free_before
        row["free_after"] = free_after
        row["time_us"] = time_us
        row["peak_memory"] = peak
        row["capacity"] = options["capacity"]
    except MemoryError:
        row["status"] = "memory_error"
    except Exception as exc:
        row["status"] = "error"
        row["error"] = str(exc)
    lines = None
    gc.collect()
    return row


def write_json(rows, path):
    if path:
        with open(path, "w") as handle:
            json.dump(rows, handle)
            handle.write("\n")
    else:
        print(json.dumps(rows))


def main(argv):
    options = parse_args(argv)
    rows = []
    for count in options["counts"]:
        rows.append(measure_case("telemetry_naive", count, options))
        rows.append(measure_case("telemetry_optimized", count, options))
    write_json(rows, options["out"])


if __name__ == "__main__":
    main(sys.argv)
