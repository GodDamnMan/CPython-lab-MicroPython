# Анализ управления памятью в MicroPython

## Цель работы

Цель лабораторной работы - понять, как MicroPython экономит память на
микроконтроллерах, чем его модель исполнения отличается от CPython, как устроены
основные встроенные структуры данных на уровне C и как эти знания помогают
переписать программу под ограниченный heap.

Практическая часть выполнена для трёх Python-сценариев:

- CPython 3.12.3 на ПК.
- MicroPython Unix port v1.28.0 с ограничением heap через `-X heapsize`.
- MicroPython v1.28.0 (`ESP8266_GENERIC`) на ESP8266EX 4MB через `mpremote`.

После обратной связи добавлен минимальный C-бейзлайн
`apps/telemetry_c_baseline.c`. Он нужен не как полноценная третья система
бенчмарков, а как нижняя точка сравнения: сколько памяти занимает та же идея
обработки телеметрии без Python-объектов вообще.

## Зачем MicroPython

MicroPython проектировался для плат, где RAM измеряется десятками или сотнями
килобайт, а flash заменяет привычный диск. На ESP8266 в этой работе после старта
прошивки было доступно около 36K heap, а при запуске через `mpremote mount` перед
отдельными измерениями оставалось около 22-26K. В таком окружении важен не только
средний расход памяти, но и короткие пики: временный список, словарь или строка
могут привести к `MemoryError`, даже если после следующей сборки мусора часть
памяти освободилась бы.

Отличие от CPython не в том, что MicroPython просто "урезанный Python".
MicroPython иначе представляет объекты, иначе управляет heap и сильнее поощряет
код, где размер данных известен заранее: фиксированные буферы, `array.array`,
`bytearray`, `range`, `memoryview`, frozen modules и отсутствие лишних временных
контейнеров.

## Модель объектов: CPython vs MicroPython

В CPython каждый обычный объект начинается с заголовка `PyObject`: счётчик ссылок
`ob_refcnt` и указатель на тип `ob_type`. Объекты переменного размера используют
`PyVarObject`, где добавляется `ob_size`. Даже маленькое целое число является
`PyLongObject`: некоторые small integers кешируются и переиспользуются, но это
всё равно полноценные объекты с заголовком и refcount.

В MicroPython все Python-значения передаются через `mp_obj_t`. Обычно это одно
машинное слово. Часть значения используется как tag:

- small int хранится прямо в `mp_obj_t`, без heap-объекта;
- interned string, то есть `qstr`, тоже может быть закодирован прямо как tagged
  value;
- `None`, `False`, `True` и некоторые специальные значения являются immediate;
- сложный объект представлен tagged pointer на структуру в GC heap.

У concrete-объекта MicroPython в heap обычно есть только `mp_obj_base_t base`, где
лежит указатель на тип. Это компактнее, чем `PyObject_HEAD` в CPython, потому что
нет per-object reference count. Цена такого решения: освобождение памяти не
моментальное, а через tracing GC.

Пример для списка:

```c
/* MicroPython */
typedef struct _mp_obj_list_t {
    mp_obj_base_t base;
    size_t alloc;
    size_t len;
    mp_obj_t *items;
} mp_obj_list_t;

/* CPython */
typedef struct {
    PyObject_VAR_HEAD
    PyObject **ob_item;
    Py_ssize_t allocated;
} PyListObject;
```

В обоих случаях контейнер хранит массив элементов, но элементы разные:
`PyObject*` в CPython и `mp_obj_t` в MicroPython. Поэтому список small int в
MicroPython может хранить числа прямо в slots, а список CPython хранит указатели
на отдельные `PyLongObject`.

## Управление памятью и GC

### CPython: reference counting и cyclic GC

Основной механизм CPython - reference counting. Когда объект получает новую
ссылку, выполняется `Py_INCREF`; когда ссылка исчезает, выполняется `Py_DECREF`.
Если `ob_refcnt` становится равен нулю, вызывается deallocator типа объекта:
например, список уменьшает refcount каждого элемента, освобождает массив
`ob_item`, затем освобождает сам `PyListObject` через Python allocator.

Это даёт важное свойство: многие временные объекты освобождаются сразу, без
ожидания отдельной фазы сборки мусора. Но reference counting не решает циклы:

```python
a = []
a.append(a)
del a
```

После `del a` внешний refcount исчез, но список всё ещё ссылается сам на себя.
Для таких случаев в CPython есть cyclic GC. Он отслеживает контейнеры, которые
могут участвовать в циклах, обходит граф ссылок через C-level `tp_traverse`,
находит недостижимые циклы и разрывает их через `tp_clear`. Поэтому фраза
"CPython использует только reference counting" неточна: RC основной и
немедленный механизм, а cyclic GC - дополнительный механизм для циклов.

### MicroPython: mark-and-sweep GC

MicroPython использует собственный Python heap. Heap разбит на блоки по 4
машинных слова. На 32-битной плате это обычно 16 bytes на блок, на 64-битной
машине - 32 bytes. Для каждого блока в allocation bitmap хранится состояние:

- `FREE` - свободный блок;
- `HEAD` - первый блок выделенного объекта;
- `TAIL` - продолжение объекта;
- `MARK` - live head block, найденный на фазе mark.

Алгоритм:

1. **Root set.** GC начинает с корней: стеки Python runtime и Python threads,
   root pointers, зарегистрированные из C через `MP_REGISTER_ROOT_POINTER`, и
   внутренние структуры VM.
2. **Mark phase.** Если значение похоже на указатель на head block в Python heap,
   GC переводит `HEAD` в `MARK` и сканирует содержимое объекта как набор
   возможных ссылок на другие heap-объекты.
3. **Sweep phase.** После обхода heap просматривается целиком. Непомеченные
   `HEAD` и их `TAIL` переводятся в `FREE`. Помеченные `MARK` переводятся обратно
   в `HEAD`.

Важно: GC не просто "помечает участки памяти для будущей деаллокации". В фазе
sweep он сам возвращает непомеченные блоки во внутренний свободный пул
MicroPython heap. На микроконтроллере это обычно не означает возврат RAM
операционной системе: память остаётся частью фиксированного Python heap, но
становится доступной для следующих `m_new`, `m_malloc`, `gc_alloc` и создания
новых Python-объектов.

Из C-кода MicroPython возможны явные `m_free`/`gc_free` при изменении размера
буферов или освобождении вспомогательной памяти. Но для обычного Python-кода
основная история такая: объект становится недостижимым, следующая сборка мусора
находит его как unmarked и переводит его блоки в `FREE`.

### Что легче: RC или GC

Утверждение "reference counting легче по вычислительной мощности, чем GC" верно
только частично.

Reference counting часто дешевле по задержке освобождения простого объекта:
последняя ссылка исчезла, объект сразу деаллоцирован, полного обхода heap не
нужно. Зато CPython платит за это постоянно: почти каждое присваивание,
добавление в контейнер, удаление из контейнера и возврат значения двигает
`ob_refcnt`. Каждый объект также хранит счётчик ссылок.

Tracing GC не делает `INCREF/DECREF` на каждой операции со ссылками и может иметь
меньше metadata на объект. Но он периодически делает более дорогую операцию:
останавливается, обходит root set, помечает live objects и сканирует heap в
sweep phase. Для микроконтроллера MicroPython такой обмен выгоден: меньше
накладные расходы на каждый объект, простой block-based allocator и возможность
работать внутри заранее выделенного heap.

Практический вывод:

- CPython лучше переносит стиль с большим числом короткоживущих объектов.
- MicroPython требует контролировать пики аллокаций и иногда явно вызывать
  `gc.collect()` в безопасный момент.
- На MicroPython важнее проектировать объём памяти заранее, чем надеяться на то,
  что временные объекты "сразу исчезнут".

## Техники снижения расхода памяти

- Использовать `const()` для констант, чтобы MicroPython мог заменить обращение к
  имени литералом в bytecode.
- Выносить неизменяемый код и данные во frozen modules/bytecode, чтобы они жили
  во flash, а не в RAM.
- Предвыделять буферы: `bytearray`, `array.array`, fixed-size ring buffer.
- Использовать `memoryview` для slices без копирования.
- Использовать `array.array` вместо `list[int]`, если элементы однотипные и
  помещаются в C-тип.
- Использовать `range` вместо `list(range(...))`, когда нужен только перебор.
- Использовать `collections.deque(iterable, maxlen)` или собственный ring buffer
  как ограниченное окно последних N значений.
- Хранить физические величины fixed-point целыми числами, например температуру в
  сотых долях градуса.
- Избегать конкатенации строк в цикле.
- Не создавать `dict` для каждой записи, если поля фиксированы.
- В горячем коде кешировать часто используемые функции и методы в локальные
  переменные.
- Контролировать `gc.collect()` и при необходимости `gc.threshold()`.

## C-структуры данных: MicroPython vs CPython

Ниже приведён разбор по исходникам MicroPython `py/obj*.c`, `py/obj*.h`,
`py/map.c` и соответствующим структурам CPython 3.12.3. Для каждого типа важны
четыре вопроса: из каких полей состоит объект, где лежат элементы, как объект
создаётся и как растёт при добавлении.

| Тип | MicroPython | CPython | Создание и рост | Практический вывод |
| --- | --- | --- | --- | --- |
| `list` | `mp_obj_list_t { base, alloc, len, items }`, элементы `mp_obj_t` | `PyListObject { PyObject_VAR_HEAD, ob_item, allocated }`, элементы `PyObject*` | MicroPython создаёт объект списка и отдельный `items`, минимум 4 slots; `append` удваивает `alloc`; `pop` может сжать при `alloc > 2 * len`. CPython растёт мягче: примерно `newsize + newsize/8 + 6`, с округлением. | MicroPython list дешевле для small int, но всё равно держит запас ссылок. |
| `tuple` | `mp_obj_tuple_t { base, len, items[] }` | `PyTupleObject { PyObject_VAR_HEAD, ob_item[] }` | Оба хранят элементы inline в одном объекте переменного размера. После создания не растёт. Empty tuple обычно singleton/constant. | Лучше list для неизменяемых наборов фиксированной длины. |
| `int` | small int прямо в `mp_obj_t`; big int как `mp_obj_int_t` с `long long` или `mpz` в зависимости от сборки | `PyLongObject` с `PyObject_HEAD` и массивом digits; small int кешируется, но остаётся объектом | MicroPython small int не аллоцируется в heap. CPython int всегда объект, даже если переиспользуется из small-int cache. | Малые целые особенно выгодны в MicroPython. |
| `str` | `mp_obj_str_t { base, hash, len, data }` или `qstr` для interned strings | `PyUnicodeObject`/compact unicode layout с header, kind, length, hash и data | Обычная строка хранит bytes и hash; `qstr` хранит interned текст один раз и сравнивается по id. CPython оптимизирует Unicode-представление, но строка остаётся полноценным объектом. | Повторяющиеся идентификаторы и имена атрибутов в MicroPython дешевле через `qstr`. |
| `dict` | `mp_obj_dict_t { base, mp_map_t }`, `mp_map_elem_t { key, value }` | `PyDictObject` с compact ordered hash table, indices/entries, возможным split table для instance dict | MicroPython использует open addressing; размеры таблицы идут `0,2,4,6,8,10,12,17,23,...`; при заполнении создаётся новая таблица и элементы rehash. | Удобный, но дорогой тип для маленького heap. |
| `set` | `mp_obj_set_t { base, mp_set_t }`, таблица `mp_obj_t` | `PySetObject` с hash table entries, dummy slots и hash values | MicroPython set похож на dict без values; при нехватке места rehash в таблицу большего размера. | Дешевле dict, но всё равно hash-table с запасом. |
| `frozenset` | тот же storage, другой type и hashable API | близок к set, но immutable и с cached hash | В MicroPython создаётся как set-таблица, затем используется неизменяемый API. | Почти не экономит память относительно set; нужен для hashability. |
| `array.array` | `mp_obj_array_t { base, typecode, free, len, items }` | array object с header и contiguous C buffer | MicroPython хранит typed buffer; `append` добавляет запас 8 элементов; `extend` может realloc. | Лучший вариант для числовых массивов вместо `list[int]`. |
| `range` | `mp_obj_range_t { base, start, stop, step }` как `mp_int_t` | `rangeobject` хранит `start`, `stop`, `step`, `length` как `PyObject*` | Элементы не материализуются. При итерации создаётся/используется iterator, значения выдаются по формуле. | Почти O(1) по памяти и в CPython, и в MicroPython; в MicroPython ещё компактнее. |
| `collections.deque` | `mp_obj_deque_t { base, alloc, i_get, i_put, items, flags }`, bounded circular buffer | CPython deque использует цепочку блоков и может расти динамически; `maxlen` опционален | MicroPython требует `maxlen`, выделяет `maxlen + 1` slots и дальше перезаписывает/контролирует переполнение. | Хорош для окна последних N значений без роста heap. |

Общий вывод: CPython обычно хранит ссылки на полноценные `PyObject` с refcount и
типом. MicroPython хранит `mp_obj_t`, где small int, `qstr` и immediate values не
создают отдельных heap-объектов. Поэтому одинаковый Python-код может иметь очень
разную цену в памяти.

## Методика измерений

Скрипт `bench/bench_structs.py` создаёт объекты размеров:

```text
0, 1, 2, 4, 5, 8, 9, 16, 17, 23, 29, 64, 256, 1024
```

Скрипт `bench/bench_programs.py` сравнивает две версии обработки телеметрии для
`N = 64, 256, 1024` входных строк.

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
  "time_us": 69,
  "status": "ok"
}
```

### Что значат метрики

- `shallow_size` - результат `sys.getsizeof(obj)` в CPython. Это размер только
  верхнего объекта: например, размер списка и его массива указателей, но не
  рекурсивный размер всех объектов, на которые список ссылается.
- `peak_memory` - максимум traced memory, замеченный `tracemalloc` между
  `tracemalloc.start()` и `tracemalloc.get_traced_memory()`. Он включает
  временные аллокации, которые могли исчезнуть к концу построения объекта.
- `memory_delta` в CPython - текущий traced memory после построения объекта или
  выполнения программы. Это ближе к live footprint, чем к пику.
- `memory_delta` в MicroPython - `free_before - free_after` после `gc.collect()`
  до и после измерения. Если `gc.mem_free()` недоступен, используется
  `gc.mem_alloc()`. Это приблизительная цена живых объектов в Python heap.
- `time_us` - время выполнения в микросекундах. Для структур используется
  несколько повторов и берётся среднее; для программы измеряется полный прогон.
- `status` - `ok`, `memory_error` или `error`. Для embedded-задач это отдельная
  важная метрика: иногда главное не ускорение, а прохождение без OOM.

Почему `shallow_size` и `peak_memory` сильно отличаются:

- `shallow_size` не считает вложенные объекты. Для `list(range(1024))` он видит
  сам list и массив slots, но не все `PyLongObject`.
- `peak_memory` видит временные объекты построения: генераторы, элементы,
  промежуточные таблицы при rehash, временные строки и списки.
- В CPython часть памяти может жить во free lists или allocator arenas, а
  `tracemalloc` измеряет Python memory blocks, не полный RSS процесса.
- В MicroPython результат округляется блоками heap и зависит от состояния GC,
  интернированных строк, импортов и фрагментации.

Дополнительные производные метрики:

- `bytes_per_element = memory_delta / n` - удобно сравнивать последовательности.
- `peak/shallow = peak_memory / shallow_size` - показывает, насколько peak больше
  поверхностного размера объекта.
- `OOM threshold` - первое значение `n`, на котором case получает
  `memory_error` при заданном heap.
- Для программ: форма роста памяти, то есть O(N) у наивной версии или O(1) у
  версии с фиксированным буфером.

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

## Результаты: структуры данных

### CPython baseline, n = 1024

Локальный baseline получен на CPython 3.12.3. Эти числа не являются оценкой
MicroPython heap, но показывают относительные свойства структур и дают точку
сравнения.

| Тип | `shallow_size`, bytes | `memory_delta`, bytes | `peak_memory`, bytes | `peak/shallow` | `memory_delta / n` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `list` | 8856 | 33560 | 33568 | 3.79 | 32.8 |
| `tuple` | 8232 | 32936 | 33024 | 4.01 | 32.2 |
| `int` | 164 | 324 | 460 | 2.80 | 0.3 |
| `str` | 1065 | 1225 | 1225 | 1.15 | 1.2 |
| `dict` | 36952 | 61712 | 69616 | 1.88 | 60.3 |
| `set` | 32984 | 57688 | 57776 | 1.75 | 56.3 |
| `frozenset` | 32984 | 57688 | 57776 | 1.75 | 56.3 |
| `array.array('h')` | 2140 | 2356 | 2476 | 1.16 | 2.3 |
| `range` | 48 | 240 | 240 | 5.00 | 0.2 |
| `collections.deque` | 9208 | 33968 | 34056 | 3.70 | 33.2 |

Главные наблюдения:

- `range` имеет маленький `shallow_size`, но `peak/shallow = 5.00`, потому что
  `tracemalloc` учитывает служебные traced blocks вокруг создания объекта. При
  этом абсолютный peak всё равно всего 240 bytes.
- `array.array('h')` хранит 1024 значения примерно за 2.3 traced bytes на
  элемент, потому что сами элементы лежат в C-буфере по 2 bytes.
- `list`, `tuple` и `deque` в CPython дают около 32-33 traced bytes на элемент
  при построении из `range(1024)`, потому что элементы являются `PyLongObject`.
- `dict`, `set`, `frozenset` имеют высокий overhead из-за hash-table и запаса
  под open addressing.

### MicroPython Unix, heap 64K, n = 1024

| Тип | `memory_delta`, bytes | `memory_delta / n` | `time_us` | Статус |
| --- | ---: | ---: | ---: | --- |
| `list` | 8224 | 8.0 | 86 | ok |
| `tuple` | 8224 | 8.0 | 77 | ok |
| `int` | 192 | 0.2 | 2 | ok |
| `str` | 1088 | 1.1 | 18 | ok |
| `dict` | MemoryError | - | - | memory_error |
| `set` | MemoryError | - | - | memory_error |
| `frozenset` | MemoryError | - | - | memory_error |
| `array.array('h')` | 2080 | 2.0 | 84 | ok |
| `range` | 32 | 0.03 | 0 | ok |
| `collections.deque` | 8288 | 8.1 | 47 | ok |

При 64K heap крупные hash-table структуры не помещаются уже на `n = 1024`.
`range` остаётся практически постоянным, а `array.array('h')` занимает примерно
в четыре раза меньше памяти, чем `list`/`tuple` с тем же количеством small int.

### ESP8266, n = 1024

| Тип | `memory_delta`, bytes | `memory_delta / n` | `time_us` | Статус |
| --- | ---: | ---: | ---: | --- |
| `list` | 4112 | 4.0 | 13965 | ok |
| `tuple` | MemoryError | - | - | memory_error |
| `int` | 160 | 0.2 | 283 | ok |
| `str` | 1056 | 1.0 | 1376 | ok |
| `dict` | MemoryError | - | - | memory_error |
| `set` | MemoryError | - | - | memory_error |
| `frozenset` | MemoryError | - | - | memory_error |
| `array.array('h')` | 2064 | 2.0 | 5010 | ok |
| `range` | 16 | 0.02 | 280 | ok |
| `collections.deque` | 4144 | 4.0 | 4969 | ok |

ESP8266 сильнее подчёркивает разницу между структурами: `array.array('h')`
помещается для 1024 элементов, `range` практически бесплатен, а крупные
`dict`/`set`/`frozenset` падают с `MemoryError`.

## Оптимизация программы

Практический пример - обработка телеметрии:

```text
timestamp,temp,humidity,status
```

Наивная версия `apps/telemetry_naive.py` делает типичные для CPython, но
неудачные для MicroPython вещи:

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

| Программа | N входов | Live/traced bytes | Peak bytes | Live bytes / input | Time, us | Рост памяти |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| naive | 64 | 20247 | 22555 | 316.4 | 1351 | O(N) |
| optimized | 64 | 1752 | 2304 | 27.4 | 3081 | O(1) после capacity |
| naive | 256 | 78283 | 87816 | 305.8 | 6582 | O(N) |
| optimized | 256 | 1744 | 2296 | 6.8 | 22498 | O(1) |
| naive | 1024 | 332251 | 371021 | 324.5 | 31186 | O(N) |
| optimized | 1024 | 1768 | 2288 | 1.7 | 108806 | O(1) |

На CPython оптимизированная версия медленнее, потому что ручной Python-парсер
проигрывает C-реализациям `split()` и `float()`. Но цель этой версии - не
ускорить CPython, а убрать рост памяти и временные аллокации. Для MicroPython
это обычно важнее: программа, которая чуть медленнее, но не падает с
`MemoryError`, лучше подходит для микроконтроллера.

### Результаты MicroPython Unix

Unix-port v1.28.0 был запущен с ограничением heap. Это удобная промежуточная
проверка: поведение ближе к MicroPython, но без аппаратных ограничений
serial-запуска.

#### Программа, heap 256K

| Программа | N входов | `memory_delta`, bytes | Time, us | Статус | Примечание |
| --- | ---: | ---: | ---: | --- | --- |
| naive | 64 | 16384 | 1316 | ok | count=64 |
| optimized | 64 | 992 | 864 | ok | count=64, overwrites=0 |
| naive | 256 | MemoryError | - | memory_error | OOM threshold: 256 |
| optimized | 256 | 992 | 9307 | ok | count=128, overwrites=128 |
| naive | 1024 | MemoryError | - | memory_error | - |
| optimized | 1024 | 992 | 26790 | ok | count=128, overwrites=896 |

#### Программа, heap 1M

| Программа | N входов | `memory_delta`, bytes | Time, us | Статус | Примечание |
| --- | ---: | ---: | ---: | --- | --- |
| naive | 64 | 16384 | 1568 | ok | count=64 |
| optimized | 64 | 992 | 1639 | ok | count=64, overwrites=0 |
| naive | 256 | 64544 | 12871 | ok | count=256 |
| optimized | 256 | 992 | 8108 | ok | count=128, overwrites=128 |
| naive | 1024 | MemoryError | - | memory_error | OOM threshold: 1024 |
| optimized | 1024 | 992 | 43152 | ok | count=128, overwrites=896 |

Даже при 1M heap наивная версия не проходит 1024 строки, потому что хранит все
записи, float-значения, словари и строковый лог. Оптимизированная версия
сохраняет постоянный footprint и ограничивает историю последними 128 измерениями.

### Результаты ESP8266

| Программа | N входов | `memory_delta`, bytes | Time, us | Статус | Примечание |
| --- | ---: | ---: | ---: | --- | --- |
| naive | 64 | 6704 | 239480 | ok | count=64 |
| optimized | 64 | 768 | 189644 | ok | count=64, overwrites=0 |
| naive | 256 | MemoryError | - | memory_error | OOM threshold: 256 |
| optimized | 256 | 768 | 1118375 | ok | count=128, overwrites=128 |
| naive | 1024 | MemoryError | - | memory_error | - |
| optimized | 1024 | 768 | 5317740 | ok | count=128, overwrites=896 |

На реальной плате optimized-версия даёт результат, ради которого она была
написана: рост числа входных строк влияет на время, но почти не влияет на
память. Наивная версия уже на 256 строках не помещается в heap ESP8266.

## C - CPython - MicroPython

Чтобы отделить цену алгоритма от цены Python-объектов, добавлен минимальный
C-бейзлайн `apps/telemetry_c_baseline.c`. Он хранит те же данные, что
оптимизированная Python-версия:

```c
#define TELEMETRY_CAPACITY 128

typedef struct {
    int16_t temps[TELEMETRY_CAPACITY];
    int16_t hums[TELEMETRY_CAPACITY];
    int32_t sum_temp;
    int32_t sum_hum;
    int16_t min_temp;
    int16_t max_temp;
    int16_t min_hum;
    int16_t max_hum;
    uint16_t count;
    uint16_t pos;
    uint16_t overwrites;
} telemetry_state_t;
```

Команда проверки:

```bash
cc -std=c11 -O2 -Wall -Wextra -pedantic apps/telemetry_c_baseline.c -o /tmp/telemetry_c_baseline
/tmp/telemetry_c_baseline
```

Локальный результат:

| N входов | count | overwrites | `sizeof(state)`, bytes | Два массива, bytes | Time, us, пример |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 64 | 64 | 0 | 536 | 512 | 2 |
| 256 | 128 | 128 | 536 | 512 | 7 |
| 1024 | 128 | 896 | 536 | 512 | 36 |

Сравнение смыслов:

- **C** даёт максимальный контроль: два массива `int16_t[128]` занимают ровно
  `128 * 2 * 2 = 512` bytes, вся структура с суммами и индексами занимает
  536 bytes на этой машине. Нет объектов, GC, refcount, hash-table и
  динамического роста. Минусы: нужно вручную проектировать память, парсинг,
  ошибки и границы буферов.
- **CPython** даёт максимум удобства: `dict`, `list`, `float`, строки и богатая
  стандартная библиотека. Цена - большой per-object overhead. Наивная telemetry
  на CPython при 1024 строках держит 332251 live/traced bytes и достигает peak
  371021 bytes.
- **MicroPython** находится посередине. Он сохраняет Python-синтаксис и доступ к
  железу, но заставляет писать ближе к embedded-модели: fixed-size buffers,
  fixed-point, минимум временных объектов. Оптимизированная telemetry на
  ESP8266 держит около 768 bytes Python heap и проходит 1024 входа, где наивная
  версия падает уже на 256.

Именно для этого MicroPython удобен: быстро писать прикладную логику для датчиков,
контроллеров и небольших устройств, но всё ещё думать как embedded-разработчик о
RAM, flash, пиках аллокаций и предсказуемости.

## Сценарии для проверки на MicroPython

1. OOM при малом heap:

```bash
micropython -X heapsize=64K bench/bench_structs.py --target=micropython-unix --heap-size=64K
```

Полученный результат: крупные `dict`, `set` и `frozenset` на `n = 1024` получают
`memory_error`, а `range` и `array.array` переживают ограничение лучше.

2. Фрагментация и временные объекты:

```bash
micropython -X heapsize=256K bench/bench_programs.py --target=micropython-unix --heap-size=256K
```

Полученный результат: naive-версия имеет больший `memory_delta` и падает на
256/1024 строках при 256K heap, optimized-версия проходит все размеры с почти
постоянной памятью.

3. ESP8266:

```bash
mpremote connect /dev/ttyUSB0 mount . exec "import bench.bench_programs as b; b.main(['bench/bench_programs.py', '--target=esp8266'])"
```

Полученный результат: полный запуск помещается для бенчмарка целиком, но
отдельные cases возвращают `memory_error`: naive-программа падает на 256 и 1024
строках, optimized-программа проходит все размеры.

## Выводы

MicroPython экономит память не одной техникой, а набором решений: compact object
representation, tagged `mp_obj_t`, qstr interning, простой block-based heap и
mark-and-sweep GC. Эти решения уменьшают накладные расходы, но не отменяют
главного ограничения embedded-среды: heap конечен, а временные аллокации опасны.

При выборе структур данных:

- Для последовательности фиксированного размера лучше `tuple` или `array.array`,
  а не растущий `list`.
- Для числового потока лучше `array.array`/`bytearray` плюс индекс, чем список
  Python-чисел.
- Для окна последних N значений лучше bounded `deque` или ring buffer.
- Для перебора диапазона лучше `range`, потому что он O(1) по памяти.
- `dict` и `set` удобны, но дороги, поэтому на MicroPython их стоит оставлять
  для случаев, где hash lookup действительно нужен.

Главный practical lesson из telemetry-примера: под MicroPython нужно проектировать
объём памяти заранее. Fixed-size buffers, fixed-point integers и отсутствие
временных контейнеров дают стабильный memory footprint. Это важнее красивого
CPython-стиля, если программа должна жить на микроконтроллере и не падать с
`MemoryError`.

## Источники

- MicroPython Memory Management: https://docs.micropython.org/en/latest/develop/memorymgt.html
- MicroPython constrained devices: https://docs.micropython.org/en/latest/reference/constrained.html
- MicroPython speed guide: https://docs.micropython.org/en/latest/reference/speed_python.html
- MicroPython Unix quick reference: https://docs.micropython.org/en/latest/unix/quickref.html
- MicroPython ESP8266 tutorial: https://docs.micropython.org/en/latest/esp8266/tutorial/intro.html
- MicroPython `mpremote`: https://docs.micropython.org/en/latest/reference/mpremote.html
- MicroPython source v1.28.0: https://github.com/micropython/micropython/tree/v1.28.0/py
- CPython `gc`: https://docs.python.org/3/library/gc.html
- CPython `sys.getsizeof`: https://docs.python.org/3/library/sys.html#sys.getsizeof
- CPython `tracemalloc`: https://docs.python.org/3/library/tracemalloc.html
- CPython source v3.12.3: https://github.com/python/cpython/tree/v3.12.3
