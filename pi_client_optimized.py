#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优化版Pi客户端 - 负责音频录制、播放和数据传输
参考py-xiaozhi.py实现，专注于低延迟音频传输
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
import logging
import queue
from queue import Queue
import signal
import sys
import struct
import wave

# 尝试导入 pvporcupine 库
try:
    import pvporcupine
    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False
    logging.warning("pvporcupine 库未安装，唤醒词功能将被禁用")

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("PiClient")

# 设备状态常量
DEVICE_STATE_IDLE = 'idle'           # 空闲状态，等待唤醒
DEVICE_STATE_LISTENING = 'listening'  # 正在录音
DEVICE_STATE_PROCESSING = 'processing'  # 正在处理音频

# 全局配置变量
PV_API_KEY = "engq+3lVOO74PHIKEFTW0/d17wc9gVarMZWkjXZgxvGbqPV2q58koA=="
PORCUPINE_KEYWORD_PATH = "wakeword_source/hey_yumi.ppn"

EMBEDDED_CONFIG = {
    "system": {
        "device_id": "rasp1",
        "password": "654321",
        "user_id": "user001",
        "boot_time": None,  # 将在运行时设置
        "version": "1.0.0",
        "language": "chinese",
        "log_level": "DEBUG",
        "status": "offline",
        "last_update": None  # 将在运行时设置
    },
    "wake_word": {
        "enabled": True
    },
    "audio_settings": {
        "general_volume": 50,
        "music_volume": 50,
        "notification_volume": 50,
    }
}


class PorcupineWakeWordDetector:
    """Porcupine唤醒词检测器"""

    def __init__(self):
        """初始化唤醒词检测器"""
        self.porcupine = None          # Porcupine实例
        self.audio = None              # PyAudio实例
        self.stream = None             # 音频流
        self.detection_thread = None   # 检测线程
        self.running = False           # 运行标志
        self.paused = False            # 暂停标志
        self.callback = None           # 唤醒回调函数
        self.audio_queue = Queue(maxsize=100)  # 音频数据队列(用于保存唤醒前的音频)
        self.lock = threading.Lock()   # 线程锁
        self.pre_buffer = []           # 预缓冲区，存储唤醒前的音频
        self.pre_buffer_duration = 0.5  # 预缓冲时长(秒)
        self.config = None             # 配置

    def initialize(self, config):
        """初始化Porcupine唤醒词检测器"""
        if not PORCUPINE_AVAILABLE:
            logger.error("无法初始化唤醒词检测器：pvporcupine库未安装")
            return False

        try:
            self.config = config

            access_key = PV_API_KEY
            keyword_paths = config.get('porcupine_keyword_paths', [PORCUPINE_KEYWORD_PATH])
            sensitivity = config.get('porcupine_sensitivity', 0.5)

            if not access_key or not keyword_paths:
                logger.error("缺少Porcupine配置：access_key或keyword_paths")
                return False

            # 确保keyword_paths是列表
            if not isinstance(keyword_paths, list):
                keyword_paths = [keyword_paths]

            # 创建与关键词数量相匹配的敏感度列表
            sensitivities = [float(sensitivity)] * len(keyword_paths)

            logger.info(f"初始化Porcupine，关键词路径：{keyword_paths}，敏感度：{sensitivities}")

            # 创建Porcupine实例
            self.porcupine = pvporcupine.create(
                access_key=access_key,
                keyword_paths=keyword_paths,
                sensitivities=sensitivities
            )

            # 初始化音频
            self.audio = pyaudio.PyAudio()

            # 计算预缓冲区大小
            sample_rate = self.porcupine.sample_rate
            frame_length = self.porcupine.frame_length
            frames_per_second = sample_rate / frame_length
            self.pre_buffer_size = int(frames_per_second * self.pre_buffer_duration)

            logger.info(f"唤醒词检测初始化成功，采样率：{sample_rate}Hz，帧长：{frame_length}，预缓冲：{self.pre_buffer_duration}秒")
            return True

        except Exception as e:
            logger.error(f"初始化唤醒词检测器失败：{e}")
            return False

    def start_detection(self):
        """启动唤醒词检测"""
        if not PORCUPINE_AVAILABLE or not self.porcupine:
            logger.error("无法启动唤醒词检测：未初始化")
            return False

        try:
            # 打开音频流
            self.stream = self.audio.open(
                # input_device_index=0,
                rate=self.porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self.porcupine.frame_length
            )

            # 设置运行标志
            self.running = True
            self.paused = False

            # 创建并启动检测线程
            self.detection_thread = threading.Thread(target=self._detection_worker)
            self.detection_thread.daemon = True
            self.detection_thread.start()

            logger.info("唤醒词检测已启动")
            return True

        except Exception as e:
            logger.error(f"启动唤醒词检测失败：{e}")
            return False

    def stop_detection(self):
        """停止唤醒词检测"""
        with self.lock:
            self.running = False

        # 等待线程结束
        if self.detection_thread and self.detection_thread.is_alive():
            self.detection_thread.join(timeout=2.0)

        # 关闭音频流
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
            except:
                pass

        logger.info("唤醒词检测已停止")

    def pause_detection(self):
        """暂停唤醒词检测"""
        with self.lock:
            self.paused = True

        # 暂停音频流
        if self.stream:
            try:
                self.stream.stop_stream()
            except:
                pass

        logger.info("唤醒词检测已暂停")

    def resume_detection(self):
        """恢复唤醒词检测"""
        with self.lock:
            self.paused = False

        # 恢复音频流
        if self.stream:
            try:
                self.stream.start_stream()
            except:
                pass

        logger.info("唤醒词检测已恢复")

    def set_callback(self, callback):
        """设置唤醒回调函数"""
        self.callback = callback

    def _detection_worker(self):
        """唤醒词检测工作线程"""
        try:
            # 清空预缓冲区
            self.pre_buffer.clear()

            while self.running:
                # 检查是否暂停
                if self.paused:
                    time.sleep(0.1)
                    continue

                try:
                    # 读取音频数据
                    pcm = self.stream.read(self.porcupine.frame_length, exception_on_overflow=False)

                    # 保存到预缓冲区
                    self.pre_buffer.append(pcm)

                    # 保持预缓冲区大小
                    while len(self.pre_buffer) > self.pre_buffer_size:
                        self.pre_buffer.pop(0)

                    # 处理音频数据
                    pcm_unpacked = struct.unpack_from("h" * self.porcupine.frame_length, pcm)

                    # 检测唤醒词
                    result = self.porcupine.process(pcm_unpacked)

                    # 如果检测到唤醒词
                    if result >= 0:
                        logger.info(f"检测到唤醒词！索引：{result}")

                        # 将预缓冲区的音频放入队列
                        for frame in self.pre_buffer:
                            self.audio_queue.put(frame)

                        # 执行回调
                        if self.callback:
                            # 在新线程中执行回调，避免阻塞检测线程
                            threading.Thread(target=self.callback).start()

                except Exception as e:
                    logger.error(f"唤醒词检测出错：{e}")
                    time.sleep(0.1)

        except Exception as e:
            logger.error(f"唤醒词检测线程异常：{e}")
        finally:
            logger.info("唤醒词检测线程已结束")

    def get_audio_data(self):
        """获取保存的音频数据"""
        frames = []

        # 从队列中获取所有数据
        while not self.audio_queue.empty():
            try:
                frames.append(self.audio_queue.get_nowait())
                self.audio_queue.task_done()
            except:
                break

        return frames

    def cleanup(self):
        """清理资源"""
        # 停止检测
        self.stop_detection()

        # 释放Porcupine资源
        if self.porcupine:
            self.porcupine.delete()
            self.porcupine = None

        # 释放PyAudio资源
        if self.audio:
            self.audio.terminate()
            self.audio = None

        logger.info("唤醒词检测器资源已清理")

class PiClient:
    def __init__(self, config=None):
        """初始化Pi客户端"""
        # 复制嵌入式配置

        # 设置运行时值
        EMBEDDED_CONFIG["system"]["boot_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        EMBEDDED_CONFIG["system"]["last_update"] = time.time()


        # 默认配置 - 第一阶段：基本配置（不依赖于self.config）
        self.config = {
            # MQTT配置 - 参照dev_control.py
            "mqtt_broker": "broker.emqx.io",
            "mqtt_port": 1883,
            "mqtt_username": None,
            "mqtt_password": None,
            "mqtt_client_id": f"smart_assistant_87_{socket.gethostname()}",

            # 设备信息
            "device_id": EMBEDDED_CONFIG["system"]["device_id"],

            # 音频配置
            "audio_sample_rate": 24000,
            "audio_channels": 1,
            "audio_chunk_size": 960,  # 优化为Opus编码器推荐的帧大小
            "audio_format": "int16",

            # 主题配置
            "topic_prefix": "smart0337187",

            # UDP配置
            "server_ip": None,      # 将通过发现服务或手动设置
            "server_udp_port": 8884,        # 音频传输端口 (与 stt_stream_bridge_processor.py 默认端口一致)
            "server_udp_receive_port": 8885, # 音频接收端口 (默认为 server_udp_port + 1)
            "stt_bridge_ip": None,   # STT 桥接处理器 IP 地址
            "stt_bridge_port": 8884, # STT 桥接处理器端口 (默认与 server_udp_port 相同)

            # 发现服务配置
            "discovery_port": 50000,        # 发现服务端口，与音频传输端口分离
            "discovery_request": b"DISCOVER_SERVER_REQUEST",
            "discovery_response_prefix": b"DISCOVER_SERVER_RESPONSE_",

            # Porcupine配置
            "porcupine_access_key": PV_API_KEY,
            "porcupine_keyword_paths": [PORCUPINE_KEYWORD_PATH],  # 使用全局变量，确保是列表
            "porcupine_sensitivity": 0.5,

            # 唤醒词行为配置
            "auto_stop_recording": True,
            "recording_timeout": 15.0,  # 秒，最大录音时长
            "silence_threshold": 300,   # 默音能量阈值
            "initial_silence_duration": 3.0,  # 秒，唤醒后初始静默时间阈值
            "speech_silence_duration": 1.0,   # 秒，说话后静默时间阈值
            "pre_buffer_duration": 0,  # 秒，保存唤醒前的音频

            # 其他配置
            "audio_save_path": "recordings",
            "debug": False
        }

        # 更新配置（如果提供）
        if config:
            self.config.update(config)

        # 生成派生配置
        self._generate_derived_config()

        # # 创建音频保存目录（如果启用调试）
        # if self.config["debug"]["enabled"]:
        #     os.makedirs(self.config["debug"]["audio_save_path"], exist_ok=True)

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

    def _generate_derived_config(self):
        """生成派生配置"""
        device_id = self.config["device_id"]
        topic_prefix = self.config["topic_prefix"]

        # 添加MQTT主题
        self.config["command_topic"] = f"{topic_prefix}/client/command/{device_id}"
        self.config["audio_topic"] = f"{topic_prefix}/client/audio/{device_id}"
        self.config["status_topic"] = f"{topic_prefix}/client/status/{device_id}"
        self.config["config_topic"] = f"{topic_prefix}/client/config/{device_id}"

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

        logger.info(f"Pi客户端初始化完成，设备ID: {EMBEDDED_CONFIG['system']['device_id']}")

    def _setup_mqtt(self):
        """设置MQTT连接 - 参照dev_control.py"""
        # 创建MQTT客户端 - 使用 paho-mqtt 1.x 风格
        self.mqtt_client = mqtt.Client(self.config["mqtt_client_id"])
        logger.info(f"初始化MQTT客户端 {self.config['mqtt_client_id']}")


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

        # 连接到MQTT代理
        try:
            # 连接到MQTT代理
            logger.info(f"正在连接到 {self.config['mqtt_broker']}:{self.config['mqtt_port']}...")
            self.mqtt_client.connect(
                self.config["mqtt_broker"],
                self.config["mqtt_port"],
                60  # keepalive 60秒
            )

            # 启动MQTT循环
            self.mqtt_client.loop_start()
            logger.info("MQTT循环已启动")

        except Exception as e:
            logger.error(f"MQTT连接失败: {e}")

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
            client.subscribe(f"{self.config['topic_prefix']}/server/command/{self.config['device_id']}", qos=2)
            logger.info(f"已订阅主题: {self.config['command_topic']}")


            # 订阅配置主题，以接收服务器发送的配置更新
            config_topic = f"{self.config['topic_prefix']}/server/config/{self.config['device_id']}"
            client.subscribe(config_topic, qos=2)
            logger.info(f"已订阅配置主题: {config_topic}")

            # 发送在线状态
            self._publish_status("online")
            time.sleep(3)
            # 发送设备配置
            self._publish_config()
        else:
            logger.error(f"MQTT连接失败，返回码: {rc}")

    def _publish_config(self):
        """发布设备配置"""
        try:
            # 构建配置主题
            config_topic = f"{self.config['topic_prefix']}/client/config/{self.config['device_id']}"

            # 构建配置消息，包含device_id和完整配置
            config_message = {
                'device_id': self.config["device_id"],
                'config': EMBEDDED_CONFIG,
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

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """MQTT断开连接回调"""
        logger.warning(f"与MQTT代理断开连接，返回码: {rc}")

        # 尝试发布离线状态（虽然可能失败，但值得一试）
        try:
            # 构建状态消息
            self._publish_status("offline")
            time.sleep(3)
            logger.info("已发布离线状态")
        except Exception as e:
            logger.error(f"发布离线状态失败: {e}")

        self.is_connected = False
        # 如果是意外断开，尝试重新连接
        if rc != 0:
            logger.info("尝试重新连接...")

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

            # 处理特定配置更新格式: {config: "section.key", new_value: value}
            if "config" in message and "new_value" in message:
                path = message["config"]
                value = message["new_value"]

                # 更新 EMBEDDED_CONFIG
                if self._set_config_value(path, value):
                    config_changed = True
                    logger.info(f"更新配置: {path} = {value}")

            # 处理完整配置更新格式: {config: {section: {key: value}}}
            elif "config" in message and isinstance(message["config"], dict):
                config_data = message["config"]

                # 遍历配置数据
                for section, values in config_data.items():
                    if isinstance(values, dict):
                        for key, value in values.items():
                            # 跳过只读配置
                            if section == "system" and key in ["boot_time", "last_update"]:
                                continue

                            # 更新 EMBEDDED_CONFIG
                            path = f"{section}.{key}"
                            if self._set_config_value(path, value):
                                config_changed = True
            else:
                logger.warning("未识别的配置消息格式")

            # 如果配置有变化，更新时间戳并应用变更
            if config_changed:
                # 更新最后修改时间
                EMBEDDED_CONFIG["system"]["last_update"] = time.time()

                # 应用配置变更
                # self._apply_config_changes()

                logger.info("配置更新完成")
            else:
                logger.info("配置无变化")

        except json.JSONDecodeError:
            logger.error("配置消息不是有效的JSON格式")
        except Exception as e:
            logger.error(f"处理配置更新时出错: {e}")

    def _apply_config_changes(self):
        """应用配置变更，处理需要特殊操作的配置项"""
        try:
            # 只处理需要特殊操作的配置项：唤醒词检测器
            if "wake_word" in EMBEDDED_CONFIG:
                wake_word_config = EMBEDDED_CONFIG["wake_word"]

                # 检查是否启用/禁用唤醒词
                if "enabled" in wake_word_config:
                    enabled = wake_word_config["enabled"]
                    if enabled and not self.wake_word_detector and PORCUPINE_AVAILABLE:
                        # 启用唤醒词
                        logger.info("启用唤醒词检测")
                        self.wake_word_detector = PorcupineWakeWordDetector()

                        # 创建唤醒词检测器配置
                        detector_config = {
                            "porcupine_access_key": PV_API_KEY,
                            "porcupine_keyword_paths": [PORCUPINE_KEYWORD_PATH],  # 确保是列表
                            "porcupine_sensitivity": wake_word_config.get("sensitivity", 0.5),
                            "pre_buffer_duration": EMBEDDED_CONFIG.get("recording", {}).get("pre_buffer_duration", 0)
                        }

                        if self.wake_word_detector.initialize(detector_config):
                            self.wake_word_detector.set_callback(self._on_wake_word_detected)
                            self.wake_word_detector.start_detection()
                    elif not enabled and self.wake_word_detector:
                        # 禁用唤醒词
                        logger.info("禁用唤醒词检测")
                        self.wake_word_detector.cleanup()
                        self.wake_word_detector = None

            # 确保调试目录存在
            if EMBEDDED_CONFIG.get("debug", {}).get("enabled", False):
                audio_save_path = EMBEDDED_CONFIG.get("debug", {}).get("audio_save_path", "recordings")
                os.makedirs(audio_save_path, exist_ok=True)

        except Exception as e:
            logger.error(f"应用配置变更时出错: {e}")

    def _set_config_value(self, path, value):
        """设置配置项

        支持使用点号分隔的路径，例如 "system.language"

        Args:
            path: 配置项路径
            value: 要设置的值

        Returns:
            bool: 设置是否成功
        """
        parts = path.split('.')
        current = EMBEDDED_CONFIG

        try:
            # 遍历路径直到倒数第二个部分
            for i, part in enumerate(parts[:-1]):
                if part not in current:
                    # 如果路径不存在，创建一个新的字典
                    current[part] = {}
                elif not isinstance(current[part], dict):
                    # 如果路径存在但不是字典，替换为字典
                    current[part] = {}

                current = current[part]

            # 设置最后一个部分的值
            last_part = parts[-1]
            if last_part in current and current[last_part] == value:
                # 值没有变化，不需要更新
                return False

            # 更新 EMBEDDED_CONFIG 中的值
            current[last_part] = value
            return True

        except Exception as e:
            logger.error(f"设置配置项 {path} 失败: {e}")
            return False

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

    def _publish_status(self, status):
        """发布状态信息"""
        if not self.is_connected:
            logger.warning("MQTT未连接，无法发布状态")
            return

        try:
            # 创建状态消息
            message = {
                "device_id": self.config["device_id"],
                "ip": socket.gethostbyname(socket.gethostname()),
                "password": EMBEDDED_CONFIG["system"]["password"],
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

    def start_recording(self):
        """开始录音"""
        if self.recording:
            logger.warning("录音已在进行中")
            return

        # if not self.config["server_ip"]:
        #     logger.error("服务器IP未设置，无法开始录音")
        #     return

        # 设置录音标志
        self.recording = True

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

    def _play_audio(self, audio_bytes):
        """播放音频数据（用于MQTT接收的音频）"""
        try:
            # 将字节转换为NumPy数组
            audio_data = np.frombuffer(audio_bytes, dtype=np.int16)

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

                logger.debug(f"播放WAV文件: {filename}, 采样率: {sample_rate}Hz, 通道数: {channels}")

                # 打开临时扬声器流
                speaker = self.audio.open(
                    format=self.audio.get_format_from_width(sample_width),
                    channels=channels,
                    rate=sample_rate,
                    output=True
                )

                # 读取并播放数据
                chunk_size = 1024
                data = wf.readframes(chunk_size)

                while data:
                    speaker.write(data)
                    data = wf.readframes(chunk_size)

                # 关闭扬声器流
                speaker.stop_stream()
                speaker.close()

                logger.debug(f"WAV文件播放完成: {filename}")
                return True

        except Exception as e:
            logger.error(f"播放WAV文件时出错: {filename}, {e}")
            return False

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
            logger.info("正在广播发现请求...")
            udp_sock.sendto(self.config["discovery_request"], broadcast_addr)

            while not server_found:
                try:
                    data, addr = udp_sock.recvfrom(1024)
                    if data.startswith(self.config["discovery_response_prefix"]):
                        server_ip = addr[0]
                        server_port = int(data[len(self.config["discovery_response_prefix"]):])
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

        # 设置运行标志为False
        self.running = False

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
        if not EMBEDDED_CONFIG["wake_word"]["enabled"]:
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

        # 注意：不再使用固定时长的定时器
        # 录音将由_record_audio_worker中的默音检测自动停止

def signal_handler(sig, frame):
    """信号处理函数"""
    logger.info("接收到中断信号，正在退出...")
    if client:
        client.cleanup()
    sys.exit(0)

if __name__ == "__main__":
    # 使用默认配置
    config = {
        # MQTT配置
        "mqtt_broker": "broker.emqx.io",
        "mqtt_port": 1883,
        "mqtt_username": None,
        "mqtt_password": None,

        # 服务器配置
        "server_ip": None,
        "server_udp_port": 8884,
        "server_udp_receive_port": 8885,
        "discovery_port": 50000,

        # STT配置
        "stt_bridge_ip": None,
        "stt_bridge_port": 8884,
        "stt_mode": False,

        # 设备配置
        "device_id": "rasp1",
        "debug": False,

        # 默音检测参数
        "silence_threshold": 300,
        "initial_silence_duration": 3.0,
        "speech_silence_duration": 1.0,
        "recording_timeout": 15.0,

        # Porcupine配置
        "porcupine_access_key": PV_API_KEY,
        "porcupine_keyword_paths": PORCUPINE_KEYWORD_PATH
    }

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
