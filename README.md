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
.venv/bin/python bench/bench_structs.py --out=results/cpython_structs.json --target=cpython-local
.venv/bin/python bench/bench_programs.py --out=results/cpython_programs.json --target=cpython-local
```

## MicroPython Unix

В работе использовался MicroPython Unix port v1.28.0. Если бинарник собран из исходников,
подставьте путь к нему вместо `micropython`.

```bash
micropython -X heapsize=64K bench/bench_structs.py --target=micropython-unix --heap-size=64K > results/micropython_unix_64k_structs.json
micropython -X heapsize=256K bench/bench_structs.py --target=micropython-unix --heap-size=256K > results/micropython_unix_256k_structs.json
micropython -X heapsize=256K bench/bench_programs.py --target=micropython-unix --heap-size=256K > results/micropython_unix_256k_programs.json
micropython -X heapsize=1M bench/bench_programs.py --target=micropython-unix --heap-size=1M > results/micropython_unix_1m_programs.json
```

## ESP8266

Выполненные аппаратные замеры сделаны на ESP8266EX 4MB с MicroPython v1.28.0
(`ESP8266_GENERIC`) на `/dev/ttyUSB0`.

Установить инструменты в локальное окружение:

```bash
.venv/bin/python -m pip install mpremote esptool
```

Проверить плату:

```bash
.venv/bin/mpremote connect /dev/ttyUSB0 exec "import sys, gc; print(sys.implementation); print(gc.mem_free())"
```

Запуск через `mpremote mount` добавляет служебную строку в stdout, поэтому JSON берётся
из первой строки raw-файла:

```bash
.venv/bin/mpremote connect /dev/ttyUSB0 mount . exec "import bench.bench_structs as b; b.main(['bench/bench_structs.py', '--target=esp8266'])" > /tmp/esp8266_structs_raw.txt
sed -n '1p' /tmp/esp8266_structs_raw.txt > results/esp8266_structs.json

.venv/bin/mpremote connect /dev/ttyUSB0 mount . exec "import bench.bench_programs as b; b.main(['bench/bench_programs.py', '--target=esp8266'])" > /tmp/esp8266_programs_raw.txt
sed -n '1p' /tmp/esp8266_programs_raw.txt > results/esp8266_programs.json
```

Для быстрой проверки:

```bash
.venv/bin/mpremote connect /dev/ttyUSB0 mount . exec "import bench.bench_structs as b; b.main(['bench/bench_structs.py', '--quick', '--target=esp8266'])" > /tmp/esp8266_structs_quick_raw.txt
sed -n '1p' /tmp/esp8266_structs_quick_raw.txt > results/esp8266_structs_quick.json

.venv/bin/mpremote connect /dev/ttyUSB0 mount . exec "import bench.bench_programs as b; b.main(['bench/bench_programs.py', '--quick', '--target=esp8266'])" > /tmp/esp8266_programs_quick_raw.txt
sed -n '1p' /tmp/esp8266_programs_quick_raw.txt > results/esp8266_programs_quick.json
```
