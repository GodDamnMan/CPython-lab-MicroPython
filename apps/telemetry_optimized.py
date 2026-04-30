try:
    from micropython import const
except ImportError:
    def const(value):
        return value

try:
    import array
except ImportError:
    array = None


_DEFAULT_CAPACITY = const(128)
_SCALE = const(100)
_COMMA = const(44)
_DOT = const(46)
_MINUS = const(45)
_LF = const(10)
_CR = const(13)
_ZERO = const(48)
_NINE = const(57)


def _scaled(sign, integer, frac, digits):
    if digits == 1:
        frac *= 10
    return sign * (integer * _SCALE + frac)


class TelemetryProcessor:
    def __init__(self, capacity=_DEFAULT_CAPACITY):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if array is None:
            raise ImportError("array module is required")
        self.capacity = capacity
        self.temps = array.array("h", [0] * capacity)
        self.hums = array.array("h", [0] * capacity)
        self.count = 0
        self.pos = 0
        self.sum_temp = 0
        self.sum_hum = 0
        self.min_temp = 0
        self.max_temp = 0
        self.min_hum = 0
        self.max_hum = 0
        self.overwrites = 0

    def _parse_line(self, line):
        if isinstance(line, str):
            line = line.encode()

        field = 0
        sign = 1
        integer = 0
        frac = 0
        digits = 0
        in_frac = False
        temp = 0
        hum = 0

        for ch in line:
            if ch == _COMMA or ch == _LF or ch == _CR:
                value = _scaled(sign, integer, frac, digits)
                if field == 1:
                    temp = value
                elif field == 2:
                    hum = value
                    return temp, hum
                field += 1
                sign = 1
                integer = 0
                frac = 0
                digits = 0
                in_frac = False
            elif ch == _MINUS:
                sign = -1
            elif ch == _DOT:
                in_frac = True
            elif _ZERO <= ch <= _NINE:
                digit = ch - _ZERO
                if in_frac:
                    if digits < 2:
                        frac = frac * 10 + digit
                        digits += 1
                else:
                    integer = integer * 10 + digit

        if field == 2:
            hum = _scaled(sign, integer, frac, digits)
        return temp, hum

    def _recompute_extremes(self):
        count = self.count
        if count == 0:
            self.min_temp = self.max_temp = 0
            self.min_hum = self.max_hum = 0
            return

        tmin = tmax = self.temps[0]
        hmin = hmax = self.hums[0]
        for index in range(1, count):
            temp = self.temps[index]
            hum = self.hums[index]
            if temp < tmin:
                tmin = temp
            elif temp > tmax:
                tmax = temp
            if hum < hmin:
                hmin = hum
            elif hum > hmax:
                hmax = hum
        self.min_temp = tmin
        self.max_temp = tmax
        self.min_hum = hmin
        self.max_hum = hmax

    def process_line(self, line):
        temp, hum = self._parse_line(line)
        index = self.pos
        overwrote_extreme = False

        if self.count < self.capacity:
            self.count += 1
        else:
            old_temp = self.temps[index]
            old_hum = self.hums[index]
            self.sum_temp -= old_temp
            self.sum_hum -= old_hum
            self.overwrites += 1
            overwrote_extreme = (
                old_temp == self.min_temp
                or old_temp == self.max_temp
                or old_hum == self.min_hum
                or old_hum == self.max_hum
            )

        self.temps[index] = temp
        self.hums[index] = hum
        self.sum_temp += temp
        self.sum_hum += hum

        self.pos = index + 1
        if self.pos == self.capacity:
            self.pos = 0

        if self.count == 1:
            self.min_temp = self.max_temp = temp
            self.min_hum = self.max_hum = hum
        elif overwrote_extreme:
            self._recompute_extremes()
        else:
            if temp < self.min_temp:
                self.min_temp = temp
            elif temp > self.max_temp:
                self.max_temp = temp
            if hum < self.min_hum:
                self.min_hum = hum
            elif hum > self.max_hum:
                self.max_hum = hum

        return temp, hum

    def summary(self):
        if self.count == 0:
            return (0, 0, 0, 0, 0, 0, 0, self.overwrites)
        return (
            self.count,
            self.sum_temp // self.count,
            self.min_temp,
            self.max_temp,
            self.sum_hum // self.count,
            self.min_hum,
            self.max_hum,
            self.overwrites,
        )


def process_stream(lines, capacity=_DEFAULT_CAPACITY):
    processor = TelemetryProcessor(capacity)
    for line in lines:
        processor.process_line(line)
    return processor.summary()
