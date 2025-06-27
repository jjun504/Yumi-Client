#!/usr/bin/env python3
import pyaudio
import numpy as np

RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
FRAMES_PER_BUFFER = 512
RECORD_SECONDS = 3

def list_devices(p):
    print("Available audio devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            print(f"[{i}] {info['name']} - "
                  f"Inputs: {info['maxInputChannels']}, "
                  f"Default sample rate: {info['defaultSampleRate']}")

def record_test(p, device_index):
    print(f"\nRecording from device {device_index} for {RECORD_SECONDS}s...")
    stream = p.open(
        rate=RATE,
        channels=CHANNELS,
        format=FORMAT,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=FRAMES_PER_BUFFER
    )
    frames = []
    for _ in range(int(RATE / FRAMES_PER_BUFFER * RECORD_SECONDS)):
        data = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)
        frames.append(np.frombuffer(data, dtype=np.int16))
    stream.stop_stream()
    stream.close()

    samples = np.concatenate(frames)
    print(f"Samples captured: {len(samples)}")
    print(f"Max amplitude: {samples.max()}")
    print(f"Min amplitude: {samples.min()}")
    print(f"Mean amplitude: {samples.mean():.2f}")
    if np.max(np.abs(samples)) < 100:
        print("⚠️  没有明显声音输入")
    else:
        print("✅  录音成功，检测到音频信号")

if __name__ == "__main__":
    pa = pyaudio.PyAudio()
    list_devices(pa)
    idx = input("\nEnter the index of your USB mic and hit Enter: ")
    try:
        dev = int(idx)
    except ValueError:
        print("Invalid index")
    else:
        record_test(pa, dev)
    pa.terminate()
