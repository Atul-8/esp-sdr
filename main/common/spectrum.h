/* Shared on-device spectrum protocol. Acquisition callbacks return normalized
 * IQ10 words (I in bits 0..9, Q in 10..19) in stable CPU-owned memory. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
typedef bool (*spectrum_acquire_fn)(unsigned n, unsigned rate, const uint32_t **words, unsigned *elapsed_us);
bool spectrum_command(const char *line, unsigned frequency_mhz, spectrum_acquire_fn acquire);

/* 扫频状态机（scan_fsm.c）专用：在**当前**频点采一帧并发出去。
 * 线上格式与 ``SPEC`` 完全相同（先文本头 ``SPEC n fs n freq``，再一帧 SPC1），
 * 只是 header.frames 恒为 1、帧数上限也为 1 —— 主机侧解析器可以原样复用。 */
bool spectrum_sweep_frame(unsigned n, unsigned rate_code, unsigned detector, unsigned freq_mhz,
                          spectrum_acquire_fn acquire);

bool spectrum_fft_init(void);

/* Snapshot and continuous backends run exclusively. Reuse their large buffers
 * on SRAM-constrained targets; acquiring this workspace invalidates the cached
 * snapshot window so a later transport switch rebuilds it. */
typedef struct {
    int16_t *fft, *window;
    float *power;
    uint8_t *frame;
} spectrum_workspace_t;
spectrum_workspace_t spectrum_workspace(void);
