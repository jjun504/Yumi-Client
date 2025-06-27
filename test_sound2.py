import pyaudio
import numpy as np

RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
FRAMES_PER_BUFFER = 512  # 你可以换成 porcupine.frame_length

audio = pyaudio.PyAudio()

stream = audio.open(
    rate=RATE,
    channels=CHANNELS,
    format=FORMAT,
    input=True,
    input_device_index=2,  # ← 如有需要可手动指定
    frames_per_buffer=FRAMES_PER_BUFFER
)

print("Recording 3 seconds...")

frames = []
for _ in range(int(RATE / FRAMES_PER_BUFFER * 3)):  # 录3秒
    data = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)
    frames.append(np.frombuffer(data, dtype=np.int16))

stream.stop_stream()
stream.close()
audio.terminate()

samples = np.concatenate(frames)
print(f"Samples: {len(samples)}")
print(f"Max amplitude: {samples.max()}")
print(f"Min amplitude: {samples.min()}")
print(f"Mean amplitude: {samples.mean():.2f}")

if np.max(np.abs(samples)) < 100:
    print("⚠️  没有明显声音输入，可能麦克风静音或设备配置有误")
else:
    print("✅  录音成功，检测到有效音频信号")
