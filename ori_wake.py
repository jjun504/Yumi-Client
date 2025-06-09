import pvporcupine
import pyaudio
import struct
import audioop
from config import config
from const_config import const_config
from loguru import logger
import sys
import time

class PorcupineWakeWord:
    def __init__(self):
        try:
            # Ã¤Â½Â¿Ã§â€Â¨ const_config.get Ã¦â€“Â¹Ã¦Â³â€¢Ã¨Å½Â·Ã¥â€“Ã©â€¦Ã§Â½Â®
            pi_enable = True
            windows_enable = False

            if pi_enable:
                access_key = "FQF0FtWRzvVldLKIMoMw3xm69iTm4xJglUfZgRkSkYYFAW2Zi6kYMA=="
                keyword_paths = ["porcupine/Hello-Chris_en_raspberry-pi_v3_0_0/Hello-Chris_en_raspberry-pi_v3_0_0.ppn"]
                logger.debug(f"Using Picovoice API key (Pi): {access_key[:4]}...{access_key[-4:]}")
                logger.debug("Loading wake word model: Hello-Chris_en_raspberry-pi_v3_0_0.ppn")
            elif windows_enable:
                access_key = const_config.get("voice_activity_detection.windows_api_key")
                keyword_paths = ["porcupine/hello-chris_en_windows_v3_0_0/hello-chris_en_windows_v3_0_0.ppn"]
                logger.debug(f"Using Picovoice API key (Windows): {access_key[:4]}...{access_key[-4:]}")
                logger.debug("Loading wake word model: hello-chris_en_windows_v3_0_0.ppn")
            else:
                logger.error("Neither Pi nor Windows mode is enabled")
                raise ValueError("No valid platform configuration found")

            self.porcupine = pvporcupine.create(
                access_key=access_key,
                keyword_paths=keyword_paths
            )

            logger.debug(f"Porcupine initialized with sample rate: {self.porcupine.sample_rate} Hz, frame length: {self.porcupine.frame_length}")

            # 设置采样率
            self.device_rate = 48000  # 麦克风的实际采样率
            self.processing_rate = self.porcupine.sample_rate  # Porcupine要求的采样率 (16000)

            # 计算每次读取的帧数，确保重采样后能得到足够的样本
            # Porcupine需要512个样本，重采样比例是3:1 (48000:16000)
            # 所以我们需要 512 * 3 = 1536 个原始样本
            self.frames_per_read = self.porcupine.frame_length * 3  # 512 * 3 = 1536

            logger.debug(f"Device sample rate: {self.device_rate} Hz, Processing sample rate: {self.processing_rate} Hz")
            logger.debug(f"Frames per read: {self.frames_per_read}")
            print(f"[DEBUG] Device rate: {self.device_rate}, Processing rate: {self.processing_rate}")
            print(f"[DEBUG] Frames per read: {self.frames_per_read}")
            print(f"[DEBUG] Porcupine frame length: {self.porcupine.frame_length}")
            print(f"[DEBUG] Expected PCM samples after resampling: {self.frames_per_read // 3}")

            self.myaudio = pyaudio.PyAudio()

            # 打印音频设备信息
            print(f"[DEBUG] Available audio devices:")
            for i in range(self.myaudio.get_device_count()):
                info = self.myaudio.get_device_info_by_index(i)
                if info['maxInputChannels'] > 0:
                    print(f"[DEBUG] Device {i}: {info['name']}, Rate: {info['defaultSampleRate']}")

            print(f"[DEBUG] Opening audio stream with device index 2")
            self.stream = self.myaudio.open(
                rate=self.device_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self.frames_per_read,
                input_device_index=2
            )

            # 测试读取一帧数据
            print("[DEBUG] Testing audio stream...")
            try:
                test_data = self.stream.read(self.frames_per_read, exception_on_overflow=False)
                print(f"[DEBUG] Test read successful: {len(test_data)} bytes")
                test_resampled = audioop.ratecv(test_data, 2, 1, self.device_rate, self.processing_rate, None)[0]
                print(f"[DEBUG] Test resample successful: {len(test_resampled)} bytes")
                test_pcm = list(struct.unpack('<' + 'h' * (len(test_resampled) // 2), test_resampled))
                print(f"[DEBUG] Test PCM conversion successful: {len(test_pcm)} samples")
                print(f"[DEBUG] PCM length matches Porcupine requirement: {len(test_pcm) == self.porcupine.frame_length}")
            except Exception as e:
                print(f"[ERROR] Audio stream test failed: {e}")

            logger.info("[Initialize][WakeUp] Wake Word Module initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Porcupine: {str(e)}")
            raise

    def listen(self, callback=None):
        """
        Start listening for wake word. Returns True if wake word detected.
        """
        logger.info("[WakeUp] Started listening for wake word")
        print("[DEBUG] Starting wake word detection loop")
        frame_count = 0
        try:
            while True:
                if(config.get("wake_word.enabled")):
                    # 每次读取足够的数据以获得512个重采样样本
                    raw_data = self.stream.read(self.frames_per_read, exception_on_overflow=False)
                    frame_count += 1

                    if frame_count % 10 == 0:  # 每10帧打印一次（因为现在每帧更大）
                        print(f"[DEBUG] Frame {frame_count}: Raw data length: {len(raw_data)} bytes")

                    # 重采样 48000 → 16000
                    resampled = audioop.ratecv(raw_data, 2, 1, self.device_rate, self.processing_rate, None)[0]

                    if frame_count % 10 == 0:
                        print(f"[DEBUG] Frame {frame_count}: Resampled data length: {len(resampled)} bytes")

                    # 转成 short/int16 list（Porcupine 要求）
                    pcm = list(struct.unpack('<' + 'h' * (len(resampled) // 2), resampled))

                    if frame_count % 10 == 0:
                        print(f"[DEBUG] Frame {frame_count}: PCM length: {len(pcm)}, Expected: {self.porcupine.frame_length}")
                        print(f"[DEBUG] Frame {frame_count}: PCM sample range: {min(pcm) if pcm else 0} to {max(pcm) if pcm else 0}")

                    # 检查PCM长度是否匹配
                    if len(pcm) != self.porcupine.frame_length:
                        if frame_count % 10 == 0:
                            print(f"[ERROR] PCM length mismatch! Got {len(pcm)}, expected {self.porcupine.frame_length}")
                        continue

                    # 送给 Porcupine 处理
                    keyword_idx = self.porcupine.process(pcm)
                    if keyword_idx >= 0:
                        detection_time = time.time()
                        print(f"[SUCCESS] Wake word detected at frame {frame_count}!")
                        logger.success(f"[WakeUp] Wake word detected")
                        if callback:
                            logger.debug("[Callback] Executing wake word callback")
                            callback()
        except Exception as e:
            logger.error(f"Error during wake word detection: {str(e)}")
            return False

    def stop(self):
        """Pause the audio stream without releasing resources."""
        if hasattr(self, 'stream') and self.stream:
            try:
                self.stream.stop_stream()
                logger.info("[WakeUp] Paused wake word detection")
            except Exception as e:
                logger.error(f"Failed to pause audio stream: {str(e)}")

    def resume(self):
        """Resume the paused audio stream."""
        if hasattr(self, 'stream') and self.stream:
            try:
                self.stream.start_stream()
                logger.info("[WakeUp] Resumed wake word detection")
            except Exception as e:
                logger.error(f"Failed to resume audio stream: {str(e)}")

    def __del__(self):
        """Clean up resources when object is destroyed."""
        try:
            if hasattr(self, 'stream') and self.stream:
                self.stream.close()
                logger.debug("Audio stream closed")

            if hasattr(self, 'myaudio') and self.myaudio:
                self.myaudio.terminate()
                logger.debug("PyAudio terminated")


            logger.info("[Destruct] Wake word detector resources cleaned up")
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

if __name__ == "__main__":
    logger.info("[Test] Starting wake word detection test")
    porcupine = PorcupineWakeWord()
    try:
        porcupine.listen()
    except KeyboardInterrupt:
        logger.info("[Test] Wake word test terminated by user")

