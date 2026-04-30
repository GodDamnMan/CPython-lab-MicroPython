from __future__ import print_function

import gc
import sys
import time

try:
    import ujson as json
except ImportError:
    import json

try:
    import array
except ImportError:
    array = None

try:
    import collections
except ImportError:
    collections = None


SIZES = (0, 1, 2, 4, 5, 8, 9, 16, 17, 23, 29, 64, 256, 1024)
TYPES = (
    "list",
    "tuple",
    "int",
    "str",
    "dict",
    "set",
    "frozenset",
    "array.array",
    "range",
    "collections.deque",
)


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
        "sizes": SIZES,
        "types": TYPES,
        "target": "host",
        "heap_size": None,
    }
    for arg in argv[1:]:
        if arg == "--quick":
            options["sizes"] = (0, 1, 4, 16, 64)
        elif arg.startswith("--out="):
            options["out"] = arg.split("=", 1)[1]
        elif arg.startswith("--sizes="):
            options["sizes"] = parse_list(arg.split("=", 1)[1], SIZES, int)
        elif arg.startswith("--types="):
            options["types"] = parse_list(arg.split("=", 1)[1], TYPES, str)
        elif arg.startswith("--target="):
            options["target"] = arg.split("=", 1)[1]
        elif arg.startswith("--heap-size="):
            options["heap_size"] = arg.split("=", 1)[1]
    return options


def build_object(type_name, n):
    if type_name == "list":
        return [i for i in range(n)]
    if type_name == "tuple":
        return tuple(range(n))
    if type_name == "int":
        return (1 << n) - 1 if n else 0
    if type_name == "str":
        return "x" * n
    if type_name == "dict":
        return dict((i, i) for i in range(n))
    if type_name == "set":
        return set(range(n))
    if type_name == "frozenset":
        return frozenset(range(n))
    if type_name == "array.array":
        if array is None:
            raise NotImplementedError("array module is unavailable")
        return array.array("h", range(n))
    if type_name == "range":
        return range(n)
    if type_name == "collections.deque":
        if collections is None or not hasattr(collections, "deque"):
            raise NotImplementedError("collections.deque is unavailable")
        return collections.deque(range(n), n)
    raise ValueError("unknown type: " + type_name)


def repetitions_for(n):
    if n <= 4:
        return 200
    if n <= 29:
        return 80
    if n <= 256:
        return 20
    return 5


def time_build(type_name, n, reps):
    gc.collect()
    start = clock_us()
    obj = None
    for _ in range(reps):
        obj = build_object(type_name, n)
    end = clock_us()
    obj = None
    gc.collect()
    return elapsed_us(start, end) // reps


def measure_micropython(type_name, n):
    gc.collect()
    free_before = gc.mem_free() if hasattr(gc, "mem_free") else None
    alloc_before = gc.mem_alloc() if hasattr(gc, "mem_alloc") else None
    start = clock_us()
    obj = build_object(type_name, n)
    end = clock_us()
    gc.collect()
    free_after = gc.mem_free() if hasattr(gc, "mem_free") else None
    alloc_after = gc.mem_alloc() if hasattr(gc, "mem_alloc") else None
    delta = None
    if free_before is not None and free_after is not None:
        delta = free_before - free_after
    elif alloc_before is not None and alloc_after is not None:
        delta = alloc_after - alloc_before
    shallow = sys.getsizeof(obj) if hasattr(sys, "getsizeof") else None
    obj = None
    gc.collect()
    return delta, free_before, free_after, elapsed_us(start, end), shallow, None


def measure_cpython(type_name, n):
    try:
        import tracemalloc
    except ImportError:
        tracemalloc = None

    gc.collect()
    if tracemalloc is None:
        start = clock_us()
        obj = build_object(type_name, n)
        end = clock_us()
        shallow = sys.getsizeof(obj) if hasattr(sys, "getsizeof") else None
        obj = None
        gc.collect()
        return shallow, None, None, elapsed_us(start, end), shallow, None

    tracemalloc.start()
    start = clock_us()
    obj = build_object(type_name, n)
    end = clock_us()
    current, peak = tracemalloc.get_traced_memory()
    shallow = sys.getsizeof(obj) if hasattr(sys, "getsizeof") else None
    tracemalloc.stop()
    obj = None
    gc.collect()
    return current, None, None, elapsed_us(start, end), shallow, peak


def measure_case(type_name, n, options):
    row = {
        "impl": getattr(sys.implementation, "name", "python"),
        "target": options["target"],
        "version": implementation_version(),
        "heap_size": options["heap_size"],
        "case": "build",
        "type": type_name,
        "n": n,
        "memory_delta": None,
        "free_before": None,
        "free_after": None,
        "time_us": None,
        "status": "ok",
    }

    try:
        if is_micropython():
            delta, free_before, free_after, build_us, shallow, peak = measure_micropython(type_name, n)
        else:
            delta, free_before, free_after, build_us, shallow, peak = measure_cpython(type_name, n)
        reps = repetitions_for(n)
        row["time_us"] = time_build(type_name, n, reps)
        row["build_once_us"] = build_us
        row["memory_delta"] = delta
        row["free_before"] = free_before
        row["free_after"] = free_after
        row["shallow_size"] = shallow
        row["peak_memory"] = peak
        row["reps"] = reps
    except MemoryError:
        row["status"] = "memory_error"
    except Exception as exc:
        row["status"] = "error"
        row["error"] = str(exc)
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
    for type_name in options["types"]:
        for n in options["sizes"]:
            rows.append(measure_case(type_name, n, options))
    write_json(rows, options["out"])


if __name__ == "__main__":
    main(sys.argv)
