#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wake word detection module - responsible for wake word detection functionality
Implemented using Porcupine
"""

import logging
import threading
import time
import struct
import pyaudio
from queue import Queue

# Try to import pvporcupine library
try:
    import pvporcupine
    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False
    logging.warning("pvporcupine library not installed, wake word functionality will be disabled")

# Configure logging
logger = logging.getLogger("WakeWordDetector")

class PorcupineWakeWordDetector:
    """Porcupine wake word detector"""

    def __init__(self):
        """Initialize wake word detector"""
        self.porcupine = None          # Porcupine instance
        self.audio = None              # PyAudio instance
        self.stream = None             # Audio stream
        self.detection_thread = None   # Detection thread
        self.running = False           # Running flag
        self.paused = False            # Pause flag
        self.callback = None           # Wake callback function
        self.audio_queue = Queue(maxsize=100)  # Audio data queue (for saving audio before wake)
        self.lock = threading.Lock()   # Thread lock
        self.pre_buffer = []           # Pre-buffer, stores audio before wake
        self.pre_buffer_duration = 0   # Pre-buffer duration (seconds)
        self.config = None             # Configuration

    def initialize(self, config):
        """Initialize Porcupine wake word detector"""
        if not PORCUPINE_AVAILABLE:
            logger.error("Cannot initialize wake word detector: pvporcupine library not installed")
            return False

        try:
            self.config = config

            access_key = config.get('porcupine_access_key')
            keyword_paths = config.get('porcupine_keyword_paths', [])
            sensitivity = config.get('porcupine_sensitivity', 0.5)

            if not access_key or not keyword_paths:
                logger.error("Missing Porcupine configuration: access_key or keyword_paths")
                return False

            # Ensure keyword_paths is a list
            if not isinstance(keyword_paths, list):
                keyword_paths = [keyword_paths]

            # Create sensitivity list matching the number of keywords
            sensitivities = [float(sensitivity)] * len(keyword_paths)

            logger.info(f"Initialize Porcupine, keyword paths: {keyword_paths}, sensitivities: {sensitivities}")

            # Create Porcupine instance
            self.porcupine = pvporcupine.create(
                access_key=access_key,
                keyword_paths=keyword_paths,
                sensitivities=sensitivities
            )

            # Initialize audio
            self.audio = pyaudio.PyAudio()

            # Calculate pre-buffer size
            sample_rate = self.porcupine.sample_rate
            frame_length = self.porcupine.frame_length
            frames_per_second = sample_rate / frame_length
            self.pre_buffer_size = int(frames_per_second * self.pre_buffer_duration)

            logger.info(f"Wake word detection initialized successfully, sample rate: {sample_rate}Hz, frame length: {frame_length}, pre-buffer: {self.pre_buffer_duration}s")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize wake word detector: {e}")
            return False

    def start_detection(self):
        """Start wake word detection"""
        if not PORCUPINE_AVAILABLE or not self.porcupine:
            logger.error("Cannot start wake word detection: not initialized")
            return False

        try:
            # Open audio stream
            self.stream = self.audio.open(
                # input_device_index=0,
                rate=self.porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self.porcupine.frame_length
            )

            # Set running flag
            self.running = True
            self.paused = False

            # Create and start detection thread
            self.detection_thread = threading.Thread(target=self._detection_worker)
            self.detection_thread.daemon = True
            self.detection_thread.start()

            logger.info("Wake word detection started")
            return True

        except Exception as e:
            logger.error(f"Failed to start wake word detection: {e}")
            return False

    def stop_detection(self):
        """Stop wake word detection"""
        with self.lock:
            self.running = False

        # Wait for thread to end
        if self.detection_thread and self.detection_thread.is_alive():
            self.detection_thread.join(timeout=2.0)

        # Close audio stream
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
            except:
                pass

        logger.info("Wake word detection stopped")

    def pause_detection(self):
        """Pause wake word detection"""
        with self.lock:
            self.paused = True

        # Pause audio stream
        if self.stream:
            try:
                self.stream.stop_stream()
            except:
                pass

        logger.info("Wake word detection paused")

    def resume_detection(self):
        """Resume wake word detection"""
        with self.lock:
            self.paused = False

        # Resume audio stream
        if self.stream:
            try:
                self.stream.start_stream()
            except:
                pass

        logger.info("Wake word detection resumed")

    def set_callback(self, callback):
        """Set wake callback function"""
        self.callback = callback

    def _detection_worker(self):
        """Wake word detection worker thread"""
        try:
            # Clear pre-buffer
            self.pre_buffer.clear()

            while self.running:
                # Check if paused
                if self.paused:
                    time.sleep(0.1)
                    continue

                try:
                    # Read audio data
                    pcm = self.stream.read(self.porcupine.frame_length, exception_on_overflow=False)

                    # Save to pre-buffer
                    self.pre_buffer.append(pcm)

                    # Maintain pre-buffer size
                    while len(self.pre_buffer) > self.pre_buffer_size:
                        self.pre_buffer.pop(0)

                    # Process audio data
                    pcm_unpacked = struct.unpack_from("h" * self.porcupine.frame_length, pcm)

                    # Detect wake word
                    result = self.porcupine.process(pcm_unpacked)

                    # If wake word detected
                    if result >= 0:
                        logger.info(f"Wake word detected! Index: {result}")

                        # Put pre-buffer audio into queue
                        for frame in self.pre_buffer:
                            self.audio_queue.put(frame)

                        # Execute callback
                        if self.callback:
                            # Execute callback in new thread to avoid blocking detection thread
                            threading.Thread(target=self.callback).start()

                except Exception as e:
                    logger.error(f"Wake word detection error: {e}")
                    time.sleep(0.1)

        except Exception as e:
            logger.error(f"Wake word detection thread exception: {e}")
        finally:
            logger.info("Wake word detection thread ended")

    def get_audio_data(self):
        """Get saved audio data"""
        frames = []

        # Get all data from queue
        while not self.audio_queue.empty():
            try:
                frames.append(self.audio_queue.get_nowait())
                self.audio_queue.task_done()
            except:
                break

        return frames

    def cleanup(self):
        """Clean up resources"""
        # Stop detection
        self.stop_detection()

        # Release Porcupine resources
        if self.porcupine:
            self.porcupine.delete()
            self.porcupine = None

        # Release PyAudio resources
        if self.audio:
            self.audio.terminate()
            self.audio = None

        logger.info("Wake word detector resources cleaned up")
