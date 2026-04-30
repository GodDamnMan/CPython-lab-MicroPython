# Лабораторная: Управление памятью в MicroPython

Проект содержит отчёт, бенчмарки и пример оптимизации программы под MicroPython.

## Структура

- `report.md` - основной отчёт.
- `bench/bench_structs.py` - измерение памяти и времени для базовых структур.
- `bench/bench_programs.py` - сравнение наивной и оптимизированной telemetry-программы.
- `apps/telemetry_naive.py` - версия с `list`, `dict`, `float` и временными объектами.
- `apps/telemetry_optimized.py` - версия с fixed-point `int`, `array.array` и кольцевым буфером.
- `results/` - JSON-результаты запусков.

## CPython

```bash
python3 bench/bench_structs.py --out=results/cpython_structs.json --target=cpython-local
python3 bench/bench_programs.py --out=results/cpython_programs.json --target=cpython-local
```

## MicroPython Unix

```bash
micropython -X heapsize=64K bench/bench_structs.py --target=micropython-unix --heap-size=64K > results/micropython_unix_64k_structs.json
micropython -X heapsize=256K bench/bench_structs.py --target=micropython-unix --heap-size=256K > results/micropython_unix_256k_structs.json
micropython -X heapsize=1M bench/bench_programs.py --target=micropython-unix --heap-size=1M > results/micropython_unix_1m_programs.json
```

## ESP32

Установить `mpremote`, подключить плату с MicroPython и запускать из корня проекта:

```bash
mpremote connect auto mount . exec "import bench.bench_structs as b; b.main(['bench/bench_structs.py', '--target=esp32'])" > results/esp32_structs.json
mpremote connect auto mount . exec "import bench.bench_programs as b; b.main(['bench/bench_programs.py', '--target=esp32'])" > results/esp32_programs.json
```

Если на плате мало памяти, используйте `--quick`:

```bash
mpremote connect auto mount . exec "import bench.bench_structs as b; b.main(['bench/bench_structs.py', '--quick', '--target=esp32'])" > results/esp32_structs_quick.json
```
