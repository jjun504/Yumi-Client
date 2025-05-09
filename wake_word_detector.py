#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
唤醒词检测模块 - 负责唤醒词检测功能
使用Porcupine实现
"""

import logging
import threading
import time
import struct
import pyaudio
from queue import Queue

# 尝试导入 pvporcupine 库
try:
    import pvporcupine
    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False
    logging.warning("pvporcupine 库未安装，唤醒词功能将被禁用")

# 配置日志
logger = logging.getLogger("WakeWordDetector")

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
        self.pre_buffer_duration = 0   # 预缓冲时长(秒)
        self.config = None             # 配置

    def initialize(self, config):
        """初始化Porcupine唤醒词检测器"""
        if not PORCUPINE_AVAILABLE:
            logger.error("无法初始化唤醒词检测器：pvporcupine库未安装")
            return False

        try:
            self.config = config

            access_key = config.get('porcupine_access_key')
            keyword_paths = config.get('porcupine_keyword_paths', [])
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
