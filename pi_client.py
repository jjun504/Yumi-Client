import paho.mqtt.client as mqtt
import pyaudio
import opuslib
import numpy as np
import threading
import socket
import json
import time
import os
from loguru import logger
import queue
from queue import Queue
import signal
import sys
import struct
import wave
import argparse
from scipy import signal as scipy_signal

# Import custom modules
from music_player import MusicPlayer, MPV_AVAILABLE
from wake_word_detector import PorcupineWakeWordDetector, PORCUPINE_AVAILABLE

# Device state constants
DEVICE_STATE_IDLE = 'idle'           # Idle state, waiting for wake-up
DEVICE_STATE_LISTENING = 'listening'  # Recording audio
DEVICE_STATE_PROCESSING = 'processing'  # Processing audio
DEVICE_STATE_PLAYING = 'playing'     # Playing music

# Global configuration variable - loaded from config.json
CONFIG = {}

# Configuration file path
CONFIG_FILE_PATH = "config.json"

def load_config_from_file():
    """Load configuration from config file"""
    global CONFIG

    try:
        if os.path.exists(CONFIG_FILE_PATH):
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                CONFIG = json.load(f)
                logger.info(f"Configuration loaded from {CONFIG_FILE_PATH}")
                return True
        else:
            logger.info(f"Configuration file {CONFIG_FILE_PATH} does not exist, creating default configuration")
            # Create default configuration
            CONFIG = {
                # System configuration
                "system": {
                    "device_id": "",
                    "password": "",
                    "user_id": None,
                    "boot_time": None,  # Will be set at runtime
                    "model": "raspberry_pi",
                    "version": "1.0.0",
                    "log_level": "DEBUG",
                    "status": "offline",
                    "last_update": None  # Will be set at runtime
                },

                # Wake word configuration
                "wake_word": {
                    "enabled": True,
                    "api_key": "",  # Get your free API key at console.picovoice.ai
                    "keyword_path": "wakeword_source/your_wake_word_pi.ppn",
                    "sensitivity": 0.5
                },

                # Audio settings
                "audio_settings": {
                    "mic_sample_rate": 48000,  # 麦克风原生采样率
                    "sample_rate": 24000,      # 传输采样率（保持与服务器兼容）
                    "wake_word_sample_rate": 16000,  # 唤醒词检测采样率
                    "channels": 1,
                    "chunk_size": 960,  # 优化为Opus编码器推荐的帧大小
                    "mic_chunk_size": 1920,  # 麦克风块大小（48000Hz对应的块大小）
                    "format": "int16",
                    "general_volume": 50,
                    "music_volume": 50,
                    "notification_volume": 50,
                    "wake_sound_path": "sound/pvwake.wav"
                },

                # MQTT configuration
                "mqtt": {
                    "broker": "broker.emqx.io",
                    "port": 1883,
                    "username": None,
                    "password": None,
                    "client_id_prefix": "smart_assistant_87",
                    "topic_prefix": "smart0337187"
                },

                # Network configuration
                "network": {
                    "server_ip": None,      # Will be set through discovery service or manually
                    "server_udp_port": 8884,        # Audio transmission port
                    "server_udp_receive_port": 8885, # Audio reception port
                    "stt_bridge_ip": None,   # STT bridge processor IP address
                    "stt_bridge_port": 8884, # STT bridge processor port
                    "discovery_port": 50000,        # Discovery service port
                    "discovery_request": "DISCOVER_SERVER_REQUEST",
                    "discovery_response_prefix": "DISCOVER_SERVER_RESPONSE_",
                    "stt_mode": False  # Whether to enable STT bridge mode
                },

                # Recording configuration
                "recording": {
                    "auto_stop": True,
                    "timeout": 15.0,  # Seconds, maximum recording duration
                    "silence_threshold": 300,   # Silence energy threshold
                    "initial_silence_duration": 3.0,  # Seconds, initial silence duration threshold after wake-up
                    "speech_silence_duration": 1.0,   # Seconds, silence duration threshold after speech
                    "save_path": "recordings"
                },

                # Debug configuration
                "debug": {
                    "enabled": False
                }
            }
            # Save default configuration to file
            save_config_to_file()
            return True
    except Exception as e:
        logger.error(f"Error loading configuration file: {e}")
        return False

def save_config_to_file():
    """Save configuration to file"""
    try:
        # Save to file
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, indent=4, ensure_ascii=False)
        logger.info(f"Configuration saved to {CONFIG_FILE_PATH}")
        return True
    except Exception as e:
        logger.error(f"Error saving configuration file: {e}")
        return False

class PiClient:
    def __init__(self, config=None):
        """Initialize Pi client"""
        # Note: Configuration file should already be loaded in main program, no need to reload here

        # Set runtime values (not saved to file)
        CONFIG["system"]["boot_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        CONFIG["system"]["last_update"] = time.time()

        # Use global configuration directly
        self.config = CONFIG

        # Update configuration (if provided)
        if config:
            # Only update device_id
            if "device_id" in config:
                CONFIG["system"]["device_id"] = config["device_id"]

        # Generate derived configuration
        self._generate_derived_config()

        # Initialize instance variables
        self._init_instance_variables()



    def _init_instance_variables(self):
        """Initialize instance variables"""
        # Initialize MQTT client
        self.mqtt_client = None
        self.is_connected = False

        # Initialize UDP socket
        self.udp_socket = None

        # Audio processing
        self.audio = None
        self.encoder = None
        self.decoder = None
        self.mic_stream = None
        self.speaker_stream = None

        # Control flags
        self.recording = False
        self.running = True
        self.recording_thread = None
        self.playback_thread = None

        # Sequence number (for UDP transmission)
        self.sequence_number = 0

        # Device state
        self.device_state = DEVICE_STATE_IDLE

        # Wake word detector
        self.wake_word_detector = None

        # Recording timeout timer
        self.recording_timer = None

        # Music player
        self.music_player = None
        if MPV_AVAILABLE:
            self.music_player = MusicPlayer()
            # Set song completion callback
            self.music_player.set_completion_callback(self._on_song_completed)
        else:
            logger.warning("Music playback functionality unavailable, missing required libraries")

        # Audio packet reception state management (for volume control)
        self.audio_packet_receiving = False
        self.original_music_volume = None
        self.volume_reduced = False
        self.last_audio_packet_time = 0
        self.volume_restore_timer = None

        # Recording state management (for volume control)
        self.recording_volume_reduced = False

        # Create audio save directory (if debug enabled)
        if CONFIG["debug"]["enabled"]:
            os.makedirs(CONFIG["recording"]["save_path"], exist_ok=True)

        # 采样率转换相关变量
        self.mic_sample_rate = CONFIG["audio_settings"]["mic_sample_rate"]
        self.target_sample_rate = CONFIG["audio_settings"]["sample_rate"]
        self.wake_word_sample_rate = CONFIG["audio_settings"]["wake_word_sample_rate"]

        # 计算采样率转换比例
        self.resample_ratio_recording = self.target_sample_rate / self.mic_sample_rate
        self.resample_ratio_wake_word = self.wake_word_sample_rate / self.mic_sample_rate

        # 音频缓冲区（用于采样率转换）
        self.audio_buffer_recording = np.array([], dtype=np.int16)
        self.audio_buffer_wake_word = np.array([], dtype=np.int16)

        logger.info(f"音频配置: 麦克风采样率={self.mic_sample_rate}Hz, 录音采样率={self.target_sample_rate}Hz, 唤醒词采样率={self.wake_word_sample_rate}Hz")

    def _resample_audio(self, audio_data, original_rate, target_rate):
        """
        使用scipy进行音频重采样

        Args:
            audio_data: 原始音频数据 (numpy array)
            original_rate: 原始采样率
            target_rate: 目标采样率

        Returns:
            重采样后的音频数据 (numpy array)
        """
        try:
            if original_rate == target_rate:
                return audio_data

            # 计算重采样比例
            num_samples = int(len(audio_data) * target_rate / original_rate)

            # 使用scipy的resample函数进行重采样
            resampled = scipy_signal.resample(audio_data, num_samples)

            # 确保数据类型为int16
            resampled = np.clip(resampled, -32768, 32767).astype(np.int16)

            return resampled

        except Exception as e:
            logger.error(f"音频重采样失败: {e}")
            return audio_data

    def _process_mic_audio_for_recording(self, audio_data):
        """
        处理麦克风音频数据用于录音传输
        将48kHz音频转换为24kHz

        Args:
            audio_data: 原始音频数据 (bytes)

        Returns:
            处理后的音频数据 (bytes)，如果没有足够数据则返回None
        """
        try:
            # 将字节转换为numpy数组
            audio_array = np.frombuffer(audio_data, dtype=np.int16)

            # 添加到缓冲区
            self.audio_buffer_recording = np.concatenate([self.audio_buffer_recording, audio_array])

            # 计算需要多少样本才能产生一个目标块
            target_chunk_size = CONFIG["audio_settings"]["chunk_size"]
            required_samples = int(target_chunk_size / self.resample_ratio_recording)

            if len(self.audio_buffer_recording) >= required_samples:
                # 提取所需的样本
                samples_to_process = self.audio_buffer_recording[:required_samples]
                self.audio_buffer_recording = self.audio_buffer_recording[required_samples:]

                # 重采样
                resampled = self._resample_audio(samples_to_process, self.mic_sample_rate, self.target_sample_rate)

                # 确保输出大小正确
                if len(resampled) != target_chunk_size:
                    # 如果大小不匹配，进行调整
                    if len(resampled) > target_chunk_size:
                        resampled = resampled[:target_chunk_size]
                    else:
                        # 填充零
                        padding = np.zeros(target_chunk_size - len(resampled), dtype=np.int16)
                        resampled = np.concatenate([resampled, padding])

                return resampled.tobytes()

            return None

        except Exception as e:
            logger.error(f"处理录音音频数据失败: {e}")
            return None

    def _generate_derived_config(self):
        """Generate derived configuration"""
        device_id = CONFIG["system"]["device_id"]
        topic_prefix = CONFIG["mqtt"]["topic_prefix"]

        # Add MQTT topics to CONFIG
        CONFIG["command_topic"] = f"{topic_prefix}/client/command/{device_id}"
        CONFIG["audio_topic"] = f"{topic_prefix}/client/audio/{device_id}"
        CONFIG["status_topic"] = f"{topic_prefix}/client/status/{device_id}"
        CONFIG["config_topic"] = f"{topic_prefix}/client/config/{device_id}"

    def _get_ip_address(self):
        """Get device's actual IP address (not 127.0.0.1 or 127.0.1.1)

        Returns:
            str: Device's IP address, returns 127.0.0.1 if unable to obtain
        """
        try:
            # Create a temporary socket connection to external server
            # This uses default route to get correct local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # Connect to Google DNS
            ip = s.getsockname()[0]     # Get local IP
            s.close()
            return ip
        except Exception as e:
            logger.warning(f"Unable to get IP address: {e}, using default value")

            # Try to get IP from all non-loopback interfaces
            try:
                for interface in socket.if_nameindex():
                    ifname = interface[1]
                    if ifname != 'lo':  # Exclude loopback interface
                        try:
                            ip = socket.inet_ntoa(
                                socket.ioctl(
                                    socket.socket(socket.AF_INET, socket.SOCK_DGRAM),
                                    0x8915,  # SIOCGIFADDR
                                    struct.pack('256s', ifname.encode()[:15])
                                )[20:24]
                            )
                            if ip and not ip.startswith('127.'):
                                return ip
                        except:
                            continue
            except:
                pass

            # If all above methods fail, return hostname resolution result
            return socket.gethostbyname(socket.gethostname())

    def initialize(self):
        """Initialize client"""
        # Initialize audio
        self.audio = pyaudio.PyAudio()

        # Initialize Opus encoder and decoder
        self.encoder = opuslib.Encoder(
            CONFIG["audio_settings"]["sample_rate"],
            CONFIG["audio_settings"]["channels"],
            opuslib.APPLICATION_AUDIO
        )

        self.decoder = opuslib.Decoder(
            CONFIG["audio_settings"]["sample_rate"],
            CONFIG["audio_settings"]["channels"]
        )

        # Ensure sound file directory exists
        os.makedirs("sound", exist_ok=True)

        # Check if wake sound file exists
        wake_sound_path = "sound/pvwake.wav"
        if not os.path.exists(wake_sound_path):
            logger.warning(f"Wake sound file does not exist: {wake_sound_path}")
            logger.info("Please ensure pvwake.wav file is placed in sound directory")

        # Initialize UDP socket
        self._setup_udp()

        # Start audio playback thread
        self.playback_thread = threading.Thread(target=self._audio_playback_worker, daemon=True)
        self.playback_thread.start()

        # Initialize wake word detector
        if PORCUPINE_AVAILABLE:
            self.wake_word_detector = PorcupineWakeWordDetector()
            # Create wake word detector configuration
            detector_config = {
                "porcupine_access_key": CONFIG["wake_word"]["api_key"],
                "porcupine_keyword_paths": [CONFIG["wake_word"]["keyword_path"]],
                "porcupine_sensitivity": CONFIG["wake_word"]["sensitivity"],
                "pre_buffer_duration": 0
            }
            if self.wake_word_detector.initialize(detector_config):
                self.wake_word_detector.set_callback(self._on_wake_word_detected)
                self.wake_word_detector.start_detection()

        # Initialize MQTT connection - moved to last, as it automatically sends status and config after successful connection
        self._setup_mqtt()

        logger.info(f"Pi client initialization complete, device ID: {CONFIG['system']['device_id']}")

    def _setup_mqtt(self):
        """Setup MQTT connection - based on dev_control.py"""
        # Generate unique client ID
        mqtt_client_id = f"{CONFIG['mqtt']['client_id_prefix']}_{socket.gethostname()}_{int(time.time())}_{id(threading.current_thread())}"

        # Create MQTT client - using paho-mqtt 1.x style
        self.mqtt_client = mqtt.Client(mqtt_client_id, clean_session=True)
        logger.info(f"Initialize MQTT client {mqtt_client_id} (clean_session=True)")

        # Set username and password (if available)
        if CONFIG["mqtt"]["username"] and CONFIG["mqtt"]["password"]:
            self.mqtt_client.username_pw_set(
                CONFIG["mqtt"]["username"],
                CONFIG["mqtt"]["password"]
            )

        # Set callbacks
        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
        self.mqtt_client.on_message = self._on_mqtt_message

        # Set auto-reconnect
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)

        # Maximum retry count
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # Connect to MQTT broker
                logger.debug(f"Connecting to {CONFIG['mqtt']['broker']}:{CONFIG['mqtt']['port']}... (attempt {retry_count+1}/{max_retries})")
                self.mqtt_client.connect(
                    CONFIG["mqtt"]["broker"],
                    CONFIG["mqtt"]["port"],
                    60  # keepalive 60 seconds
                )

                # Start MQTT loop
                self.mqtt_client.loop_start()
                logger.info("MQTT loop started")

                # Connection successful, break loop
                break

            except Exception as e:
                retry_count += 1
                logger.error(f"MQTT connection failed (attempt {retry_count}/{max_retries}): {e}")

                if retry_count < max_retries:
                    # Wait before retry
                    retry_delay = 2 ** retry_count  # Exponential backoff: 2, 4, 8... seconds
                    logger.info(f"Will retry connection in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"MQTT connection failed, reached maximum retry count ({max_retries})")
                    # After last attempt fails, still start loop for subsequent auto-reconnect
                    self.mqtt_client.loop_start()

    def _setup_udp(self):
        """Setup UDP socket"""
        try:
            # Create UDP socket
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            # Set buffer size to improve performance
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)  # 256KB

            # Output connection information
            if CONFIG["network"]["stt_mode"] and CONFIG["network"]["stt_bridge_ip"]:
                logger.info(f"UDP socket created, STT bridge mode, target: {CONFIG['network']['stt_bridge_ip']}:{CONFIG['network']['stt_bridge_port']}")
            elif CONFIG["network"]["server_ip"]:
                logger.info(f"UDP socket created, server mode, target: {CONFIG['network']['server_ip']}:{CONFIG['network']['server_udp_port']}")
            else:
                logger.info("UDP socket created, waiting for server discovery")
        except Exception as e:
            logger.error(f"Failed to create UDP socket: {e}")

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connection callback"""
        if rc == 0:
            logger.info("Connected to MQTT broker")
            self.is_connected = True

            # Subscribe to command topic
            command_topic = f"{CONFIG['mqtt']['topic_prefix']}/server/command/{CONFIG['system']['device_id']}"
            client.subscribe(command_topic, qos=2)
            logger.info(f"Subscribed to command topic: {command_topic}")

            # Subscribe to configuration topic to receive configuration updates from server
            config_topic = f"{CONFIG['mqtt']['topic_prefix']}/server/config/{CONFIG['system']['device_id']}"
            client.subscribe(config_topic, qos=2)
            logger.info(f"Subscribed to configuration topic: {config_topic}")

            # Update status (in memory only)
            CONFIG["system"]["status"] = "online"

            # Send online status
            self._publish_status("online")
            time.sleep(1)

            # Send device configuration
            self._publish_config()

            logger.info("MQTT initialization complete")
        else:
            logger.error(f"MQTT connection failed, return code: {rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """MQTT disconnect callback"""
        logger.warning(f"Disconnected from MQTT broker, return code: {rc}")

        self.is_connected = False

        # If unexpected disconnect, try to reconnect
        if rc != 0:
            logger.info("MQTT connection unexpectedly disconnected, will automatically attempt to reconnect...")

            # Update client ID to ensure uniqueness
            new_client_id = f"{CONFIG['mqtt']['client_id_prefix']}_{socket.gethostname()}_{int(time.time())}_{id(threading.current_thread())}"
            logger.info(f"Generated new client ID: {new_client_id}")

            # Client will automatically try to reconnect because we used loop_start()
            # If manual reconnection is needed, uncomment below
            # try:
            #     # Stop current loop
            #     client.loop_stop()
            #     # Create new client instance
            #     self.mqtt_client = mqtt.Client(new_client_id)
            #     # Set callbacks
            #     self.mqtt_client.on_connect = self._on_mqtt_connect
            #     self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
            #     self.mqtt_client.on_message = self._on_mqtt_message
            #     # Reconnect
            #     self.mqtt_client.connect(self.config["mqtt_broker"], self.config["mqtt_port"], 60)
            #     self.mqtt_client.loop_start()
            #     logger.info("Attempted to reconnect MQTT")
            # except Exception as e:
            #     logger.error(f"Failed to reconnect MQTT: {e}")
        else:
            logger.info("MQTT connection normally disconnected")

    def _on_mqtt_message(self, client, userdata, msg):
        """MQTT message callback"""
        try:
            # Parse message
            payload = msg.payload.decode()
            logger.debug(f"Received MQTT message: {msg.topic}")

            # Get device ID and topic prefix
            if msg.topic == f"{CONFIG['mqtt']['topic_prefix']}/server/command/{CONFIG['system']['device_id']}":
                logger.debug("Processing command message")
                self._handle_command(payload)

            # Handle configuration updates
            elif msg.topic == f"{CONFIG['mqtt']['topic_prefix']}/server/config/{CONFIG['system']['device_id']}":
                logger.debug("Processing configuration update message")
                self._handle_config_update(payload)

        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _publish_config(self):
        """Publish device configuration"""
        try:
            # Build configuration topic
            config_topic = f"{CONFIG['mqtt']['topic_prefix']}/client/config/{CONFIG['system']['device_id']}"

            # Create EMBEDDED_CONFIG only when publishing
            embedded_config = {
                "system": dict(CONFIG["system"]),
                "wake_word": {
                    "enabled": CONFIG["wake_word"]["enabled"],
                },
                "audio_settings": {
                    "general_volume": CONFIG["audio_settings"]["general_volume"],
                    "music_volume": CONFIG["audio_settings"]["music_volume"],
                    "notification_volume": CONFIG["audio_settings"]["notification_volume"],
                },
                "mqtt": dict(CONFIG["mqtt"]),
            }

            # Build configuration message containing device_id and complete configuration
            config_message = {
                'device_id': CONFIG["system"]["device_id"],
                'config': embedded_config,
                'timestamp': time.time()
            }

            # Publish configuration
            result = self.mqtt_client.publish(
                config_topic,
                json.dumps(config_message),
                qos=2,
                retain=False
            )

            if result.rc == 0:
                logger.info(f"Device configuration sent to topic: {config_topic}")
                return True
            else:
                logger.error(f"Failed to send device configuration, error code: {result.rc}")
                return False

        except Exception as e:
            logger.error(f"Failed to publish device configuration: {e}")
            return False

    def _publish_status(self, status):
        """Publish status information"""
        if not self.is_connected:
            logger.warning("MQTT not connected, cannot publish status")
            return

        try:
            # Create status message
            message = {
                "device_id": CONFIG["system"]["device_id"],
                "password": CONFIG["system"]["password"],
                "user_id": CONFIG["system"]["user_id"],
                "ip": self._get_ip_address(),  # Use more reliable method to get IP
                "model": CONFIG["system"]["model"],
                "timestamp": time.time(),
                "status": status
            }

            # Publish message
            result = self.mqtt_client.publish(
                CONFIG["status_topic"],
                json.dumps(message),
                qos=2,
                retain=False
            )

            if result.rc == 0:
                logger.debug(f"Published status: {status}")
            else:
                logger.error(f"Failed to publish status, error code: {result.rc}")

        except Exception as e:
            logger.error(f"Error publishing status: {e}")

    def _handle_config_update(self, payload):
        """Handle configuration update message"""
        try:
            # Parse configuration message
            message = json.loads(payload)

            # Check message format
            if not isinstance(message, dict):
                logger.warning("Configuration message is not a valid JSON object")
                return

            # Check device ID (if present)
            device_id = message.get("device_id")
            if device_id and device_id != CONFIG["system"]["device_id"]:
                logger.warning(f"Received configuration for other device: {device_id}")
                return

            # Check timestamp
            timestamp = message.get("timestamp")
            if timestamp:
                logger.info(f"Configuration timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))}")

            config_changed = False

            # Handle partial configuration update format from server: {config: "section.key", new_value: value}
            if "config" in message and "new_value" in message:
                path = message["config"]
                value = message["new_value"]

                logger.info(f"Received partial configuration update: {path} = {value}")

                # Update configuration
                if self._set_config_value(path, value):
                    config_changed = True
                    logger.info(f"Updated configuration: {path} = {value}")

                    # Special handling for certain configuration items
                    if path == "wake_word.enabled":
                        logger.info(f"Wake word status changed to: {value}")
                    elif path.startswith("audio_settings."):
                        volume_type = path.split('.')[-1]
                        logger.info(f"Volume setting changed: {volume_type} = {value}")
                    elif path.startswith("system."):
                        system_setting = path.split('.')[-1]
                        logger.info(f"System setting changed: {system_setting} = {value}")

            # Handle complete configuration update format: {config: {section: {key: value}}}
            elif "config" in message and isinstance(message["config"], dict):
                config_data = message["config"]
                logger.info(f"Received complete configuration update: {config_data}")

                # Iterate through configuration data
                for section, values in config_data.items():
                    if isinstance(values, dict):
                        for key, value in values.items():
                            # Update configuration
                            path = f"{section}.{key}"
                            if self._set_config_value(path, value):
                                config_changed = True
                                logger.info(f"Updated configuration item: {path} = {value}")
            else:
                logger.warning("Unrecognized configuration message format")

            # If configuration changed, update timestamp and apply changes
            if config_changed:
                # Update last modification time
                CONFIG["system"]["last_update"] = time.time()

                # Apply configuration changes
                self._apply_config_changes()

                # Save configuration to file
                save_config_to_file()

                # # Send confirmation message
                # self._publish_config_update_ack(message)

                logger.info("Configuration update completed and applied")
            else:
                logger.info("No configuration changes")

        except json.JSONDecodeError:
            logger.error("Configuration message is not valid JSON format")
        except Exception as e:
            logger.error(f"Error handling configuration update: {e}")

    # def _publish_config_update_ack(self, original_message):
    #     """发送配置更新确认消息"""
    #     if not self.is_connected:
    #         logger.warning("MQTT未连接，无法发送配置更新确认")
    #         return

    #     try:
    #         # 构建确认消息主题
    #         ack_topic = f"{self.config['topic_prefix']}/client/config_ack/{self.config['device_id']}"

    #         # 构建确认消息
    #         ack_message = {
    #             "device_id": self.config["device_id"],
    #             "timestamp": time.time(),
    #             "status": "success",
    #             "original_config": original_message.get("config"),
    #             "original_value": original_message.get("new_value", None)
    #         }

    #         # 发布确认消息
    #         result = self.mqtt_client.publish(
    #             ack_topic,
    #             json.dumps(ack_message),
    #             qos=1
    #         )

    #         if result.rc == 0:
    #             logger.debug("已发送配置更新确认")
    #         else:
    #             logger.error(f"发送配置更新确认失败，错误码: {result.rc}")

    #     except Exception as e:
    #         logger.error(f"发送配置更新确认时出错: {e}")

    def _set_config_value(self, path, value):
        """Set configuration item

        Supports dot-separated paths, e.g. "system.language"

        Args:
            path: Configuration item path
            value: Value to set

        Returns:
            bool: Whether setting was successful
        """
        # Handle special configuration path mapping
        path_mapping = {
            "audio_settings.general_volume": "audio_settings.general_volume",
            "audio_settings.music_volume": "audio_settings.music_volume",
            "audio_settings.notification_volume": "audio_settings.notification_volume",
            "wake_word.enabled": "wake_word.enabled",
            "system.password": "system.password",
            "system.user_id": "system.user_id"
        }

        # Check if path mapping is needed
        if path in path_mapping:
            path = path_mapping[path]

        parts = path.split('.')
        current = CONFIG

        try:
            # Traverse path until second-to-last part
            for i, part in enumerate(parts[:-1]):
                if part not in current:
                    # If path doesn't exist, create new dictionary
                    current[part] = {}
                    logger.info(f"Created new configuration node: {part}")
                elif not isinstance(current[part], dict):
                    # If path exists but is not dictionary, replace with dictionary
                    logger.warning(f"Configuration node {part} is not a dictionary, will be replaced")
                    current[part] = {}

                current = current[part]

            # Set value for last part
            last_part = parts[-1]

            # Check if value needs conversion
            if isinstance(value, str) and last_part in ["enabled"]:
                # Convert string "true"/"false" to boolean
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False

            # Check if value has changed
            if last_part in current and current[last_part] == value:
                logger.info(f"Configuration item {path} value unchanged: {value}")
                return False

            # Update configuration value
            old_value = current.get(last_part, "not set")
            current[last_part] = value
            logger.info(f"Updated configuration item {path}: {old_value} -> {value}")
            return True

        except Exception as e:
            logger.error(f"Failed to set configuration item {path}: {e}")
            return False

    def _apply_config_changes(self):
        """Apply configuration changes, handle configuration items that need special operations"""
        try:
            # Handle wake word configuration
            if "wake_word" in CONFIG:
                wake_word_config = CONFIG["wake_word"]

                # Check if wake word should be enabled/disabled
                if "enabled" in wake_word_config:
                    enabled = wake_word_config["enabled"]
                    if enabled and not self.wake_word_detector and PORCUPINE_AVAILABLE:
                        # Enable wake word
                        logger.info("Enabling wake word detection")
                        self.wake_word_detector = PorcupineWakeWordDetector()

                        # Create wake word detector configuration
                        detector_config = {
                            "porcupine_access_key": CONFIG["wake_word"]["api_key"],
                            "porcupine_keyword_paths": [CONFIG["wake_word"]["keyword_path"]],  # Ensure it's a list
                            "porcupine_sensitivity": wake_word_config.get("sensitivity", 0.5),
                            "pre_buffer_duration": CONFIG.get("recording", {}).get("pre_buffer_duration", 0)
                        }

                        if self.wake_word_detector.initialize(detector_config):
                            self.wake_word_detector.set_callback(self._on_wake_word_detected)
                            self.wake_word_detector.start_detection()
                    elif not enabled and self.wake_word_detector:
                        # Disable wake word
                        logger.info("Disabling wake word detection")
                        self.wake_word_detector.cleanup()
                        self.wake_word_detector = None

            # Handle audio settings
            if "audio_settings" in CONFIG:
                audio_settings = CONFIG["audio_settings"]
                logger.info(f"Applying audio settings: {audio_settings}")

                # Volume settings will be automatically applied when playing audio, no additional handling needed

            # Handle system settings
            if "system" in CONFIG:
                system_settings = CONFIG["system"]
                logger.info(f"Applying system settings: {system_settings}")

                # Device ID is already updated in CONFIG, no additional handling needed
                logger.info(f"Device ID: {system_settings['device_id']}")

            # Ensure debug directory exists
            if CONFIG.get("debug", {}).get("enabled", False):
                audio_save_path = CONFIG.get("recording", {}).get("save_path", "recordings")
                os.makedirs(audio_save_path, exist_ok=True)

        except Exception as e:
            logger.error(f"Error applying configuration changes: {e}")

    def _handle_command(self, payload):
        """Handle received commands"""
        try:
            command = json.loads(payload)
            cmd_type = command.get("type")

            if cmd_type == "record":
                # Start recording
                self.start_recording()

            elif cmd_type == "stop_record":
                # Stop recording
                self.stop_recording()

            elif cmd_type == "play":
                # Play audio
                audio_data = command.get("data")
                if audio_data:
                    # Decode Base64 encoded audio data and play
                    import base64
                    audio_bytes = base64.b64decode(audio_data)
                    self._play_audio(audio_bytes)

            elif cmd_type == "play_music":
                # Play music (YouTube link)
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("Music playback functionality unavailable")
                    return

                # Get YouTube link and volume
                youtube_url = command.get("url")
                volume = command.get("volume", CONFIG["audio_settings"]["music_volume"])

                if not youtube_url:
                    logger.error("Missing YouTube link")
                    return

                # If music is currently playing, stop it first
                if self.music_player.is_playing or self.music_player.player:
                    logger.info("Stopping current playback to start new playback")
                    self.music_player.stop_playback()
                    # Brief delay to ensure resources are released
                    time.sleep(0.5)

                # Update device state
                self.device_state = DEVICE_STATE_PLAYING

                # Play music
                logger.info(f"Starting music playback: {youtube_url}, volume: {volume}")
                success = self.music_player.play_url(youtube_url, volume)

                # Send playback status
                if success:
                    self._publish_music_status("playing", self.music_player.current_title)
                else:
                    self._publish_music_status("error", "Playback failed")
                    self.device_state = DEVICE_STATE_IDLE

            elif cmd_type == "stop_music":
                # Stop music playback
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("Music playback functionality unavailable")
                    return

                # Stop playback
                self.music_player.stop_playback()

                # Update device state
                self.device_state = DEVICE_STATE_IDLE

                # Send stop status
                self._publish_music_status("stopped")

            elif cmd_type == "pause_music":
                # Pause music playback
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("Music playback functionality unavailable")
                    return

                # Check if currently playing
                if not self.music_player.is_playing:
                    logger.warning("No music currently playing, cannot pause")
                    return

                # Pause playback
                success = self.music_player.pause_playback()

                # Send pause status
                if success:
                    self._publish_music_status("paused")
                    logger.info("Music playback paused")

            elif cmd_type == "resume_music":
                # Resume music playback
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("Music playback functionality unavailable")
                    return

                # Check if player instance exists
                if not self.music_player.player:
                    logger.warning("No active player, cannot resume playback")
                    return

                # Get current title (before resuming)
                current_title = self.music_player.current_title
                logger.info(f"Title before resume: {current_title if current_title else 'Unknown title'}")

                # Resume playback
                success = self.music_player.resume_playback()

                # Update device state
                if success:
                    self.device_state = DEVICE_STATE_PLAYING

                    # Get title again (may be updated during resume process)
                    title = self.music_player.current_title

                    # If title is still empty but we had a title before, use the previous title
                    if not title and current_title:
                        self.music_player.current_title = current_title
                        logger.info(f"Manually restored title information: {current_title}")

                    # Publish status update
                    self._publish_music_status("playing", self.music_player.current_title)
                    logger.info(f"Music playback resumed: {self.music_player.current_title if self.music_player.current_title else 'Unknown title'}")

            elif cmd_type == "set_volume":
                # Set volume
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("Music playback functionality unavailable")
                    return

                # Get volume
                volume = command.get("volume")
                if volume is None:
                    logger.error("Missing volume parameter")
                    return

                # Set volume
                success = self.music_player.set_volume(volume)

                # Update configuration
                if success:
                    CONFIG["audio_settings"]["music_volume"] = volume
                    logger.info(f"Music volume set to: {volume}")

                    # Send volume status
                    self._publish_music_status("volume_changed", volume=volume)

            elif cmd_type == "set_server":
                # Set server IP and port
                server_ip = command.get("server_ip")
                server_port = command.get("server_port")
                if server_ip:
                    CONFIG["network"]["server_ip"] = server_ip
                    logger.info(f"Server IP set to: {server_ip}")
                if server_port:
                    CONFIG["network"]["server_udp_port"] = server_port
                    logger.info(f"Server UDP port set to: {server_port}")

            elif cmd_type == "ping":
                # Heartbeat detection
                self._publish_status("online")

            else:
                logger.warning(f"Unknown command type: {cmd_type}")

        except json.JSONDecodeError:
            logger.error("Invalid JSON format")
        except Exception as e:
            logger.error(f"Error handling command: {e}")


    def _on_song_completed(self, finished_title):
        """Song completion callback function"""
        try:
            logger.info(f"Song playback completed: {finished_title if finished_title else 'Unknown title'}")

            # Update device state
            self.device_state = DEVICE_STATE_IDLE

            # Publish song completion status
            self._publish_music_status("completed", finished_title)

            # Send request for next song to server
            self._request_next_song()

        except Exception as e:
            logger.error(f"Error handling song completion callback: {e}")

    def _request_next_song(self):
        """Request server to play next song"""
        try:
            if not self.is_connected:
                logger.warning("MQTT not connected, cannot request next song")
                return

            # Build request message for next song
            request_data = {
                "type": "request_next_song",
                "device_id": CONFIG["system"]["device_id"],
                "timestamp": time.time()
            }

            # Send to server command topic
            topic = f"{CONFIG['mqtt']['topic_prefix']}/client/request/{CONFIG['system']['device_id']}"
            message = json.dumps(request_data, ensure_ascii=False)

            result = self.mqtt_client.publish(topic, message, qos=1)
            if result.rc == 0:
                logger.debug("Next song request sent")
            else:
                logger.error(f"Failed to send next song request, return code: {result.rc}")

        except Exception as e:
            logger.error(f"Error requesting next song: {e}")


    def _publish_music_status(self, status, title=None, volume=None):
        """Publish music playback status"""
        if not self.is_connected:
            logger.warning("MQTT not connected, cannot publish music status")
            return

        try:
            # Build status message topic
            music_status_topic = f"{CONFIG['mqtt']['topic_prefix']}/client/music_status/{CONFIG['system']['device_id']}"

            # Build status message
            message = {
                "device_id": CONFIG["system"]["device_id"],
                "timestamp": time.time(),
                "status": status
            }

            # Add optional fields
            if title:
                message["title"] = title
            if volume is not None:
                message["volume"] = volume

            # Add current playback information (regardless of whether playing)
            if self.music_player and self.music_player.player:
                player_status = self.music_player.get_status()
                message["player_status"] = player_status

            # Publish message
            result = self.mqtt_client.publish(
                music_status_topic,
                json.dumps(message),
                qos=1
            )

            if result.rc == 0:
                logger.debug(f"Published music status: {status}")
            else:
                logger.error(f"Failed to publish music status, error code: {result.rc}")

        except Exception as e:
            logger.error(f"Error publishing music status: {e}")

    def _reduce_music_volume(self, reason="unknown"):
        """Reduce music volume (generic method)

        Args:
            reason: Reason for reducing volume, for logging
        """
        if not self.music_player or not self.music_player.is_playing:
            return

        try:
            # Get current volume
            current_volume = self.music_player.player.volume if self.music_player.player else None
            if current_volume is None:
                return

            # If volume hasn't been reduced yet, save original volume and reduce
            if not self.volume_reduced:
                self.original_music_volume = current_volume
                target_volume = min(current_volume, 40)

                # Only reduce if target volume is less than current volume
                if target_volume < current_volume:
                    self.music_player.set_volume(target_volume)
                    self.volume_reduced = True
                    logger.info(f"{reason}, music volume reduced from {current_volume} to {target_volume}")

        except Exception as e:
            logger.error(f"Error reducing music volume: {e}")

    def _reduce_music_volume_for_audio_packet(self):
        """Reduce music volume when receiving audio packets"""
        self._reduce_music_volume("Audio packet reception")

    def _reduce_music_volume_for_recording(self):
        """Reduce music volume when starting recording"""
        self._reduce_music_volume("Recording in progress")

    def _restore_music_volume(self, reason="unknown"):
        """Restore music volume

        Args:
            reason: Reason for restoring volume, for logging
        """
        if not self.music_player or not self.volume_reduced or self.original_music_volume is None:
            return

        try:
            self.music_player.set_volume(self.original_music_volume)
            logger.info(f"{reason}, music volume restored to {self.original_music_volume}")
            self.volume_reduced = False
            self.original_music_volume = None

        except Exception as e:
            logger.error(f"Error restoring music volume: {e}")

    def _schedule_volume_restore(self, reason="Audio packet reception ended"):
        """Schedule volume restoration (delayed execution)

        Args:
            reason: Reason for restoring volume, for logging
        """
        # Cancel previous timer
        if self.volume_restore_timer:
            self.volume_restore_timer.cancel()

        # Set new timer to restore volume after 2 seconds
        self.volume_restore_timer = threading.Timer(2.0, lambda: self._restore_music_volume(reason))
        self.volume_restore_timer.start()

    def start_recording(self):
        """Start recording"""
        if self.recording:
            logger.warning("Recording already in progress")
            return

        # Set recording flag
        self.recording = True

        # Reduce music volume (if music is playing)
        self._reduce_music_volume_for_recording()
        self.recording_volume_reduced = True

        # Publish recording status
        self._publish_status("recording")

        # Start recording thread
        self.recording_thread = threading.Thread(target=self._record_audio_worker)
        self.recording_thread.daemon = True
        self.recording_thread.start()

        logger.info("Started recording")

    def stop_recording(self):
        """Stop recording"""
        if not self.recording:
            logger.warning("No recording in progress")
            return

        # Clear recording flag
        self.recording = False

        # Restore music volume (if volume was reduced due to recording)
        if self.recording_volume_reduced:
            # Check if there are other reasons to keep volume low (e.g., receiving audio packets)
            if not self.audio_packet_receiving:
                self._restore_music_volume("Recording ended")
            self.recording_volume_reduced = False

        # Publish online status
        self._publish_status("online")

        logger.info("Stopped recording")

    def _calculate_energy(self, audio_data):
        """Calculate energy of audio data"""
        try:
            # Convert byte data to short integer array
            as_ints = np.frombuffer(audio_data, dtype=np.int16)

            # Check if there's valid data
            if len(as_ints) == 0:
                return 0.0

            # Use float64 to avoid overflow and ensure positive values
            squared = np.square(as_ints.astype(np.float64))
            mean_squared = np.mean(squared)

            # Prevent negative or zero values
            if mean_squared <= 0:
                return 0.0

            # Calculate root mean square energy
            return np.sqrt(mean_squared)

        except Exception as e:
            logger.error(f"Error calculating energy: {e}")
            return 0.0

    def _record_audio_worker(self):
        """录音工作线程 - 支持48kHz麦克风采样率转换"""
        try:
            # Open microphone stream
            self.mic_stream = self.audio.open(
                format=pyaudio.paInt16,
                input_device_index=2,
                channels=CONFIG["audio_settings"]["channels"],
                rate=CONFIG["audio_settings"]["mic_sample_rate"],  # 使用48kHz
                input=True,
                frames_per_buffer=CONFIG["audio_settings"]["mic_chunk_size"]  # 使用更大的块大小
            )

            logger.info(f"麦克风已打开，采样率: {CONFIG['audio_settings']['mic_sample_rate']}Hz, 通道数: {CONFIG['audio_settings']['channels']}")

            # Silence detection variables
            silence_start = None
            max_recording_time = time.time() + CONFIG["recording"]["timeout"]

            # Recording state tracking
            speech_detected = False  # Whether speech has been detected
            recording_start_time = time.time()  # Recording start time

            # Recording loop
            while self.recording:
                # Check maximum recording duration
                if time.time() > max_recording_time:
                    logger.info("Reached maximum recording duration, stopping recording")
                    break

                # 读取音频数据（48kHz）
                raw_audio_data = self.mic_stream.read(CONFIG["audio_settings"]["mic_chunk_size"], exception_on_overflow=False)

                # 处理音频数据用于录音传输（48kHz -> 24kHz）
                processed_audio_data = self._process_mic_audio_for_recording(raw_audio_data)

                if processed_audio_data is None:
                    # 缓冲区中数据不足，继续读取
                    continue

                # 计算音频能量（使用处理后的数据）
                energy = self._calculate_energy(processed_audio_data)

                # Detect if there's speech
                if energy >= CONFIG["recording"]["silence_threshold"]:
                    # Speech detected
                    if not speech_detected:
                        speech_detected = True
                        logger.info(f"Speech start detected, energy: {energy:.2f}")

                    # Reset silence timer
                    silence_start = None
                else:
                    # Silence detected
                    if silence_start is None:
                        silence_start = time.time()
                        logger.debug(f"Silence start detected, energy: {energy:.2f}")
                    else:
                        # Calculate silence duration
                        silence_duration = time.time() - silence_start

                        # Choose different silence thresholds based on whether speech has been detected
                        if speech_detected:
                            # Speech detected, use shorter silence threshold
                            silence_threshold = CONFIG["recording"]["speech_silence_duration"]
                            threshold_name = "speech_silence_duration"
                        else:
                            # No speech detected, use longer initial silence threshold
                            silence_threshold = CONFIG["recording"]["initial_silence_duration"]
                            threshold_name = "initial_silence_duration"

                        # Check if silence threshold exceeded
                        if silence_duration > silence_threshold:
                            logger.info(f"Silence detected for {silence_duration:.2f} seconds, exceeding {threshold_name} ({silence_threshold}s), stopping recording")
                            break

                # 使用Opus编码（编码24kHz的数据）
                encoded_data = self.encoder.encode(processed_audio_data, CONFIG["audio_settings"]["chunk_size"])

                # Send via UDP
                self._send_audio_udp(encoded_data)

                # Optional: Save raw audio data (for debugging)
                if CONFIG["debug"]["enabled"]:
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    filename = f"{CONFIG['recording']['save_path']}/{timestamp}_{self.sequence_number}.raw"
                    with open(filename, 'wb') as f:
                        f.write(processed_audio_data)  # 保存处理后的数据

        except Exception as e:
            logger.error(f"Error during recording: {e}")
        finally:
            # Close microphone stream
            if self.mic_stream:
                self.mic_stream.stop_stream()
                self.mic_stream.close()
                self.mic_stream = None

            self.recording = False
            self._publish_status("online")
            logger.info("Recording thread ended")

    def _send_audio_udp(self, encoded_data):
        """Send encoded audio data via UDP

        Args:
            encoded_data: Encoded audio data
        """
        if not self.udp_socket:
            return

        try:
            # Increment sequence number
            self.sequence_number += 1

            # Create header (containing sequence number, data length and flag bits)
            header = self.sequence_number.to_bytes(4, byteorder='big') + len(encoded_data).to_bytes(2, byteorder='big')

            # Combine packet
            packet = header + encoded_data

            # Choose send target based on mode
            if CONFIG["network"]["stt_mode"] and CONFIG["network"]["stt_bridge_ip"]:
                # STT bridge mode: send to STT bridge processor
                self.udp_socket.sendto(packet, (CONFIG["network"]["stt_bridge_ip"], CONFIG["network"]["stt_bridge_port"]))
                if CONFIG["debug"]["enabled"] and self.sequence_number % 100 == 0:
                    logger.debug(f"Audio packet sent to STT bridge processor, sequence: {self.sequence_number}, size: {len(packet)} bytes")
            elif CONFIG["network"]["server_ip"]:
                # Normal mode: send to server
                self.udp_socket.sendto(packet, (CONFIG["network"]["server_ip"], CONFIG["network"]["server_udp_port"]))
                if CONFIG["debug"]["enabled"] and self.sequence_number % 100 == 0:
                    logger.debug(f"Audio packet sent to server, sequence: {self.sequence_number}, size: {len(packet)} bytes")
            else:
                logger.warning("Server IP or STT bridge processor IP not set, cannot send audio data")
                return

        except Exception as e:
            logger.error(f"Error sending UDP audio data: {e}")

    def _play_audio(self, audio_bytes):
        """Play audio data (for MQTT received audio)"""
        try:
            # Convert bytes to NumPy array
            audio_data = np.frombuffer(audio_bytes, dtype=np.int16)

            # Adjust audio data based on volume settings
            try:
                # Get current volume setting
                volume_percent = CONFIG["audio_settings"]["general_volume"] / 100.0

                # Adjust volume
                if volume_percent != 0.5:  # If not default volume (50%)
                    # Use float64 for calculation to avoid overflow, then convert back to int16
                    audio_data = np.clip(
                        audio_data.astype(np.float64) * (volume_percent * 2),
                        -32768, 32767  # int16 range
                    ).astype(np.int16)

                    logger.debug(f"Audio volume adjusted: {int(volume_percent * 100)}%")
            except Exception as e:
                logger.error(f"Volume adjustment failed: {e}")

            # Open temporary speaker stream
            speaker = self.audio.open(
                format=pyaudio.paInt16,
                channels=CONFIG["audio_settings"]["channels"],
                rate=CONFIG["audio_settings"]["sample_rate"],
                output=True
            )

            # Play audio
            speaker.write(audio_data.tobytes())

            # Close speaker stream
            speaker.stop_stream()
            speaker.close()

            logger.info("Audio playback completed")

        except Exception as e:
            logger.error(f"Error playing audio: {e}")

    def _play_wav_file(self, filename):
        """Play WAV file (lightweight implementation for short notification sounds)"""
        try:
            # Check if file exists
            if not os.path.exists(filename):
                logger.error(f"WAV file does not exist: {filename}")
                return False

            # Open WAV file
            with wave.open(filename, 'rb') as wf:
                # Get WAV file parameters
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()

                # Get current volume setting
                volume_percent = CONFIG["audio_settings"]["general_volume"] / 100.0

                logger.debug(f"Playing WAV file: {filename}, sample rate: {sample_rate}Hz, channels: {channels}, volume: {int(volume_percent * 100)}%")

                # Open temporary speaker stream
                speaker = self.audio.open(
                    format=self.audio.get_format_from_width(sample_width),
                    channels=channels,
                    rate=sample_rate,
                    output=True
                )

                # Read all data
                all_data = wf.readframes(wf.getnframes())

                # Adjust volume if needed
                if volume_percent != 0.5:  # If not default volume (50%)
                    try:
                        # Convert bytes to NumPy array
                        audio_data = np.frombuffer(all_data, dtype=np.int16)

                        # Adjust volume
                        audio_data = np.clip(
                            audio_data.astype(np.float64) * (volume_percent * 2),
                            -32768, 32767  # int16 range
                        ).astype(np.int16)

                        # Convert back to bytes
                        all_data = audio_data.tobytes()
                    except Exception as e:
                        logger.error(f"Failed to adjust WAV file volume: {e}")

                # Play data
                speaker.write(all_data)

                # Close speaker stream
                speaker.stop_stream()
                speaker.close()

                logger.debug(f"WAV file playback completed: {filename}")
                return True

        except Exception as e:
            logger.error(f"Error playing WAV file: {filename}, {e}")
            return False

    def _audio_playback_worker(self):
        """Audio playback worker thread - optimized based on xiaozhi-esp-1.6.0"""
        try:
            # Create UDP receive socket
            recv_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            recv_socket.bind(('0.0.0.0', CONFIG["network"]["server_udp_receive_port"]))  # Use dedicated audio receive port
            recv_socket.settimeout(0.5)  # Set timeout to check running flag

            # Set receive buffer size to improve performance
            recv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)  # 1MB

            logger.info(f"UDP receive socket bound to port {CONFIG['network']['server_udp_receive_port']}")

            # Audio packet queue - based on xiaozhi-esp-1.6.0 implementation
            # Maximum queue length = 1000ms / frame duration (increased to 1 second buffer)
            frame_duration_ms = 60  # Assume 60ms per frame
            max_queue_size = int(1000 / frame_duration_ms)
            audio_queue = Queue(maxsize=200)  # Increase queue size to 200 for larger buffer

            # Sequence number tracking
            expected_seq_num = None
            last_packet_time = time.time()

            # Open speaker stream (on demand)
            speaker_stream = None

            # Create decode thread
            decode_thread_running = True

            def decode_and_play_worker():
                """Decode and playback worker thread - separate decode and playback process"""
                nonlocal speaker_stream

                while decode_thread_running:
                    try:
                        # Get audio packet from queue, wait up to 0.5 seconds
                        try:
                            packet_data = audio_queue.get(timeout=0.5)
                        except queue.Empty:
                            continue

                        # Parse data
                        seq_num, is_raw_pcm, encoded_data = packet_data

                        # Handle based on data type
                        if is_raw_pcm:
                            # Raw PCM data, no decoding needed
                            decoded_data = encoded_data
                        else:
                            # Opus encoded data, needs decoding
                            try:
                                decoded_data = self.decoder.decode(encoded_data, CONFIG["audio_settings"]["chunk_size"])
                            except Exception as e:
                                logger.error(f"Failed to decode audio data, sequence: {seq_num}, error: {e}")
                                audio_queue.task_done()
                                continue

                        # Adjust audio data based on volume settings - apply volume adjustment to all audio data
                        try:
                            # Get current volume setting - use CONFIG to support real-time updates
                            volume_percent = CONFIG["audio_settings"]["general_volume"] / 100.0

                            # Adjust if not default volume (50%)
                            if volume_percent != 0.5:
                                # Convert byte data to numpy array
                                audio_array = np.frombuffer(decoded_data, dtype=np.int16)

                                # Adjust volume (multiply by 2x volume percentage to make 50% correspond to original volume)
                                # Use float64 for calculation to avoid overflow, then convert back to int16
                                audio_array = np.clip(
                                    audio_array.astype(np.float64) * (volume_percent * 2),
                                    -32768, 32767  # int16 range
                                ).astype(np.int16)

                                # Convert back to byte data
                                decoded_data = audio_array.tobytes()

                                if CONFIG["debug"]["enabled"] and seq_num % 500 == 0:
                                    logger.debug(f"Audio volume adjusted: {int(volume_percent * 100)}%")
                        except Exception as e:
                            logger.error(f"Volume adjustment failed: {e}")

                        # Open speaker stream on demand
                        if not speaker_stream:
                            try:
                                speaker_stream = self.audio.open(
                                    format=pyaudio.paInt16,
                                    channels=CONFIG["audio_settings"]["channels"],
                                    rate=CONFIG["audio_settings"]["sample_rate"],
                                    output=True,
                                    frames_per_buffer=CONFIG["audio_settings"]["chunk_size"]
                                )
                                logger.info("Speaker opened")
                            except Exception as e:
                                logger.error(f"Failed to open speaker: {e}")
                                audio_queue.task_done()
                                continue

                        # Play audio
                        try:
                            speaker_stream.write(decoded_data)
                            if CONFIG["debug"]["enabled"] and seq_num % 100 == 0:
                                logger.debug(f"Audio packet played, sequence: {seq_num}")
                        except Exception as e:
                            logger.error(f"Audio playback failed, sequence: {seq_num}, error: {e}")
                            # Try to reopen speaker
                            try:
                                if speaker_stream:
                                    speaker_stream.stop_stream()
                                    speaker_stream.close()
                                speaker_stream = self.audio.open(
                                    format=pyaudio.paInt16,
                                    channels=CONFIG["audio_settings"]["channels"],
                                    rate=CONFIG["audio_settings"]["sample_rate"],
                                    output=True,
                                    frames_per_buffer=CONFIG["audio_settings"]["chunk_size"]
                                )
                                logger.info("Speaker reopened")
                            except:
                                pass

                        # Mark task done
                        audio_queue.task_done()

                    except Exception as e:
                        logger.error(f"Decode and playback thread error: {e}")
                        time.sleep(0.1)

            # Start decode and playback thread
            decode_thread = threading.Thread(target=decode_and_play_worker, daemon=True)
            decode_thread.start()

            # Main receive loop
            while self.running:
                try:
                    # Receive UDP data
                    data, addr = recv_socket.recvfrom(4096)

                    # Update last packet receive time
                    last_packet_time = time.time()

                    # Audio packet detected, reduce music volume
                    if not self.audio_packet_receiving:
                        self.audio_packet_receiving = True
                        self._reduce_music_volume_for_audio_packet()

                    # Update last audio packet time
                    self.last_audio_packet_time = last_packet_time

                    # Check packet length
                    if len(data) < 6:
                        logger.warning(f"Received invalid UDP packet, length: {len(data)}")
                        continue

                    # Parse header
                    seq_num = int.from_bytes(data[0:4], byteorder='big')
                    data_len = int.from_bytes(data[4:6], byteorder='big')

                    # Check packet integrity
                    if len(data) < 6 + data_len:
                        logger.warning(f"UDP packet incomplete, expected length: {6 + data_len}, actual length: {len(data)}")
                        continue

                    # Sequence number check - based on xiaozhi-esp-1.6.0 implementation
                    if expected_seq_num is not None:
                        if seq_num < expected_seq_num:
                            logger.warning(f"Received expired audio packet, sequence: {seq_num}, expected: {expected_seq_num}")
                            continue
                        elif seq_num > expected_seq_num:
                            # Packet loss detected
                            missed_packets = seq_num - expected_seq_num
                            if missed_packets > 1:
                                logger.warning(f"Packet loss detected, lost {missed_packets} packets, from {expected_seq_num} to {seq_num-1}")

                    # Update expected next sequence number
                    expected_seq_num = seq_num + 1

                    # Check for flag bit (new format)
                    if len(data) > 6 and len(data) >= 7 + data_len:
                        is_raw_pcm = data[6] == 1  # Read flag bit: 0=Opus, 1=PCM
                        encoded_data = data[7:7+data_len]
                    else:
                        # Compatible with old format (no flag bit)
                        is_raw_pcm = False
                        encoded_data = data[6:6+data_len]

                    # Check if queue is full
                    if audio_queue.full():
                        # Queue full, discard oldest packet
                        try:
                            audio_queue.get_nowait()
                            audio_queue.task_done()
                            if CONFIG["debug"]["enabled"] and seq_num % 100 == 0:
                                logger.debug("Audio queue full, discarding oldest packet")
                        except:
                            pass

                    # Put data into queue
                    audio_queue.put((seq_num, is_raw_pcm, encoded_data))

                except socket.timeout:
                    # Timeout, continue loop
                    # Check if no packets received for long time
                    if time.time() - last_packet_time > 10:  # 10 seconds no data
                        if expected_seq_num is not None:
                            logger.info("No audio packets received for long time, resetting sequence number tracking")
                            expected_seq_num = None

                    # Check if music volume needs to be restored (no audio packets received for 2 seconds)
                    if self.audio_packet_receiving and time.time() - self.last_audio_packet_time > 2.0:
                        self.audio_packet_receiving = False
                        # Only restore volume if not recording
                        if not self.recording:
                            self._schedule_volume_restore("Audio packet reception ended")
                    pass
                except Exception as e:
                    logger.error(f"Error receiving audio: {e}")
                    time.sleep(0.1)

        except Exception as e:
            logger.error(f"Audio playback thread error: {e}")
        finally:
            # Stop decode thread
            decode_thread_running = False
            if decode_thread.is_alive():
                decode_thread.join(timeout=2.0)

            # Close socket and speaker
            try:
                recv_socket.close()
            except:
                pass

            if speaker_stream:
                try:
                    speaker_stream.stop_stream()
                    speaker_stream.close()
                except:
                    pass

            logger.info("Audio playback thread ended")

    def discover_server(self, timeout=5):
        """Discover server"""
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_sock.settimeout(timeout)

        # Use dedicated discovery service port, separate from audio transmission port
        broadcast_addr = ("255.255.255.255", CONFIG["network"]["discovery_port"])

        server_found = False
        server_ip, server_port = None, None

        try:
            # Ensure discovery_request is bytes type
            discovery_request = CONFIG["network"]["discovery_request"]
            if isinstance(discovery_request, str):
                discovery_request = discovery_request.encode('utf-8')

            # Ensure discovery_response_prefix is bytes type
            discovery_response_prefix = CONFIG["network"]["discovery_response_prefix"]
            if isinstance(discovery_response_prefix, str):
                discovery_response_prefix = discovery_response_prefix.encode('utf-8')

            logger.info("Broadcasting discovery request...")
            udp_sock.sendto(discovery_request, broadcast_addr)

            while not server_found:
                try:
                    data, addr = udp_sock.recvfrom(1024)
                    if data.startswith(discovery_response_prefix):
                        server_ip = addr[0]
                        server_port = int(data[len(discovery_response_prefix):])
                        server_found = True
                        logger.info(f"Server discovered: {server_ip}:{server_port}")
                except socket.timeout:
                    logger.warning("Discovery timeout, server not found")
                    break

        except Exception as e:
            logger.error(f"Error discovering server: {e}")
        finally:
            udp_sock.close()

        # Update server information
        if server_ip:
            CONFIG["network"]["server_ip"] = server_ip
        if server_port:
            CONFIG["network"]["server_udp_port"] = server_port

        return server_ip, server_port

    def cleanup(self):
        """Clean up resources"""
        logger.info("Cleaning up resources...")

        # Stop recording
        self.stop_recording()

        # Clean up volume restore timer
        if self.volume_restore_timer:
            self.volume_restore_timer.cancel()
            self.volume_restore_timer = None

        # Reset volume control state
        self.audio_packet_receiving = False
        self.recording_volume_reduced = False
        self.volume_reduced = False
        self.original_music_volume = None

        # Stop music playback
        if self.music_player and self.music_player.is_playing:
            try:
                self.music_player.stop_playback()
                logger.info("Music playback stopped")
            except Exception as e:
                logger.error(f"Error stopping music playback: {e}")

        # Set running flag to False
        self.running = False

        # Update status and save current configuration to file
        old_status = CONFIG["system"]["status"]
        CONFIG["system"]["status"] = "offline"

        # Only save configuration if status changed
        if old_status != "offline":
            save_config_to_file()
            logger.info("Configuration saved to file")

        # Publish offline status
        if self.is_connected:
            logger.info("Program is exiting normally, sending offline status to server")
            self._publish_status("offline")
            # Give some time for the message to be sent
            time.sleep(0.5)
        else:
            logger.info("MQTT not connected, skipping offline status notification")

        # Stop MQTT client
        if self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except:
                pass

        # Close UDP socket
        if self.udp_socket:
            try:
                self.udp_socket.close()
            except:
                pass

        # Close audio
        if self.audio:
            try:
                self.audio.terminate()
            except:
                pass

        # Stop wake word detector
        if self.wake_word_detector:
            self.wake_word_detector.cleanup()

        logger.info("Resource cleanup completed")

    def _on_wake_word_detected(self):
        """Wake word detection callback"""
        logger.info("Wake word detection callback triggered")

        # Check if wake word is still enabled
        if not CONFIG["wake_word"]["enabled"]:
            logger.warning("Wake word has been disabled, ignoring wake event")
            return

        # Play wake sound
        wake_sound_path = "sound/pvwake.wav"
        # Play sound in separate thread to avoid blocking main process
        threading.Thread(
            target=self._play_wav_file,
            args=(wake_sound_path,),
            daemon=True
        ).start()

        # Switch device state
        self.device_state = DEVICE_STATE_LISTENING

        # Get pre-wake audio data
        pre_audio_data = self.wake_word_detector.get_audio_data()

        # Start recording
        self.start_recording()

        # Send pre-wake audio data
        for frame in pre_audio_data:
            self._send_audio_udp(frame)


def signal_handler(sig, frame):
    """Signal handler function"""
    logger.info("Received interrupt signal, exiting...")
    if client:
        client.cleanup()
    sys.exit(0)


if __name__ == "__main__":
    # Parse command line arguments - only keep device ID switching functionality
    parser = argparse.ArgumentParser(description="Optimized Pi Client")
    parser.add_argument("--device-id", help="Device ID", default="yumi100")
    args = parser.parse_args()

    # Load configuration file first
    load_config_from_file()

    # Check if device ID needs to be updated
    config_changed = False
    if CONFIG["system"]["device_id"] != args.device_id:
        CONFIG["system"]["device_id"] = args.device_id
        config_changed = True

    # Only save if configuration actually changed
    if config_changed:
        save_config_to_file()

    # Set signal handling
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create and initialize client
    client = PiClient()
    client.initialize()

    # Try to discover server
    client.discover_server()

    # Keep program running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        client.cleanup()
