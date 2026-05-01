# Анализ управления памятью в MicroPython

## Цель работы

Цель лабораторной работы - понять, как MicroPython экономит память на микроконтроллерах, чем его модель исполнения отличается от CPython, как устроены основные встроенные структуры данных на уровне C и как эти знания помогают переписать программу под ограниченный heap.

Практическая часть подготовлена для трёх запусков:

- CPython 3.12.3 на ПК.
- MicroPython Unix port с ограничением heap через `-X heapsize`.
- MicroPython на ESP8266EX 4MB через `mpremote`.

Фактически выполнены все три набора: CPython 3.12.3, MicroPython Unix port v1.28.0
и плата ESP8266EX 4MB с MicroPython v1.28.0 (`ESP8266_GENERIC`) на `/dev/ttyUSB0`.

## Теория

### Модель памяти MicroPython

MicroPython проектировался для плат с десятками или сотнями килобайт RAM. Поэтому он не копирует внутреннюю модель CPython один к одному. Главная идея: большая часть Python-объектов представляется через машинное слово `mp_obj_t`. Если значение маленькое, оно может храниться прямо в этом слове: например small int, `qstr` или специальные immediate-значения. Если объект сложный, `mp_obj_t` содержит tagged pointer на объект в heap.

Python heap MicroPython управляется собственным GC. Heap разбит на блоки по 4 машинных слова. На 32-битной плате это обычно 16 байт на блок, на 64-битной машине - 32 байта. Для каждого блока GC хранит состояние в bitmap: свободен, head объекта, tail объекта или marked. Такой формат даёт маленькие накладные расходы и позволяет работать без `malloc` для каждого Python-объекта.

Строковые идентификаторы часто переводятся в `qstr`: interned string с числовым идентификатором. Это экономит память на именах атрибутов, ключах словарей модулей и повторяющихся строках, а также ускоряет сравнение, потому что `qstr` можно сравнить как значение.

### Heap и stack

На микроконтроллере heap обычно фиксирован при старте прошивки. Если heap кончился, MicroPython делает сборку мусора и после этого либо продолжает работу, либо выбрасывает `MemoryError`. В Unix-порте можно имитировать маленькую плату:

```bash
micropython -X heapsize=64K script.py
```

Stack остаётся C-stack интерпретатора и пользовательского кода. MicroPython сканирует stack как часть root set для GC, поэтому живые ссылки из стека не удаляются. Глубокая рекурсия опасна не только из-за времени, но и из-за расхода C-stack. На микроконтроллерах это особенно заметно.

### Garbage Collector: MicroPython против CPython

CPython использует reference counting как основной механизм освобождения памяти. Когда счётчик ссылок объекта падает до нуля, объект обычно освобождается сразу. Дополнительно CPython имеет cyclic GC для контейнеров, которые могут образовать циклы ссылок.

MicroPython использует mark-and-sweep GC для Python heap. Сборщик находит корни из регистров, stack, глобальных структур VM и помечает достижимые объекты. После маркировки непомеченные blocks возвращаются в свободный список. Это проще для embedded-среды и дешевле по metadata, но освобождение памяти менее немедленное, чем reference counting в CPython.

Практическое отличие:

- В CPython временный объект часто исчезает сразу после потери последней ссылки.
- В MicroPython память временных объектов может оставаться занятой до следующего `gc.collect()`.
- В MicroPython важнее избегать пиковых временных аллокаций, потому что даже короткий пик может привести к `MemoryError`.

### Техники снижения расхода памяти

- Использовать `const()` для констант, чтобы MicroPython мог оптимизировать обращения.
- Выносить неизменяемый код и данные во frozen modules/bytecode, чтобы они жили во flash, а не в RAM.
- Предвыделять буферы: `bytearray`, `array.array`, fixed-size ring buffer.
- Использовать `memoryview` для slices без копирования.
- Использовать `array.array` вместо `list[int]`, если элементы однотипные и помещаются в C-тип.
- Использовать `range` вместо `list(range(...))`, когда нужен только перебор.
- Использовать `collections.deque(iterable, maxlen)` как ограниченный кольцевой буфер.
- Хранить физические величины fixed-point целыми числами, например температуру в сотых долях градуса.
- Избегать конкатенации строк в цикле.
- Не создавать `dict` для каждой записи, если поля фиксированы.
- В горячем коде кешировать часто используемые функции/методы в локальные переменные.
- Контролировать `gc.collect()` и при необходимости `gc.threshold()`.

## C-структуры данных MicroPython

Ниже приведён разбор по исходникам MicroPython `py/obj*.c`, `py/obj*.h`, `py/map.c`.

| Тип | Основная структура | Аллокация и рост | Вывод |
| --- | --- | --- | --- |
| `list` | `mp_obj_list_t { base, alloc, len, items }` | объект списка и отдельный массив `items`; минимум 4 slots; `append` растит `alloc * 2`; `pop` сжимает, если `alloc > 2 * len` | Хорош для изменяемой последовательности, но держит запас указателей |
| `tuple` | `mp_obj_tuple_t { base, len, items[] }` | один var-object с inline массивом; empty tuple singleton | Экономичнее list для неизменяемых данных |
| `int` | small int прямо в `mp_obj_t`; big int зависит от `MICROPY_LONGINT_IMPL` | small int не требует heap-объекта; большие числа выделяются отдельно или запрещены конфигурацией | Малые целые особенно дешёвые |
| `str` | `mp_obj_str_t { base, hash, len, data }` | объект строки плюс буфер `len + 1`; часть строк может быть `qstr` | Повторы идентификаторов дешевле через interning |
| `dict` | `mp_obj_dict_t { base, mp_map_t }` | open addressing; `mp_map_elem_t { key, value }`; таблица размеров `0,2,4,6,8,10,12,17,23,...` | Быстрый, но дорогой по памяти на маленькой плате |
| `set` | `mp_obj_set_t { base, mp_set_t }` | `mp_set_t { alloc, used, table }`; open addressing | Похож на dict без values |
| `frozenset` | тот же storage, другой type | создаётся как set-таблица, затем неизменяемый API | Экономии относительно set почти нет, но есть hashability |
| `array.array` | `mp_obj_array_t { base, typecode, free, len, items }` | typed contiguous buffer; `append` добавляет запас 8 элементов | Лучше list для числовых массивов |
| `range` | `mp_obj_range_t { base, start, stop, step }` | фиксированный объект O(1), элементы не материализуются | Самый дешёвый способ задать арифметическую последовательность |
| `collections.deque` | `mp_obj_deque_t { base, alloc, i_get, i_put, items, flags }` | bounded circular buffer; `alloc = maxlen + 1`; без динамического роста | Хорош для окон последних N значений |

Важный общий момент: контейнеры MicroPython обычно хранят массивы `mp_obj_t`, то есть массив ссылок/значений объектов. Если элементы являются small int, они лежат прямо в этих slots. Если элементы сложные, slots указывают на отдельные heap-объекты.

## Методика измерений

Скрипт `bench/bench_structs.py` создаёт объекты размеров:

```text
0, 1, 2, 4, 5, 8, 9, 16, 17, 23, 29, 64, 256, 1024
```

Единый JSON-формат результата:

```json
{
  "impl": "cpython",
  "target": "cpython-local",
  "version": "cpython 3.12.3",
  "heap_size": null,
  "case": "build",
  "type": "list",
  "n": 1024,
  "memory_delta": 33560,
  "free_before": null,
  "free_after": null,
  "time_us": 53,
  "status": "ok"
}
```

Для CPython используется `tracemalloc`: `memory_delta` показывает текущий traced memory после построения объекта, `peak_memory` показывает пик с временными аллокациями, `shallow_size` показывает `sys.getsizeof(obj)`.

Для MicroPython используется `gc.collect()`, затем `gc.mem_free()`/`gc.mem_alloc()`. В этом режиме `memory_delta = free_before - free_after`, то есть приблизительная цена живого объекта в Python heap.

JSON пишется потоково: скрипты не накапливают весь список результатов в памяти, а
сразу сериализуют строки массива. Это важно для ESP8266, где даже список из всех
измерений может сам вызвать `MemoryError`. В telemetry-бенчмарке входные строки
также подаются как поток, чтобы измерять обработчик, а не заранее созданный
буфер входных данных.

Команды запуска:

```bash
.venv/bin/python bench/bench_structs.py --out=results/cpython_structs.json --target=cpython-local
.venv/bin/python bench/bench_programs.py --out=results/cpython_programs.json --target=cpython-local
```

Для Unix-порта MicroPython:

```bash
micropython -X heapsize=64K bench/bench_structs.py --target=micropython-unix --heap-size=64K > results/micropython_unix_64k_structs.json
micropython -X heapsize=256K bench/bench_structs.py --target=micropython-unix --heap-size=256K > results/micropython_unix_256k_structs.json
micropython -X heapsize=256K bench/bench_programs.py --target=micropython-unix --heap-size=256K > results/micropython_unix_256k_programs.json
micropython -X heapsize=1M bench/bench_programs.py --target=micropython-unix --heap-size=1M > results/micropython_unix_1m_programs.json
```

Для ESP8266:

```bash
mpremote connect /dev/ttyUSB0 mount . exec "import bench.bench_structs as b; b.main(['bench/bench_structs.py', '--target=esp8266'])" > /tmp/esp8266_structs_raw.txt
sed -n '1p' /tmp/esp8266_structs_raw.txt > results/esp8266_structs.json

mpremote connect /dev/ttyUSB0 mount . exec "import bench.bench_programs as b; b.main(['bench/bench_programs.py', '--target=esp8266'])" > /tmp/esp8266_programs_raw.txt
sed -n '1p' /tmp/esp8266_programs_raw.txt > results/esp8266_programs.json
```

## CPython baseline

Локальный baseline получен на CPython 3.12.3. Эти числа не являются оценкой MicroPython heap, но хорошо показывают относительные свойства структур и дают точку сравнения.

### Структуры, n = 1024

| Тип | `shallow_size`, bytes | `peak_memory`, bytes | `time_us` |
| --- | ---: | ---: | ---: |
| `list` | 8856 | 33568 | 69 |
| `tuple` | 8232 | 33024 | 52 |
| `int` | 164 | 460 | 2 |
| `str` | 1065 | 1225 | 2 |
| `dict` | 36952 | 69616 | 116 |
| `set` | 32984 | 57776 | 34 |
| `frozenset` | 32984 | 57776 | 32 |
| `array.array('h')` | 2140 | 2476 | 140 |
| `range` | 48 | 240 | 2 |
| `collections.deque` | 9208 | 34056 | 21 |

Главные наблюдения:

- `range` почти не зависит от `n`, потому что хранит только `start`, `stop`, `step`.
- `array.array('h')` намного компактнее `list[int]`, потому что хранит C-массив 16-битных чисел, а не массив Python-объектов.
- `dict`, `set`, `frozenset` дают высокий overhead из-за hash-table.
- `list` и `tuple` близки по shallow size, но tuple не держит запас под рост.

## Оптимизация программы

Практический пример - обработка телеметрии:

```text
timestamp,temp,humidity,status
```

Наивная версия `apps/telemetry_naive.py` делает типичные для CPython, но неудачные для MicroPython вещи:

- хранит каждую запись как `dict`;
- использует `float`;
- держит неограниченный `list`;
- конкатенирует строковый лог в цикле;
- в `summary()` создаёт временные списки `temps` и `hums`.

Оптимизированная версия `apps/telemetry_optimized.py`:

- хранит температуру и влажность fixed-point в сотых долях;
- использует `array.array('h')`;
- держит только последние `capacity` измерений;
- использует кольцевой буфер;
- парсит bytes без `split()`;
- не создаёт `dict` в горячем цикле;
- использует `const()` там, где он доступен.

### Результаты CPython для программы

| Программа | N входов | Live/traced bytes | Peak bytes | Time, us | Примечание |
| --- | ---: | ---: | ---: | ---: | --- |
| naive | 64 | 20247 | 22555 | 1351 | хранит все 64 записи |
| optimized | 64 | 1752 | 2304 | 3081 | хранит 64 записи в preallocated arrays |
| naive | 256 | 78283 | 87816 | 6582 | footprint растёт вместе с числом записей |
| optimized | 256 | 1744 | 2296 | 22498 | хранит окно 128, 128 overwrites |
| naive | 1024 | 332251 | 371021 | 31186 | footprint и peak резко растут |
| optimized | 1024 | 1768 | 2288 | 108806 | память стабильна, 896 overwrites |

На CPython оптимизированная версия медленнее, потому что ручной Python-парсер проигрывает C-реализациям `split()` и `float()`. Но цель этой версии - не ускорить CPython, а убрать рост памяти и временные аллокации. Для MicroPython это обычно важнее: программа, которая чуть медленнее, но не падает с `MemoryError`, лучше подходит для микроконтроллера.

## Результаты MicroPython Unix

Unix-port v1.28.0 был собран из исходников MicroPython и запущен с ограничением
heap. Это удобная промежуточная проверка: поведение ближе к MicroPython, но
без аппаратных ограничений serial-запуска.

### Структуры, heap 64K, n = 1024

| Тип | `memory_delta`, bytes | `time_us` | Статус |
| --- | ---: | ---: | --- |
| `list` | 8224 | 86 | ok |
| `tuple` | 8224 | 77 | ok |
| `int` | 192 | 2 | ok |
| `str` | 1088 | 18 | ok |
| `dict` | MemoryError | - | memory_error |
| `set` | MemoryError | - | memory_error |
| `frozenset` | MemoryError | - | memory_error |
| `array.array('h')` | 2080 | 84 | ok |
| `range` | 32 | 0 | ok |
| `collections.deque` | 8288 | 47 | ok |

При 64K heap крупные hash-table структуры не помещаются уже на `n = 1024`.
`range` остаётся практически постоянным, а `array.array('h')` занимает примерно
в четыре раза меньше памяти, чем `list`/`tuple` с тем же количеством small int.

### Программа, heap 256K

| Программа | N входов | `memory_delta`, bytes | Time, us | Статус | Примечание |
| --- | ---: | ---: | ---: | --- | --- |
| naive | 64 | 16384 | 1316 | ok | count=64 |
| optimized | 64 | 992 | 864 | ok | count=64, overwrites=0 |
| naive | 256 | MemoryError | - | memory_error | - |
| optimized | 256 | 992 | 9307 | ok | count=128, overwrites=128 |
| naive | 1024 | MemoryError | - | memory_error | - |
| optimized | 1024 | 992 | 26790 | ok | count=128, overwrites=896 |

### Программа, heap 1M

| Программа | N входов | `memory_delta`, bytes | Time, us | Статус | Примечание |
| --- | ---: | ---: | ---: | --- | --- |
| naive | 64 | 16384 | 1568 | ok | count=64 |
| optimized | 64 | 992 | 1639 | ok | count=64, overwrites=0 |
| naive | 256 | 64544 | 12871 | ok | count=256 |
| optimized | 256 | 992 | 8108 | ok | count=128, overwrites=128 |
| naive | 1024 | MemoryError | - | memory_error | - |
| optimized | 1024 | 992 | 43152 | ok | count=128, overwrites=896 |

Даже при 1M heap наивная версия не проходит 1024 строки, потому что хранит все
записи, float-значения, словари и строковый лог. Оптимизированная версия
сохраняет постоянный footprint и ограничивает историю последними 128 измерениями.

## Результаты ESP8266

Плата: ESP8266EX, 4MB flash, MicroPython v1.28.0 (`ESP8266_GENERIC`), порт
`/dev/ttyUSB0`. После загрузки было доступно около 36K heap; во время запуска
через `mpremote mount` свободный heap перед отдельными измерениями был около
22-26K.

### Структуры, n = 1024

| Тип | `memory_delta`, bytes | `time_us` | Статус |
| --- | ---: | ---: | --- |
| `list` | 4112 | 13965 | ok |
| `tuple` | MemoryError | - | memory_error |
| `int` | 160 | 283 | ok |
| `str` | 1056 | 1376 | ok |
| `dict` | MemoryError | - | memory_error |
| `set` | MemoryError | - | memory_error |
| `frozenset` | MemoryError | - | memory_error |
| `array.array('h')` | 2064 | 5010 | ok |
| `range` | 16 | 280 | ok |
| `collections.deque` | 4144 | 4969 | ok |

ESP8266 сильнее подчёркивает разницу между структурами: `array.array('h')`
помещается для 1024 элементов, `range` практически бесплатен, а крупные
`dict`/`set`/`frozenset` падают с `MemoryError`.

### Программа

| Программа | N входов | `memory_delta`, bytes | Time, us | Статус | Примечание |
| --- | ---: | ---: | ---: | --- | --- |
| naive | 64 | 6704 | 239480 | ok | count=64 |
| optimized | 64 | 768 | 189644 | ok | count=64, overwrites=0 |
| naive | 256 | MemoryError | - | memory_error | - |
| optimized | 256 | 768 | 1118375 | ok | count=128, overwrites=128 |
| naive | 1024 | MemoryError | - | memory_error | - |
| optimized | 1024 | 768 | 5317740 | ok | count=128, overwrites=896 |

На реальной плате optimized-версия даёт тот результат, ради которого она была
написана: рост числа входных строк влияет на время, но почти не влияет на
память. Наивная версия уже на 256 строках не помещается в heap ESP8266.

## Сценарии для проверки на MicroPython

1. OOM при малом heap:

```bash
micropython -X heapsize=64K bench/bench_structs.py --target=micropython-unix --heap-size=64K
```

Полученный результат: крупные `dict`, `set` и `frozenset` на `n = 1024` получают `memory_error`, а `range` и `array.array` переживают ограничение лучше.

2. Фрагментация и временные объекты:

```bash
micropython -X heapsize=256K bench/bench_programs.py --target=micropython-unix --heap-size=256K
```

Полученный результат: naive-версия имеет больший `memory_delta` и падает на 256/1024 строках при 256K heap, optimized-версия проходит все размеры с почти постоянной памятью.

3. ESP8266:

```bash
mpremote connect /dev/ttyUSB0 mount . exec "import bench.bench_programs as b; b.main(['bench/bench_programs.py', '--target=esp8266'])"
```

Полученный результат: полный запуск помещается для бенчмарка целиком, но отдельные
cases возвращают `memory_error`: naive-программа падает на 256 и 1024 строках,
optimized-программа проходит все размеры.

## Выводы

MicroPython экономит память не одной техникой, а набором решений: compact object representation, tagged `mp_obj_t`, qstr interning, простой block-based heap и mark-and-sweep GC. Эти решения уменьшают накладные расходы, но не отменяют главного ограничения embedded-среды: heap конечен, а временные аллокации опасны.

При выборе структур данных:

- Для последовательности фиксированного размера лучше `tuple` или `array.array`, а не растущий `list`.
- Для числового потока лучше `array.array`/`bytearray` плюс индекс, чем список Python-чисел.
- Для окна последних N значений лучше bounded `deque` или ring buffer.
- Для перебора диапазона лучше `range`, потому что он O(1) по памяти.
- `dict` и `set` удобны, но дороги, поэтому на MicroPython их стоит оставлять для случаев, где hash lookup действительно нужен.

Оптимизация telemetry-программы показывает главный practical lesson: под MicroPython нужно проектировать объём памяти заранее. Fixed-size buffers, fixed-point integers и отсутствие временных контейнеров дают стабильный memory footprint, что важнее красивого CPython-стиля на микроконтроллере.

## Источники

- MicroPython Memory Management: https://docs.micropython.org/en/latest/develop/memorymgt.html
- MicroPython constrained devices: https://docs.micropython.org/en/latest/reference/constrained.html
- MicroPython speed guide: https://docs.micropython.org/en/latest/reference/speed_python.html
- MicroPython Unix quick reference: https://docs.micropython.org/en/latest/unix/quickref.html
- MicroPython ESP8266 tutorial: https://docs.micropython.org/en/latest/esp8266/tutorial/intro.html
- MicroPython `mpremote`: https://docs.micropython.org/en/latest/reference/mpremote.html
- CPython `gc`: https://docs.python.org/3.14/library/gc.html
- MicroPython source: https://github.com/micropython/micropython
