#include <stdint.h>
#include <stdio.h>
#include <time.h>

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

static void telemetry_init(telemetry_state_t *state) {
    state->sum_temp = 0;
    state->sum_hum = 0;
    state->min_temp = 0;
    state->max_temp = 0;
    state->min_hum = 0;
    state->max_hum = 0;
    state->count = 0;
    state->pos = 0;
    state->overwrites = 0;
}

static void telemetry_recompute_extremes(telemetry_state_t *state) {
    if (state->count == 0) {
        state->min_temp = state->max_temp = 0;
        state->min_hum = state->max_hum = 0;
        return;
    }

    int16_t tmin = state->temps[0];
    int16_t tmax = state->temps[0];
    int16_t hmin = state->hums[0];
    int16_t hmax = state->hums[0];

    for (uint16_t i = 1; i < state->count; i++) {
        int16_t temp = state->temps[i];
        int16_t hum = state->hums[i];
        if (temp < tmin) {
            tmin = temp;
        }
        if (temp > tmax) {
            tmax = temp;
        }
        if (hum < hmin) {
            hmin = hum;
        }
        if (hum > hmax) {
            hmax = hum;
        }
    }

    state->min_temp = tmin;
    state->max_temp = tmax;
    state->min_hum = hmin;
    state->max_hum = hmax;
}

static void telemetry_process_sample(telemetry_state_t *state, int16_t temp, int16_t hum) {
    uint16_t index = state->pos;
    int overwrote_extreme = 0;

    if (state->count < TELEMETRY_CAPACITY) {
        state->count++;
    } else {
        int16_t old_temp = state->temps[index];
        int16_t old_hum = state->hums[index];
        state->sum_temp -= old_temp;
        state->sum_hum -= old_hum;
        state->overwrites++;
        overwrote_extreme = old_temp == state->min_temp
            || old_temp == state->max_temp
            || old_hum == state->min_hum
            || old_hum == state->max_hum;
    }

    state->temps[index] = temp;
    state->hums[index] = hum;
    state->sum_temp += temp;
    state->sum_hum += hum;

    state->pos++;
    if (state->pos == TELEMETRY_CAPACITY) {
        state->pos = 0;
    }

    if (state->count == 1) {
        state->min_temp = state->max_temp = temp;
        state->min_hum = state->max_hum = hum;
    } else if (overwrote_extreme) {
        telemetry_recompute_extremes(state);
    } else {
        if (temp < state->min_temp) {
            state->min_temp = temp;
        }
        if (temp > state->max_temp) {
            state->max_temp = temp;
        }
        if (hum < state->min_hum) {
            state->min_hum = hum;
        }
        if (hum > state->max_hum) {
            state->max_hum = hum;
        }
    }
}

static int64_t elapsed_us(clock_t start, clock_t end) {
    return (int64_t)(end - start) * 1000000 / CLOCKS_PER_SEC;
}

static void run_case(int n) {
    telemetry_state_t state;
    telemetry_init(&state);

    clock_t start = clock();
    for (int i = 0; i < n; i++) {
        int16_t temp = (int16_t)(2000 + (i * 37) % 1500);
        int16_t hum = (int16_t)(4500 + (i * 19) % 3000);
        telemetry_process_sample(&state, temp, hum);
    }
    clock_t end = clock();

    int32_t avg_temp = state.count == 0 ? 0 : state.sum_temp / state.count;
    int32_t avg_hum = state.count == 0 ? 0 : state.sum_hum / state.count;
    printf(
        "n=%d count=%u overwrites=%u state_bytes=%zu window_bytes=%zu avg_temp=%ld avg_hum=%ld time_us=%lld\n",
        n,
        (unsigned)state.count,
        (unsigned)state.overwrites,
        sizeof(state),
        sizeof(state.temps) + sizeof(state.hums),
        (long)avg_temp,
        (long)avg_hum,
        (long long)elapsed_us(start, end)
    );
}

int main(void) {
    printf("capacity=%d sizeof_state=%zu\n", TELEMETRY_CAPACITY, sizeof(telemetry_state_t));
    run_case(64);
    run_case(256);
    run_case(1024);
    return 0;
}
