#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优化版Pi客户端 - 负责音频录制和数据传输
专注于低延迟音频传输
支持Porcupine唤醒词检测
"""

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
import copy

# 导入自定义模块
from music_player import MusicPlayer, MPV_AVAILABLE
from wake_word_detector import PorcupineWakeWordDetector, PORCUPINE_AVAILABLE

# 配置日志
# logging.basicConfig(
#     level=logging.DEBUG,
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
# )
# logger = logging.getLogger("PiClient")

# 设备状态常量
DEVICE_STATE_IDLE = 'idle'           # 空闲状态，等待唤醒
DEVICE_STATE_LISTENING = 'listening'  # 正在录音
DEVICE_STATE_PROCESSING = 'processing'  # 正在处理音频
DEVICE_STATE_PLAYING = 'playing'     # 正在播放音乐

# 默认配置结构 - 集中管理所有默认值
DEFAULT_CONFIG = {
    # 系统配置
    "system": {
        "device_id": "",
        "password": "",
        "user_id": None,
        "boot_time": None,  # 将在运行时设置
        "model": "raspberry_pi",
        "version": "1.0.0",
        "log_level": "DEBUG",
        "status": "offline",
        "last_update": None  # 将在运行时设置
    },

    # 唤醒词配置
    "wake_word": {
        "enabled": True,
        # "api_key": "t3m7HIoZMij6ckGwQlNwq41olJVIWTYVlH81lSjyt792u6nC8kFjLw==",
        "api_key": "engq+3lVOO74PHIKEFTW0/d17wc9gVarMZWkjXZgxvGbqPV2q58koA==",
        "keyword_path": "wakeword_source/hello_chris.ppn",
        "sensitivity": 0.5
    },

    # 音频设置
    "audio_settings": {
        "sample_rate": 24000,
        "channels": 1,
        "chunk_size": 960,  # 优化为Opus编码器推荐的帧大小
        "format": "int16",
        "general_volume": 50,
        "music_volume": 50,
        "notification_volume": 50,
        "wake_sound_path": "sound/pvwake.wav"
    },

    # MQTT配置
    "mqtt": {
        "broker": "broker.emqx.io",
        "port": 1883,
        "username": None,
        "password": None,
        "client_id_prefix": "smart_assistant_87",
        "topic_prefix": "smart0337187"
    },

    # 网络配置
    "network": {
        "server_ip": None,      # 将通过发现服务或手动设置
        "server_udp_port": 8884,        # 音频传输端口
        "server_udp_receive_port": 8885, # 音频接收端口
        "stt_bridge_ip": None,   # STT 桥接处理器 IP 地址
        "stt_bridge_port": 8884, # STT 桥接处理器端口
        "discovery_port": 50000,        # 发现服务端口
        "discovery_request": b"DISCOVER_SERVER_REQUEST",
        "discovery_response_prefix": b"DISCOVER_SERVER_RESPONSE_",
        "stt_mode": False  # 是否启用STT桥接模式
    },

    # 录音配置
    "recording": {
        "auto_stop": True,
        "timeout": 15.0,  # 秒，最大录音时长
        "silence_threshold": 300,   # 默音能量阈值
        "initial_silence_duration": 3.0,  # 秒，唤醒后初始静默时间阈值
        "speech_silence_duration": 1.0,   # 秒，说话后静默时间阈值
        "save_path": "recordings"
    },

    # 调试配置
    "debug": {
        "enabled": False
    }
}

# 配置文件路径
CONFIG_FILE_PATH = "config.json"

'''
# 创建嵌入式配置（仅在发布时使用）
# 这个结构只在_publish_config方法中创建，用于发送给服务器
# 实际运行时使用DEFAULT_CONFIG
'''

# 兼容性变量 - 为了保持与现有代码的兼容性
DEFAULT_PV_API_KEY = DEFAULT_CONFIG["wake_word"]["api_key"]
PORCUPINE_KEYWORD_PATH = DEFAULT_CONFIG["wake_word"]["keyword_path"]

def load_config_from_file():
    """从配置文件加载配置"""
    global DEFAULT_CONFIG

    try:
        if os.path.exists(CONFIG_FILE_PATH):
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                logger.info(f"从 {CONFIG_FILE_PATH} 加载配置")

                # 创建一个完整的配置，包含所有默认值
                complete_config = {
                    "system": dict(DEFAULT_CONFIG["system"]),
                    "wake_word": dict(DEFAULT_CONFIG["wake_word"]),
                    "audio_settings": dict(DEFAULT_CONFIG["audio_settings"]),
                    "mqtt": dict(DEFAULT_CONFIG["mqtt"]),
                    "network": {
                        "server_ip": DEFAULT_CONFIG["network"]["server_ip"],
                        "server_udp_port": DEFAULT_CONFIG["network"]["server_udp_port"],
                        "server_udp_receive_port": DEFAULT_CONFIG["network"]["server_udp_receive_port"],
                        "stt_bridge_ip": DEFAULT_CONFIG["network"]["stt_bridge_ip"],
                        "stt_bridge_port": DEFAULT_CONFIG["network"]["stt_bridge_port"],
                        "discovery_port": DEFAULT_CONFIG["network"]["discovery_port"],
                        "discovery_request": "DISCOVER_SERVER_REQUEST",
                        "discovery_response_prefix": "DISCOVER_SERVER_RESPONSE_",
                        "stt_mode": DEFAULT_CONFIG["network"]["stt_mode"]
                    },
                    "recording": dict(DEFAULT_CONFIG["recording"]),
                    "debug": dict(DEFAULT_CONFIG["debug"])
                }

                # 更新完整配置，使用文件中的值
                for section, values in file_config.items():
                    if section in complete_config:
                        if isinstance(values, dict) and isinstance(complete_config[section], dict):
                            # 更新现有部分
                            complete_config[section].update(values)
                        else:
                            # 替换非字典部分
                            complete_config[section] = copy.deepcopy(values)
                    else:
                        # 添加新部分
                        complete_config[section] = copy.deepcopy(values)

                # 更新DEFAULT_CONFIG
                DEFAULT_CONFIG = complete_config

                return True
        else:
            logger.info(f"配置文件 {CONFIG_FILE_PATH} 不存在，使用默认配置")
            return False
    except Exception as e:
        logger.error(f"加载配置文件时出错: {e}")
        return False

def save_config_to_file():
    """将配置保存到文件"""
    try:
        # 保存到文件
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        logger.info(f"配置已保存到 {CONFIG_FILE_PATH}")
        return True
    except Exception as e:
        logger.error(f"保存配置文件时出错: {e}")
        return False

class PiClient:
    def __init__(self, config=None):
        """初始化Pi客户端"""
        # 注意：配置文件应该已经在主程序中加载，这里不再重复加载

        # 设置运行时值（不保存到文件）
        DEFAULT_CONFIG["system"]["boot_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        DEFAULT_CONFIG["system"]["last_update"] = time.time()

        # 从默认配置创建扁平化配置
        self.config = self._create_flat_config()

        # 更新配置（如果提供）
        if config:
            self.config.update(config)

        # 生成派生配置
        self._generate_derived_config()

        # 初始化实例变量
        self._init_instance_variables()

    def _create_flat_config(self):
        """从默认配置创建扁平化配置

        Returns:
            dict: 扁平化的配置字典
        """
        config = {
            # MQTT配置
            "mqtt_broker": DEFAULT_CONFIG["mqtt"]["broker"],
            "mqtt_port": DEFAULT_CONFIG["mqtt"]["port"],
            "mqtt_username": DEFAULT_CONFIG["mqtt"]["username"],
            "mqtt_password": DEFAULT_CONFIG["mqtt"]["password"],
            "mqtt_client_id": f"{DEFAULT_CONFIG['mqtt']['client_id_prefix']}_{socket.gethostname()}_{int(time.time())}_{id(threading.current_thread())}",
            "topic_prefix": DEFAULT_CONFIG["mqtt"]["topic_prefix"],

            # 设备信息
            "device_id": DEFAULT_CONFIG["system"]["device_id"],

            # 音频配置
            "audio_sample_rate": DEFAULT_CONFIG["audio_settings"]["sample_rate"],
            "audio_channels": DEFAULT_CONFIG["audio_settings"]["channels"],
            "audio_chunk_size": DEFAULT_CONFIG["audio_settings"]["chunk_size"],
            "audio_format": DEFAULT_CONFIG["audio_settings"]["format"],

            # 网络配置
            "server_ip": DEFAULT_CONFIG["network"]["server_ip"],
            "server_udp_port": DEFAULT_CONFIG["network"]["server_udp_port"],
            "server_udp_receive_port": DEFAULT_CONFIG["network"]["server_udp_receive_port"],
            "stt_bridge_ip": DEFAULT_CONFIG["network"]["stt_bridge_ip"],
            "stt_bridge_port": DEFAULT_CONFIG["network"]["stt_bridge_port"],
            "stt_mode": DEFAULT_CONFIG["network"]["stt_mode"],
            "discovery_port": DEFAULT_CONFIG["network"]["discovery_port"],
            "discovery_request": DEFAULT_CONFIG["network"]["discovery_request"],
            "discovery_response_prefix": DEFAULT_CONFIG["network"]["discovery_response_prefix"],

            # Porcupine配置
            "porcupine_access_key": DEFAULT_CONFIG["wake_word"]["api_key"],
            "porcupine_keyword_paths": [DEFAULT_CONFIG["wake_word"]["keyword_path"]],
            "porcupine_sensitivity": DEFAULT_CONFIG["wake_word"]["sensitivity"],

            # 录音配置
            "auto_stop_recording": DEFAULT_CONFIG["recording"]["auto_stop"],
            "recording_timeout": DEFAULT_CONFIG["recording"]["timeout"],
            "silence_threshold": DEFAULT_CONFIG["recording"]["silence_threshold"],
            "initial_silence_duration": DEFAULT_CONFIG["recording"]["initial_silence_duration"],
            "speech_silence_duration": DEFAULT_CONFIG["recording"]["speech_silence_duration"],
            "pre_buffer_duration": 0,  # 秒，保存唤醒前的音频

            # 其他配置
            "audio_save_path": DEFAULT_CONFIG["recording"]["save_path"],
            "debug": DEFAULT_CONFIG["debug"]["enabled"]
        }

        return config

    def _init_instance_variables(self):
        """初始化实例变量"""
        # 初始化MQTT客户端
        self.mqtt_client = None
        self.is_connected = False

        # 初始化UDP套接字
        self.udp_socket = None

        # 音频处理
        self.audio = None
        self.encoder = None
        self.decoder = None
        self.mic_stream = None
        self.speaker_stream = None

        # 控制标志
        self.recording = False
        self.running = True
        self.recording_thread = None
        self.playback_thread = None

        # 序列号（用于UDP传输）
        self.sequence_number = 0

        # 设备状态
        self.device_state = DEVICE_STATE_IDLE

        # 唤醒词检测器
        self.wake_word_detector = None

        # 录音超时定时器
        self.recording_timer = None

        # 音乐播放器
        self.music_player = None
        if MPV_AVAILABLE:
            self.music_player = MusicPlayer()
            # 设置歌曲完成回调
            self.music_player.set_completion_callback(self._on_song_completed)
        else:
            logger.warning("音乐播放功能不可用，缺少必要的库")

        # 音频包接收状态管理（用于音量控制）
        self.audio_packet_receiving = False
        self.original_music_volume = None
        self.volume_reduced = False
        self.last_audio_packet_time = 0
        self.volume_restore_timer = None

        # 录音状态管理（用于音量控制）
        self.recording_volume_reduced = False

        # 创建音频保存目录（如果启用调试）
        if self.config["debug"]:
            os.makedirs(self.config["audio_save_path"], exist_ok=True)

    def _generate_derived_config(self):
        """生成派生配置"""
        device_id = self.config["device_id"]
        topic_prefix = self.config["topic_prefix"]

        # 添加MQTT主题
        self.config["command_topic"] = f"{topic_prefix}/client/command/{device_id}"
        self.config["audio_topic"] = f"{topic_prefix}/client/audio/{device_id}"
        self.config["status_topic"] = f"{topic_prefix}/client/status/{device_id}"
        self.config["config_topic"] = f"{topic_prefix}/client/config/{device_id}"

    def _get_ip_address(self):
        """获取设备的实际IP地址（非127.0.0.1或127.0.1.1）

        返回:
            str: 设备的IP地址，如果无法获取则返回127.0.0.1
        """
        try:
            # 创建一个临时套接字连接到外部服务器
            # 这会使用默认路由，从而获取正确的本地IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # 连接到Google DNS
            ip = s.getsockname()[0]     # 获取本地IP
            s.close()
            return ip
        except Exception as e:
            logger.warning(f"无法获取IP地址: {e}，使用默认值")

            # 尝试获取所有非回环接口的IP
            try:
                for interface in socket.if_nameindex():
                    ifname = interface[1]
                    if ifname != 'lo':  # 排除回环接口
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

            # 如果上述方法都失败，返回主机名解析结果
            return socket.gethostbyname(socket.gethostname())

    def initialize(self):
        """初始化客户端"""
        # 初始化音频
        self.audio = pyaudio.PyAudio()

        # 初始化Opus编码器和解码器
        self.encoder = opuslib.Encoder(
            self.config["audio_sample_rate"],
            self.config["audio_channels"],
            opuslib.APPLICATION_AUDIO
        )

        self.decoder = opuslib.Decoder(
            self.config["audio_sample_rate"],
            self.config["audio_channels"]
        )

        # 确保声音文件目录存在
        os.makedirs("sound", exist_ok=True)

        # 检查唤醒提示音是否存在
        wake_sound_path = "sound/pvwake.wav"
        if not os.path.exists(wake_sound_path):
            logger.warning(f"唤醒提示音文件不存在: {wake_sound_path}")
            logger.info("请确保在sound目录中放置pvwake.wav文件")

        # 初始化UDP套接字
        self._setup_udp()

        # 启动音频播放线程
        self.playback_thread = threading.Thread(target=self._audio_playback_worker, daemon=True)
        self.playback_thread.start()

        # 初始化唤醒词检测器
        if PORCUPINE_AVAILABLE:
            self.wake_word_detector = PorcupineWakeWordDetector()
            # 创建唤醒词检测器配置
            if self.wake_word_detector.initialize(self.config):
                self.wake_word_detector.set_callback(self._on_wake_word_detected)
                self.wake_word_detector.start_detection()

        # 初始化MQTT连接 - 移到最后，因为连接成功后会自动发送状态和配置
        self._setup_mqtt()

        logger.info(f"Pi客户端初始化完成，设备ID: {DEFAULT_CONFIG['system']['device_id']}")

    def _setup_mqtt(self):
        """设置MQTT连接 - 参照dev_control.py"""
        # 确保客户端ID是唯一的
        if not self.config["mqtt_client_id"] or len(self.config["mqtt_client_id"]) < 10:
            self.config["mqtt_client_id"] = f"{DEFAULT_CONFIG['mqtt']['client_id_prefix']}_{socket.gethostname()}_{int(time.time())}_{id(threading.current_thread())}"

        # 创建MQTT客户端 - 使用 paho-mqtt 1.x 风格
        self.mqtt_client = mqtt.Client(self.config["mqtt_client_id"], clean_session=True)
        logger.info(f"初始化MQTT客户端 {self.config['mqtt_client_id']} (clean_session=True)")

        # 设置用户名密码（如果有）
        if self.config["mqtt_username"] and self.config["mqtt_password"]:
            self.mqtt_client.username_pw_set(
                self.config["mqtt_username"],
                self.config["mqtt_password"]
            )

        # 设置回调
        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
        self.mqtt_client.on_message = self._on_mqtt_message

        # 设置自动重连
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)

        # 最大重试次数
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # 连接到MQTT代理
                logger.debug(f"正在连接到 {self.config['mqtt_broker']}:{self.config['mqtt_port']}... (尝试 {retry_count+1}/{max_retries})")
                self.mqtt_client.connect(
                    self.config["mqtt_broker"],
                    self.config["mqtt_port"],
                    60  # keepalive 60秒
                )

                # 启动MQTT循环
                self.mqtt_client.loop_start()
                logger.info("MQTT循环已启动")

                # 连接成功，跳出循环
                break

            except Exception as e:
                retry_count += 1
                logger.error(f"MQTT连接失败 (尝试 {retry_count}/{max_retries}): {e}")

                if retry_count < max_retries:
                    # 等待一段时间后重试
                    retry_delay = 2 ** retry_count  # 指数退避: 2, 4, 8...秒
                    logger.info(f"将在 {retry_delay} 秒后重试连接...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"MQTT连接失败，已达到最大重试次数 ({max_retries})")
                    # 最后一次尝试失败后，仍然启动循环，以便后续自动重连
                    self.mqtt_client.loop_start()

    def _setup_udp(self):
        """设置UDP套接字"""
        try:
            # 创建UDP套接字
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            # 设置缓冲区大小，提高性能
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)  # 256KB

            # 输出连接信息
            if self.config["stt_mode"] and self.config["stt_bridge_ip"]:
                logger.info(f"UDP套接字已创建，STT桥接模式，目标: {self.config['stt_bridge_ip']}:{self.config['stt_bridge_port']}")
            elif self.config["server_ip"]:
                logger.info(f"UDP套接字已创建，服务器模式，目标: {self.config['server_ip']}:{self.config['server_udp_port']}")
            else:
                logger.info("UDP套接字已创建，等待服务器发现")
        except Exception as e:
            logger.error(f"创建UDP套接字失败: {e}")

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT连接回调"""
        if rc == 0:
            logger.info("已连接到MQTT代理")
            self.is_connected = True

            # 订阅命令主题
            command_topic = f"{self.config['topic_prefix']}/server/command/{self.config['device_id']}"
            client.subscribe(command_topic, qos=2)
            logger.info(f"已订阅命令主题: {command_topic}")

            # 订阅配置主题，以接收服务器发送的配置更新
            config_topic = f"{self.config['topic_prefix']}/server/config/{self.config['device_id']}"
            client.subscribe(config_topic, qos=2)
            logger.info(f"已订阅配置主题: {config_topic}")

            # 更新状态（仅在内存中）
            DEFAULT_CONFIG["system"]["status"] = "online"

            # 发送在线状态
            self._publish_status("online")
            time.sleep(1)

            # 发送设备配置
            self._publish_config()

            logger.info("MQTT初始化完成")
        else:
            logger.error(f"MQTT连接失败，返回码: {rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """MQTT断开连接回调"""
        logger.warning(f"与MQTT代理断开连接，返回码: {rc}")

        # 尝试发布离线状态（虽然可能失败，但值得一试）
        try:
            # 构建状态消息
            self._publish_status("offline")
            time.sleep(1)  # 减少等待时间
            logger.info("已发布离线状态")
        except Exception as e:
            logger.error(f"发布离线状态失败: {e}")

        self.is_connected = False

        # 如果是意外断开，尝试重新连接
        if rc != 0:
            logger.info("MQTT连接意外断开，将自动尝试重新连接...")

            # 更新客户端ID，确保唯一性
            new_client_id = f"{DEFAULT_CONFIG['mqtt']['client_id_prefix']}_{socket.gethostname()}_{int(time.time())}_{id(threading.current_thread())}"
            self.config["mqtt_client_id"] = new_client_id
            logger.info(f"生成新的客户端ID: {new_client_id}")

            # 客户端会自动尝试重新连接，因为我们使用了loop_start()
            # 如果需要手动重连，可以取消下面的注释
            # try:
            #     # 停止当前循环
            #     client.loop_stop()
            #     # 创建新的客户端实例
            #     self.mqtt_client = mqtt.Client(new_client_id)
            #     # 设置回调
            #     self.mqtt_client.on_connect = self._on_mqtt_connect
            #     self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
            #     self.mqtt_client.on_message = self._on_mqtt_message
            #     # 重新连接
            #     self.mqtt_client.connect(self.config["mqtt_broker"], self.config["mqtt_port"], 60)
            #     self.mqtt_client.loop_start()
            #     logger.info("已尝试重新连接MQTT")
            # except Exception as e:
            #     logger.error(f"重新连接MQTT失败: {e}")
        else:
            logger.info("MQTT连接正常断开")

    def _on_mqtt_message(self, client, userdata, msg):
        """MQTT消息回调"""
        try:
            # 解析消息
            payload = msg.payload.decode()
            logger.debug(f"收到MQTT消息: {msg.topic}")

            # 获取设备ID和主题前缀
            if msg.topic == f"{self.config['topic_prefix']}/server/command/{self.config['device_id']}":
                logger.debug("处理命令消息")
                self._handle_command(payload)

            # 处理配置更新
            elif msg.topic == f"{self.config['topic_prefix']}/server/config/{self.config['device_id']}":
                logger.debug("处理配置更新消息")
                self._handle_config_update(payload)

        except Exception as e:
            logger.error(f"处理MQTT消息时出错: {e}")

    def _publish_config(self):
        """发布设备配置"""
        try:
            # 构建配置主题
            config_topic = f"{self.config['topic_prefix']}/client/config/{self.config['device_id']}"

            # 只在发布时创建EMBEDDED_CONFIG
            embedded_config = {
                "system": dict(DEFAULT_CONFIG["system"]),
                "wake_word": {
                    "enabled": DEFAULT_CONFIG["wake_word"]["enabled"],
                },
                "audio_settings": {
                    "general_volume": DEFAULT_CONFIG["audio_settings"]["general_volume"],
                    "music_volume": DEFAULT_CONFIG["audio_settings"]["music_volume"],
                    "notification_volume": DEFAULT_CONFIG["audio_settings"]["notification_volume"],
                },
                "mqtt": dict(DEFAULT_CONFIG["mqtt"]),
            }

            # 构建配置消息，包含device_id和完整配置
            config_message = {
                'device_id': self.config["device_id"],
                'config': embedded_config,
                'timestamp': time.time()
            }

            # 发布配置
            result = self.mqtt_client.publish(
                config_topic,
                json.dumps(config_message),
                qos=2,
                retain=False
            )

            if result.rc == 0:
                logger.info(f"已发送设备配置到主题: {config_topic}")
                return True
            else:
                logger.error(f"发送设备配置失败，错误码: {result.rc}")
                return False

        except Exception as e:
            logger.error(f"发布设备配置失败: {e}")
            return False

    def _publish_status(self, status):
        """发布状态信息"""
        if not self.is_connected:
            logger.warning("MQTT未连接，无法发布状态")
            return

        try:
            # 创建状态消息
            message = {
                "device_id": self.config["device_id"],
                "password": DEFAULT_CONFIG["system"]["password"],
                "user_id": DEFAULT_CONFIG["system"]["user_id"],
                "ip": self._get_ip_address(),  # 使用更可靠的方法获取 IP
                "model": DEFAULT_CONFIG["system"]["model"],
                "timestamp": time.time(),
                "status": status
            }

            # 发布消息
            result = self.mqtt_client.publish(
                self.config["status_topic"],
                json.dumps(message),
                qos=2,
                retain=False
            )

            if result.rc == 0:
                logger.debug(f"已发布状态: {status}")
            else:
                logger.error(f"发布状态失败，错误码: {result.rc}")

        except Exception as e:
            logger.error(f"发布状态时出错: {e}")

    def _handle_config_update(self, payload):
        """处理配置更新消息"""
        try:
            # 解析配置消息
            message = json.loads(payload)

            # 检查消息格式
            if not isinstance(message, dict):
                logger.warning("配置消息不是有效的JSON对象")
                return

            # 检查设备ID（如果有）
            device_id = message.get("device_id")
            if device_id and device_id != self.config["device_id"]:
                logger.warning(f"收到其他设备的配置: {device_id}")
                return

            # 检查时间戳
            timestamp = message.get("timestamp")
            if timestamp:
                logger.info(f"配置时间戳: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))}")

            config_changed = False

            # 处理服务器发送的部分配置更新格式: {config: "section.key", new_value: value}
            if "config" in message and "new_value" in message:
                path = message["config"]
                value = message["new_value"]

                logger.info(f"收到部分配置更新: {path} = {value}")

                # 更新配置
                if self._set_config_value(path, value):
                    config_changed = True
                    logger.info(f"更新配置: {path} = {value}")

                    # 特殊处理某些配置项
                    if path == "wake_word.enabled":
                        logger.info(f"唤醒词状态已更改为: {value}")
                    elif path.startswith("audio_settings."):
                        volume_type = path.split('.')[-1]
                        logger.info(f"音量设置已更改: {volume_type} = {value}")
                    elif path.startswith("system."):
                        system_setting = path.split('.')[-1]
                        logger.info(f"系统设置已更改: {system_setting} = {value}")

            # 处理完整配置更新格式: {config: {section: {key: value}}}
            elif "config" in message and isinstance(message["config"], dict):
                config_data = message["config"]
                logger.info(f"收到完整配置更新: {config_data}")

                # 遍历配置数据
                for section, values in config_data.items():
                    if isinstance(values, dict):
                        for key, value in values.items():
                            # 更新配置
                            path = f"{section}.{key}"
                            if self._set_config_value(path, value):
                                config_changed = True
                                logger.info(f"更新配置项: {path} = {value}")
            else:
                logger.warning("未识别的配置消息格式")

            # 如果配置有变化，更新时间戳并应用变更
            if config_changed:
                # 更新最后修改时间
                DEFAULT_CONFIG["system"]["last_update"] = time.time()

                # 应用配置变更
                self._apply_config_changes()

                # 保存配置到文件
                save_config_to_file()

                # # 发送确认消息
                # self._publish_config_update_ack(message)

                logger.info("配置更新完成并已应用")
            else:
                logger.info("配置无变化")

        except json.JSONDecodeError:
            logger.error("配置消息不是有效的JSON格式")
        except Exception as e:
            logger.error(f"处理配置更新时出错: {e}")

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
        """设置配置项

        支持使用点号分隔的路径，例如 "system.language"

        Args:
            path: 配置项路径
            value: 要设置的值

        Returns:
            bool: 设置是否成功
        """
        # 处理特殊的配置路径映射
        path_mapping = {
            "audio_settings.general_volume": "audio_settings.general_volume",
            "audio_settings.music_volume": "audio_settings.music_volume",
            "audio_settings.notification_volume": "audio_settings.notification_volume",
            "wake_word.enabled": "wake_word.enabled",
            "system.password": "system.password",
            "system.user_id": "system.user_id"
        }

        # 检查是否需要映射路径
        if path in path_mapping:
            path = path_mapping[path]

        parts = path.split('.')
        current = DEFAULT_CONFIG

        try:
            # 遍历路径直到倒数第二个部分
            for i, part in enumerate(parts[:-1]):
                if part not in current:
                    # 如果路径不存在，创建一个新的字典
                    current[part] = {}
                    logger.info(f"创建新的配置节点: {part}")
                elif not isinstance(current[part], dict):
                    # 如果路径存在但不是字典，替换为字典
                    logger.warning(f"配置节点 {part} 不是字典，将被替换")
                    current[part] = {}

                current = current[part]

            # 设置最后一个部分的值
            last_part = parts[-1]

            # 检查值是否需要转换
            if isinstance(value, str) and last_part in ["enabled"]:
                # 将字符串 "true"/"false" 转换为布尔值
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False

            # 检查值是否有变化
            if last_part in current and current[last_part] == value:
                logger.info(f"配置项 {path} 的值未变化: {value}")
                return False

            # 更新配置值
            old_value = current.get(last_part, "未设置")
            current[last_part] = value
            logger.info(f"更新配置项 {path}: {old_value} -> {value}")
            return True

        except Exception as e:
            logger.error(f"设置配置项 {path} 失败: {e}")
            return False

    def _apply_config_changes(self):
        """应用配置变更，处理需要特殊操作的配置项"""
        try:
            # 处理唤醒词配置
            if "wake_word" in DEFAULT_CONFIG:
                wake_word_config = DEFAULT_CONFIG["wake_word"]

                # 检查是否启用/禁用唤醒词
                if "enabled" in wake_word_config:
                    enabled = wake_word_config["enabled"]
                    if enabled and not self.wake_word_detector and PORCUPINE_AVAILABLE:
                        # 启用唤醒词
                        logger.info("启用唤醒词检测")
                        self.wake_word_detector = PorcupineWakeWordDetector()

                        # 创建唤醒词检测器配置
                        detector_config = {
                            "porcupine_access_key": DEFAULT_PV_API_KEY,
                            "porcupine_keyword_paths": [PORCUPINE_KEYWORD_PATH],  # 确保是列表
                            "porcupine_sensitivity": wake_word_config.get("sensitivity", 0.5),
                            "pre_buffer_duration": DEFAULT_CONFIG.get("recording", {}).get("pre_buffer_duration", 0)
                        }

                        if self.wake_word_detector.initialize(detector_config):
                            self.wake_word_detector.set_callback(self._on_wake_word_detected)
                            self.wake_word_detector.start_detection()
                    elif not enabled and self.wake_word_detector:
                        # 禁用唤醒词
                        logger.info("禁用唤醒词检测")
                        self.wake_word_detector.cleanup()
                        self.wake_word_detector = None

            # 处理音频设置
            if "audio_settings" in DEFAULT_CONFIG:
                audio_settings = DEFAULT_CONFIG["audio_settings"]
                logger.info(f"应用音频设置: {audio_settings}")

                # 音量设置会在播放音频时自动应用，无需额外处理

            # 处理系统设置
            if "system" in DEFAULT_CONFIG:
                system_settings = DEFAULT_CONFIG["system"]
                logger.info(f"应用系统设置: {system_settings}")

                # 更新设备ID和密码
                if "device_id" in system_settings:
                    self.config["device_id"] = system_settings["device_id"]
                    logger.info(f"设备ID已更新为: {system_settings['device_id']}")

            # 确保调试目录存在
            if DEFAULT_CONFIG.get("debug", {}).get("enabled", False):
                audio_save_path = DEFAULT_CONFIG.get("debug", {}).get("audio_save_path", "recordings")
                os.makedirs(audio_save_path, exist_ok=True)

        except Exception as e:
            logger.error(f"应用配置变更时出错: {e}")

    def _handle_command(self, payload):
        """处理接收到的命令"""
        try:
            command = json.loads(payload)
            cmd_type = command.get("type")

            if cmd_type == "record":
                # 开始录音
                self.start_recording()

            elif cmd_type == "stop_record":
                # 停止录音
                self.stop_recording()

            elif cmd_type == "play":
                # 播放音频
                audio_data = command.get("data")
                if audio_data:
                    # 将Base64编码的音频数据解码并播放
                    import base64
                    audio_bytes = base64.b64decode(audio_data)
                    self._play_audio(audio_bytes)

            elif cmd_type == "play_music":
                # 播放音乐（YouTube链接）
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("音乐播放功能不可用")
                    return

                # 获取YouTube链接和音量
                youtube_url = command.get("url")
                volume = command.get("volume", DEFAULT_CONFIG["audio_settings"]["music_volume"])

                if not youtube_url:
                    logger.error("缺少YouTube链接")
                    return

                # 如果当前有音乐在播放，先停止它
                if self.music_player.is_playing or self.music_player.player:
                    logger.info("停止当前播放以开始新的播放")
                    self.music_player.stop_playback()
                    # 短暂延迟以确保资源被释放
                    time.sleep(0.5)

                # 更新设备状态
                self.device_state = DEVICE_STATE_PLAYING

                # 播放音乐
                logger.info(f"开始播放音乐: {youtube_url}, 音量: {volume}")
                success = self.music_player.play_url(youtube_url, volume)

                # 发送播放状态
                if success:
                    self._publish_music_status("playing", self.music_player.current_title)
                else:
                    self._publish_music_status("error", "播放失败")
                    self.device_state = DEVICE_STATE_IDLE

            elif cmd_type == "stop_music":
                # 停止音乐播放
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("音乐播放功能不可用")
                    return

                # 停止播放
                self.music_player.stop_playback()

                # 更新设备状态
                self.device_state = DEVICE_STATE_IDLE

                # 发送停止状态
                self._publish_music_status("stopped")

            elif cmd_type == "pause_music":
                # 暂停音乐播放
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("音乐播放功能不可用")
                    return

                # 检查是否正在播放
                if not self.music_player.is_playing:
                    logger.warning("没有正在播放的音乐，无法暂停")
                    return

                # 暂停播放
                success = self.music_player.pause_playback()

                # 发送暂停状态
                if success:
                    self._publish_music_status("paused")
                    logger.info("音乐播放已暂停")

            elif cmd_type == "resume_music":
                # 恢复音乐播放
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("音乐播放功能不可用")
                    return

                # 检查是否有播放器实例
                if not self.music_player.player:
                    logger.warning("没有活动的播放器，无法恢复播放")
                    return

                # 获取当前标题（在恢复之前）
                current_title = self.music_player.current_title
                logger.info(f"恢复前的标题: {current_title if current_title else '未知标题'}")

                # 恢复播放
                success = self.music_player.resume_playback()

                # 更新设备状态
                if success:
                    self.device_state = DEVICE_STATE_PLAYING

                    # 再次获取标题（可能在恢复过程中被更新）
                    title = self.music_player.current_title

                    # 如果标题仍然为空，但我们之前有标题，则使用之前的标题
                    if not title and current_title:
                        self.music_player.current_title = current_title
                        logger.info(f"手动恢复标题信息: {current_title}")

                    # 发布状态更新
                    self._publish_music_status("playing", self.music_player.current_title)
                    logger.info(f"音乐播放已恢复: {self.music_player.current_title if self.music_player.current_title else '未知标题'}")

            elif cmd_type == "set_volume":
                # 设置音量
                if not MPV_AVAILABLE or not self.music_player:
                    logger.error("音乐播放功能不可用")
                    return

                # 获取音量
                volume = command.get("volume")
                if volume is None:
                    logger.error("缺少音量参数")
                    return

                # 设置音量
                success = self.music_player.set_volume(volume)

                # 更新配置
                if success:
                    DEFAULT_CONFIG["audio_settings"]["music_volume"] = volume
                    logger.info(f"音乐音量已设置为: {volume}")

                    # 发送音量状态
                    self._publish_music_status("volume_changed", volume=volume)

            elif cmd_type == "set_server":
                # 设置服务器IP和端口
                server_ip = command.get("server_ip")
                server_port = command.get("server_port")
                if server_ip:
                    self.config["server_ip"] = server_ip
                    logger.info(f"服务器IP已设置为: {server_ip}")
                if server_port:
                    self.config["server_udp_port"] = server_port
                    logger.info(f"服务器UDP端口已设置为: {server_port}")

            elif cmd_type == "ping":
                # 心跳检测
                self._publish_status("online")

            else:
                logger.warning(f"未知命令类型: {cmd_type}")

        except json.JSONDecodeError:
            logger.error("无效的JSON格式")
        except Exception as e:
            logger.error(f"处理命令时出错: {e}")


    def _on_song_completed(self, finished_title):
        """歌曲完成回调函数"""
        try:
            logger.info(f"歌曲播放完成: {finished_title if finished_title else '未知标题'}")

            # 更新设备状态
            self.device_state = DEVICE_STATE_IDLE

            # 发布歌曲完成状态
            self._publish_music_status("completed", finished_title)

            # 发送请求下一首歌的命令到服务器
            self._request_next_song()

        except Exception as e:
            logger.error(f"处理歌曲完成回调时出错: {e}")

    def _request_next_song(self):
        """请求服务器播放下一首歌"""
        try:
            if not self.is_connected:
                logger.warning("MQTT未连接，无法请求下一首歌")
                return

            # 构建请求下一首歌的消息
            request_data = {
                "type": "request_next_song",
                "device_id": self.config["device_id"],
                "timestamp": time.time()
            }

            # 发送到服务器命令主题
            topic = f"{self.config['topic_prefix']}/client/request/{self.config['device_id']}"
            message = json.dumps(request_data, ensure_ascii=False)

            result = self.mqtt_client.publish(topic, message, qos=1)
            if result.rc == 0:
                logger.debug("已发送下一首歌请求")
            else:
                logger.error(f"发送下一首歌请求失败，返回码: {result.rc}")

        except Exception as e:
            logger.error(f"请求下一首歌时出错: {e}")


    def _publish_music_status(self, status, title=None, volume=None):
        """发布音乐播放状态"""
        if not self.is_connected:
            logger.warning("MQTT未连接，无法发布音乐状态")
            return

        try:
            # 构建状态消息主题
            music_status_topic = f"{self.config['topic_prefix']}/client/music_status/{self.config['device_id']}"

            # 构建状态消息
            message = {
                "device_id": self.config["device_id"],
                "timestamp": time.time(),
                "status": status
            }

            # 添加可选字段
            if title:
                message["title"] = title
            if volume is not None:
                message["volume"] = volume

            # 添加当前播放信息（无论是否正在播放）
            if self.music_player and self.music_player.player:
                player_status = self.music_player.get_status()
                message["player_status"] = player_status

            # 发布消息
            result = self.mqtt_client.publish(
                music_status_topic,
                json.dumps(message),
                qos=1
            )

            if result.rc == 0:
                logger.debug(f"已发布音乐状态: {status}")
            else:
                logger.error(f"发布音乐状态失败，错误码: {result.rc}")

        except Exception as e:
            logger.error(f"发布音乐状态时出错: {e}")

    def _reduce_music_volume(self, reason="unknown"):
        """降低音乐音量（通用方法）

        Args:
            reason: 降低音量的原因，用于日志记录
        """
        if not self.music_player or not self.music_player.is_playing:
            return

        try:
            # 获取当前音量
            current_volume = self.music_player.player.volume if self.music_player.player else None
            if current_volume is None:
                return

            # 如果还没有降低音量，则保存原始音量并降低
            if not self.volume_reduced:
                self.original_music_volume = current_volume
                target_volume = min(current_volume, 40)

                # 只有当目标音量小于当前音量时才降低
                if target_volume < current_volume:
                    self.music_player.set_volume(target_volume)
                    self.volume_reduced = True
                    logger.info(f"{reason}，音乐音量从 {current_volume} 降低到 {target_volume}")

        except Exception as e:
            logger.error(f"降低音乐音量时出错: {e}")

    def _reduce_music_volume_for_audio_packet(self):
        """当收到音频包时降低音乐音量"""
        self._reduce_music_volume("音频包接收中")

    def _reduce_music_volume_for_recording(self):
        """当开始录音时降低音乐音量"""
        self._reduce_music_volume("录音进行中")

    def _restore_music_volume(self, reason="unknown"):
        """恢复音乐音量

        Args:
            reason: 恢复音量的原因，用于日志记录
        """
        if not self.music_player or not self.volume_reduced or self.original_music_volume is None:
            return

        try:
            self.music_player.set_volume(self.original_music_volume)
            logger.info(f"{reason}，音乐音量恢复到 {self.original_music_volume}")
            self.volume_reduced = False
            self.original_music_volume = None

        except Exception as e:
            logger.error(f"恢复音乐音量时出错: {e}")

    def _schedule_volume_restore(self, reason="音频包接收结束"):
        """安排音量恢复（延迟执行）

        Args:
            reason: 恢复音量的原因，用于日志记录
        """
        # 取消之前的定时器
        if self.volume_restore_timer:
            self.volume_restore_timer.cancel()

        # 设置新的定时器，2秒后恢复音量
        self.volume_restore_timer = threading.Timer(2.0, lambda: self._restore_music_volume(reason))
        self.volume_restore_timer.start()

    def start_recording(self):
        """开始录音"""
        if self.recording:
            logger.warning("录音已在进行中")
            return

        # 设置录音标志
        self.recording = True

        # 降低音乐音量（如果正在播放音乐）
        self._reduce_music_volume_for_recording()
        self.recording_volume_reduced = True

        # 发布录音状态
        self._publish_status("recording")

        # 启动录音线程
        self.recording_thread = threading.Thread(target=self._record_audio_worker)
        self.recording_thread.daemon = True
        self.recording_thread.start()

        logger.info("开始录音")

    def stop_recording(self):
        """停止录音"""
        if not self.recording:
            logger.warning("没有正在进行的录音")
            return

        # 清除录音标志
        self.recording = False

        # 恢复音乐音量（如果之前因录音而降低了音量）
        if self.recording_volume_reduced:
            # 检查是否还有其他原因需要保持低音量（如正在接收音频包）
            if not self.audio_packet_receiving:
                self._restore_music_volume("录音结束")
            self.recording_volume_reduced = False

        # 发布在线状态
        self._publish_status("online")

        logger.info("停止录音")

    def _calculate_energy(self, audio_data):
        """计算音频数据的能量"""
        try:
            # 将字节数据转换为短整型数组
            as_ints = np.frombuffer(audio_data, dtype=np.int16)

            # 检查是否有有效数据
            if len(as_ints) == 0:
                return 0.0

            # 使用float64避免溢出，并确保值为正
            squared = np.square(as_ints.astype(np.float64))
            mean_squared = np.mean(squared)

            # 防止负值或零值
            if mean_squared <= 0:
                return 0.0

            # 计算均方根能量
            return np.sqrt(mean_squared)

        except Exception as e:
            logger.error(f"计算能量时出错: {e}")
            return 0.0

    def _record_audio_worker(self):
        """录音工作线程"""
        try:
            # 打开麦克风流
            self.mic_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.config["audio_channels"],
                rate=self.config["audio_sample_rate"],
                input=True,
                frames_per_buffer=self.config["audio_chunk_size"]
            )

            logger.info(f"麦克风已打开，采样率: {self.config['audio_sample_rate']}Hz, 通道数: {self.config['audio_channels']}")

            # 默音检测变量
            silence_start = None
            max_recording_time = time.time() + self.config["recording_timeout"]

            # 录音状态跟踪
            speech_detected = False  # 是否检测到过语音
            recording_start_time = time.time()  # 录音开始时间

            # 录音循环
            while self.recording:
                # 检查最大录音时长
                if time.time() > max_recording_time:
                    logger.info("达到最大录音时长，停止录音")
                    break

                # 读取音频数据
                audio_data = self.mic_stream.read(self.config["audio_chunk_size"], exception_on_overflow=False)

                # 计算音频能量
                energy = self._calculate_energy(audio_data)

                # 检测是否有语音
                if energy >= self.config["silence_threshold"]:
                    # 检测到语音
                    if not speech_detected:
                        speech_detected = True
                        logger.info(f"检测到语音开始，能量: {energy:.2f}")

                    # 重置静默计时
                    silence_start = None
                else:
                    # 检测到静默
                    if silence_start is None:
                        silence_start = time.time()
                        logger.debug(f"检测到静默开始，能量: {energy:.2f}")
                    else:
                        # 计算静默持续时间
                        silence_duration = time.time() - silence_start

                        # 根据是否已检测到语音选择不同的静默阈值
                        if speech_detected:
                            # 已检测到语音，使用较短的静默阈值
                            silence_threshold = self.config["speech_silence_duration"]
                            threshold_name = "speech_silence_duration"
                        else:
                            # 未检测到语音，使用较长的初始静默阈值
                            silence_threshold = self.config["initial_silence_duration"]
                            threshold_name = "initial_silence_duration"

                        # 检查是否超过静默阈值
                        if silence_duration > silence_threshold:
                            logger.info(f"检测到静默持续 {silence_duration:.2f} 秒，超过{threshold_name} ({silence_threshold}秒)，停止录音")
                            break

                # 使用Opus编码
                encoded_data = self.encoder.encode(audio_data, self.config["audio_chunk_size"])

                # 通过UDP发送
                self._send_audio_udp(encoded_data)

                # 可选：保存原始音频数据（用于调试）
                if self.config["debug"]:
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    filename = f"{self.config['audio_save_path']}/{timestamp}_{self.sequence_number}.raw"
                    with open(filename, 'wb') as f:
                        f.write(audio_data)

        except Exception as e:
            logger.error(f"录音时出错: {e}")
        finally:
            # 关闭麦克风流
            if self.mic_stream:
                self.mic_stream.stop_stream()
                self.mic_stream.close()
                self.mic_stream = None

            self.recording = False
            self._publish_status("online")
            logger.info("录音线程已结束")

    def _send_audio_udp(self, encoded_data):
        """通过UDP发送编码后的音频数据

        Args:
            encoded_data: 编码后的音频数据
        """
        if not self.udp_socket:
            return

        try:
            # 增加序列号
            self.sequence_number += 1

            # 创建头部（包含序列号、数据长度和标记位）
            header = self.sequence_number.to_bytes(4, byteorder='big') + len(encoded_data).to_bytes(2, byteorder='big')

            # 组合数据包
            packet = header + encoded_data

            # 根据模式选择发送目标
            if self.config["stt_mode"] and self.config["stt_bridge_ip"]:
                # STT桥接模式：发送到STT桥接处理器
                self.udp_socket.sendto(packet, (self.config["stt_bridge_ip"], self.config["stt_bridge_port"]))
                if self.config["debug"] and self.sequence_number % 100 == 0:
                    logger.debug(f"已发送音频数据包到STT桥接处理器，序列号: {self.sequence_number}, 大小: {len(packet)} 字节")
            elif self.config["server_ip"]:
                # 正常模式：发送到服务器
                self.udp_socket.sendto(packet, (self.config["server_ip"], self.config["server_udp_port"]))
                if self.config["debug"] and self.sequence_number % 100 == 0:
                    logger.debug(f"已发送音频数据包到服务器，序列号: {self.sequence_number}, 大小: {len(packet)} 字节")
            else:
                logger.warning("未设置服务器IP或STT桥接处理器IP，无法发送音频数据")
                return

        except Exception as e:
            logger.error(f"发送UDP音频数据时出错: {e}")

    def _play_audio(self, audio_bytes):
        """播放音频数据（用于MQTT接收的音频）"""
        try:
            # 将字节转换为NumPy数组
            audio_data = np.frombuffer(audio_bytes, dtype=np.int16)

            # 根据音量设置调整音频数据
            try:
                # 获取当前音量设置
                volume_percent = DEFAULT_CONFIG["audio_settings"]["general_volume"] / 100.0

                # 调整音量
                if volume_percent != 0.5:  # 如果不是默认音量(50%)
                    # 使用float64进行计算以避免溢出，然后转回int16
                    audio_data = np.clip(
                        audio_data.astype(np.float64) * (volume_percent * 2),
                        -32768, 32767  # int16的范围
                    ).astype(np.int16)

                    logger.debug(f"已调整音频音量: {int(volume_percent * 100)}%")
            except Exception as e:
                logger.error(f"调整音量失败: {e}")

            # 打开临时扬声器流
            speaker = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.config["audio_channels"],
                rate=self.config["audio_sample_rate"],
                output=True
            )

            # 播放音频
            speaker.write(audio_data.tobytes())

            # 关闭扬声器流
            speaker.stop_stream()
            speaker.close()

            logger.info("音频播放完成")

        except Exception as e:
            logger.error(f"播放音频时出错: {e}")

    def _play_wav_file(self, filename):
        """播放WAV文件（轻量级实现，用于短小提示音）"""
        try:
            # 检查文件是否存在
            if not os.path.exists(filename):
                logger.error(f"WAV文件不存在: {filename}")
                return False

            # 打开WAV文件
            with wave.open(filename, 'rb') as wf:
                # 获取WAV文件参数
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()

                # 获取当前音量设置
                volume_percent = DEFAULT_CONFIG["audio_settings"]["general_volume"] / 100.0

                logger.debug(f"播放WAV文件: {filename}, 采样率: {sample_rate}Hz, 通道数: {channels}, 音量: {int(volume_percent * 100)}%")

                # 打开临时扬声器流
                speaker = self.audio.open(
                    format=self.audio.get_format_from_width(sample_width),
                    channels=channels,
                    rate=sample_rate,
                    output=True
                )

                # 读取所有数据
                all_data = wf.readframes(wf.getnframes())

                # 如果需要调整音量
                if volume_percent != 0.5:  # 如果不是默认音量(50%)
                    try:
                        # 将字节转换为NumPy数组
                        audio_data = np.frombuffer(all_data, dtype=np.int16)

                        # 调整音量
                        audio_data = np.clip(
                            audio_data.astype(np.float64) * (volume_percent * 2),
                            -32768, 32767  # int16的范围
                        ).astype(np.int16)

                        # 转回字节
                        all_data = audio_data.tobytes()
                    except Exception as e:
                        logger.error(f"调整WAV文件音量失败: {e}")

                # 播放数据
                speaker.write(all_data)

                # 关闭扬声器流
                speaker.stop_stream()
                speaker.close()

                logger.debug(f"WAV文件播放完成: {filename}")
                return True

        except Exception as e:
            logger.error(f"播放WAV文件时出错: {filename}, {e}")
            return False

    def _audio_playback_worker(self):
        """音频播放工作线程 - 基于xiaozhi-esp-1.6.0优化"""
        try:
            # 创建UDP接收套接字
            recv_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            recv_socket.bind(('0.0.0.0', self.config["server_udp_receive_port"]))  # 使用专门的音频接收端口
            recv_socket.settimeout(0.5)  # 设置超时，以便能够检查running标志

            # 设置接收缓冲区大小，提高性能
            recv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)  # 1MB

            logger.info(f"UDP接收套接字已绑定到端口 {self.config['server_udp_receive_port']}")

            # 音频包队列 - 参考xiaozhi-esp-1.6.0的实现
            # 最大队列长度 = 1000ms / 帧时长 (增加到1秒的缓冲)
            frame_duration_ms = 60  # 假设每帧60ms
            max_queue_size = int(1000 / frame_duration_ms)
            audio_queue = Queue(maxsize=200)  # 增加队列大小到200，提供更大的缓冲区

            # 序列号跟踪
            expected_seq_num = None
            last_packet_time = time.time()

            # 打开扬声器流（按需打开）
            speaker_stream = None

            # 创建解码线程
            decode_thread_running = True

            def decode_and_play_worker():
                """解码和播放工作线程 - 分离解码和播放过程"""
                nonlocal speaker_stream

                while decode_thread_running:
                    try:
                        # 从队列获取音频包，最多等待0.5秒
                        try:
                            packet_data = audio_queue.get(timeout=0.5)
                        except queue.Empty:
                            continue

                        # 解析数据
                        seq_num, is_raw_pcm, encoded_data = packet_data

                        # 根据数据类型处理
                        if is_raw_pcm:
                            # 原始PCM数据，无需解码
                            decoded_data = encoded_data
                        else:
                            # Opus编码数据，需要解码
                            try:
                                decoded_data = self.decoder.decode(encoded_data, self.config["audio_chunk_size"])
                            except Exception as e:
                                logger.error(f"解码音频数据失败，序列号: {seq_num}, 错误: {e}")
                                audio_queue.task_done()
                                continue

                        # 根据音量设置调整音频数据 - 对所有音频数据应用音量调整
                        try:
                            # 获取当前音量设置 - 使用DEFAULT_CONFIG以支持实时更新
                            volume_percent = DEFAULT_CONFIG["audio_settings"]["general_volume"] / 100.0

                            # 如果不是默认音量(50%)，则调整
                            if volume_percent != 0.5:
                                # 将字节数据转换为numpy数组
                                audio_array = np.frombuffer(decoded_data, dtype=np.int16)

                                # 调整音量 (乘以音量百分比的2倍，使50%对应原始音量)
                                # 使用float64进行计算以避免溢出，然后转回int16
                                audio_array = np.clip(
                                    audio_array.astype(np.float64) * (volume_percent * 2),
                                    -32768, 32767  # int16的范围
                                ).astype(np.int16)

                                # 转回字节数据
                                decoded_data = audio_array.tobytes()

                                if self.config["debug"] and seq_num % 500 == 0:
                                    logger.debug(f"已调整音频音量: {int(volume_percent * 100)}%")
                        except Exception as e:
                            logger.error(f"调整音量失败: {e}")

                        # 按需打开扬声器流
                        if not speaker_stream:
                            try:
                                speaker_stream = self.audio.open(
                                    format=pyaudio.paInt16,
                                    channels=self.config["audio_channels"],
                                    rate=self.config["audio_sample_rate"],
                                    output=True,
                                    frames_per_buffer=self.config["audio_chunk_size"]
                                )
                                logger.info("扬声器已打开")
                            except Exception as e:
                                logger.error(f"打开扬声器失败: {e}")
                                audio_queue.task_done()
                                continue

                        # 播放音频
                        try:
                            speaker_stream.write(decoded_data)
                            if self.config["debug"] and seq_num % 100 == 0:
                                logger.debug(f"已播放音频数据包，序列号: {seq_num}")
                        except Exception as e:
                            logger.error(f"播放音频失败，序列号: {seq_num}, 错误: {e}")
                            # 尝试重新打开扬声器
                            try:
                                if speaker_stream:
                                    speaker_stream.stop_stream()
                                    speaker_stream.close()
                                speaker_stream = self.audio.open(
                                    format=pyaudio.paInt16,
                                    channels=self.config["audio_channels"],
                                    rate=self.config["audio_sample_rate"],
                                    output=True,
                                    frames_per_buffer=self.config["audio_chunk_size"]
                                )
                                logger.info("扬声器已重新打开")
                            except:
                                pass

                        # 标记任务完成
                        audio_queue.task_done()

                    except Exception as e:
                        logger.error(f"解码和播放线程出错: {e}")
                        time.sleep(0.1)

            # 启动解码和播放线程
            decode_thread = threading.Thread(target=decode_and_play_worker, daemon=True)
            decode_thread.start()

            # 主接收循环
            while self.running:
                try:
                    # 接收UDP数据
                    data, addr = recv_socket.recvfrom(4096)

                    # 更新最后接收包的时间
                    last_packet_time = time.time()

                    # 检测到音频包，降低音乐音量
                    if not self.audio_packet_receiving:
                        self.audio_packet_receiving = True
                        self._reduce_music_volume_for_audio_packet()

                    # 更新最后音频包时间
                    self.last_audio_packet_time = last_packet_time

                    # 检查数据包长度
                    if len(data) < 6:
                        logger.warning(f"收到无效的UDP数据包，长度: {len(data)}")
                        continue

                    # 解析头部
                    seq_num = int.from_bytes(data[0:4], byteorder='big')
                    data_len = int.from_bytes(data[4:6], byteorder='big')

                    # 检查数据包完整性
                    if len(data) < 6 + data_len:
                        logger.warning(f"UDP数据包不完整，期望长度: {6 + data_len}，实际长度: {len(data)}")
                        continue

                    # 序列号检查 - 参考xiaozhi-esp-1.6.0的实现
                    if expected_seq_num is not None:
                        if seq_num < expected_seq_num:
                            logger.warning(f"收到过期的音频包，序列号: {seq_num}，期望: {expected_seq_num}")
                            continue
                        elif seq_num > expected_seq_num:
                            # 检测到丢包
                            missed_packets = seq_num - expected_seq_num
                            if missed_packets > 1:
                                logger.warning(f"检测到丢包，丢失 {missed_packets} 个包，从 {expected_seq_num} 到 {seq_num-1}")

                    # 更新期望的下一个序列号
                    expected_seq_num = seq_num + 1

                    # 检查是否有标记位（新格式）
                    if len(data) > 6 and len(data) >= 7 + data_len:
                        is_raw_pcm = data[6] == 1  # 读取标记位: 0=Opus, 1=PCM
                        encoded_data = data[7:7+data_len]
                    else:
                        # 兼容旧格式（无标记位）
                        is_raw_pcm = False
                        encoded_data = data[6:6+data_len]

                    # 检查队列是否已满
                    if audio_queue.full():
                        # 队列已满，丢弃最旧的包
                        try:
                            audio_queue.get_nowait()
                            audio_queue.task_done()
                            if self.config["debug"] and seq_num % 100 == 0:
                                logger.debug("音频队列已满，丢弃最旧的包")
                        except:
                            pass

                    # 将数据放入队列
                    audio_queue.put((seq_num, is_raw_pcm, encoded_data))

                except socket.timeout:
                    # 超时，继续循环
                    # 检查是否长时间没有收到数据包
                    if time.time() - last_packet_time > 10:  # 10秒无数据
                        if expected_seq_num is not None:
                            logger.info("长时间未收到音频包，重置序列号跟踪")
                            expected_seq_num = None

                    # 检查是否需要恢复音乐音量（2秒内没有收到音频包）
                    if self.audio_packet_receiving and time.time() - self.last_audio_packet_time > 2.0:
                        self.audio_packet_receiving = False
                        # 只有在没有录音进行时才恢复音量
                        if not self.recording:
                            self._schedule_volume_restore("音频包接收结束")
                    pass
                except Exception as e:
                    logger.error(f"接收音频时出错: {e}")
                    time.sleep(0.1)

        except Exception as e:
            logger.error(f"音频播放线程出错: {e}")
        finally:
            # 停止解码线程
            decode_thread_running = False
            if decode_thread.is_alive():
                decode_thread.join(timeout=2.0)

            # 关闭套接字和扬声器
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

            logger.info("音频播放线程已结束")

    def discover_server(self, timeout=5):
        """发现服务器"""
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_sock.settimeout(timeout)

        # 使用专门的发现服务端口，与音频传输端口分离
        broadcast_addr = ("255.255.255.255", self.config["discovery_port"])

        server_found = False
        server_ip, server_port = None, None

        try:
            # 确保discovery_request是字节类型
            discovery_request = self.config["discovery_request"]
            if isinstance(discovery_request, str):
                discovery_request = discovery_request.encode('utf-8')

            # 确保discovery_response_prefix是字节类型
            discovery_response_prefix = self.config["discovery_response_prefix"]
            if isinstance(discovery_response_prefix, str):
                discovery_response_prefix = discovery_response_prefix.encode('utf-8')

            logger.info("正在广播发现请求...")
            udp_sock.sendto(discovery_request, broadcast_addr)

            while not server_found:
                try:
                    data, addr = udp_sock.recvfrom(1024)
                    if data.startswith(discovery_response_prefix):
                        server_ip = addr[0]
                        server_port = int(data[len(discovery_response_prefix):])
                        server_found = True
                        logger.info(f"发现服务器: {server_ip}:{server_port}")
                except socket.timeout:
                    logger.warning("发现超时，未找到服务器")
                    break

        except Exception as e:
            logger.error(f"发现服务器时出错: {e}")
        finally:
            udp_sock.close()

        # 更新服务器信息
        if server_ip:
            self.config["server_ip"] = server_ip
        if server_port:
            self.config["server_udp_port"] = server_port

        return server_ip, server_port

    def cleanup(self):
        """清理资源"""
        logger.info("正在清理资源...")

        # 停止录音
        self.stop_recording()

        # 清理音量恢复定时器
        if self.volume_restore_timer:
            self.volume_restore_timer.cancel()
            self.volume_restore_timer = None

        # 重置音量控制状态
        self.audio_packet_receiving = False
        self.recording_volume_reduced = False
        self.volume_reduced = False
        self.original_music_volume = None

        # 停止音乐播放
        if self.music_player and self.music_player.is_playing:
            try:
                self.music_player.stop_playback()
                logger.info("音乐播放已停止")
            except Exception as e:
                logger.error(f"停止音乐播放时出错: {e}")

        # 设置运行标志为False
        self.running = False

        # 更新状态并保存当前配置到文件
        old_status = DEFAULT_CONFIG["system"]["status"]
        DEFAULT_CONFIG["system"]["status"] = "offline"

        # 只有在状态发生变化时才保存配置
        if old_status != "offline":
            save_config_to_file()
            logger.info("配置已保存到文件")

        # 发布离线状态
        if self.is_connected:
            self._publish_status("offline")

        # 停止MQTT客户端
        if self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except:
                pass

        # 关闭UDP套接字
        if self.udp_socket:
            try:
                self.udp_socket.close()
            except:
                pass

        # 关闭音频
        if self.audio:
            try:
                self.audio.terminate()
            except:
                pass

        # 停止唤醒词检测器
        if self.wake_word_detector:
            self.wake_word_detector.cleanup()

        logger.info("资源清理完成")

    def _on_wake_word_detected(self):
        """唤醒词检测回调"""
        logger.info("唤醒词检测回调触发")

        # 检查唤醒词是否仍然启用
        if not DEFAULT_CONFIG["wake_word"]["enabled"]:
            logger.warning("唤醒词已被禁用，忽略唤醒事件")
            return

        # 播放唤醒提示音
        wake_sound_path = "sound/pvwake.wav"
        # 在单独的线程中播放提示音，避免阻塞主流程
        threading.Thread(
            target=self._play_wav_file,
            args=(wake_sound_path,),
            daemon=True
        ).start()

        # 切换设备状态
        self.device_state = DEVICE_STATE_LISTENING

        # 获取唤醒前的音频数据
        pre_audio_data = self.wake_word_detector.get_audio_data()

        # 启动录音
        self.start_recording()

        # 发送唤醒前的音频数据
        for frame in pre_audio_data:
            self._send_audio_udp(frame)


def signal_handler(sig, frame):
    """信号处理函数"""
    logger.info("接收到中断信号，正在退出...")
    if client:
        client.cleanup()
    sys.exit(0)


if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="优化版Pi客户端")

    # MQTT配置参数
    parser.add_argument("--broker", help="MQTT代理地址", default=DEFAULT_CONFIG["mqtt"]["broker"])
    parser.add_argument("--port", type=int, help="MQTT代理端口", default=DEFAULT_CONFIG["mqtt"]["port"])
    parser.add_argument("--username", help="MQTT用户名")
    parser.add_argument("--password", help="MQTT密码")

    # 网络配置参数
    parser.add_argument("--server", help="服务器IP地址", default=DEFAULT_CONFIG["network"]["server_ip"])
    parser.add_argument("--udp-port", type=int, help="服务器音频传输UDP端口", default=DEFAULT_CONFIG["network"]["server_udp_port"])
    parser.add_argument("--udp-receive-port", type=int, help="客户端音频接收UDP端口", default=DEFAULT_CONFIG["network"]["server_udp_receive_port"])
    parser.add_argument("--discovery-port", type=int, help="服务发现UDP端口", default=DEFAULT_CONFIG["network"]["discovery_port"])

    # 设备配置参数
    parser.add_argument("--device-id", help="设备ID", default="yumi005")
    parser.add_argument("--device-password", help="设备密码", default="654321")

    # Porcupine配置参数
    parser.add_argument("--porcupine-access-key", help="Porcupine访问密钥", default=DEFAULT_CONFIG["wake_word"]["api_key"])
    parser.add_argument("--porcupine-keyword-path", help="Porcupine关键词路径", default=DEFAULT_CONFIG["wake_word"]["keyword_path"])

    args = parser.parse_args()

    # 创建配置
    config = {
        # MQTT配置
        "mqtt_broker": args.broker,
        "mqtt_port": args.port,
        "mqtt_username": args.username,
        "mqtt_password": args.password,

        # 网络配置
        "server_ip": args.server,
        "server_udp_port": args.udp_port,
        "server_udp_receive_port": args.udp_receive_port,
        "discovery_port": args.discovery_port,

        # 设备配置
        "device_id": args.device_id,

        # Porcupine配置
        "porcupine_access_key": args.porcupine_access_key,
        "porcupine_keyword_paths": [args.porcupine_keyword_path],
    }

    # 先加载配置文件
    load_config_from_file()

    # 检查是否需要更新设备ID和密码
    config_changed = False
    if DEFAULT_CONFIG["system"]["device_id"] != args.device_id:
        DEFAULT_CONFIG["system"]["device_id"] = args.device_id
        config_changed = True


    # 只有在配置实际发生变化时才保存
    if config_changed:
        save_config_to_file()

    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 创建并初始化客户端
    client = PiClient(config)
    client.initialize()

    # 尝试发现服务器
    client.discover_server()

    # 保持程序运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        client.cleanup()
