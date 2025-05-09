#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音乐播放模块 - 负责音乐播放功能
基于pi_mpv_player.py实现
"""

import logging
import threading
import time

# 尝试导入 mpv 和 yt_dlp 库，用于音乐播放
try:
    import mpv
    from yt_dlp import YoutubeDL
    MPV_AVAILABLE = True
except ImportError:
    MPV_AVAILABLE = False
    logging.warning("mpv 或 yt_dlp 库未安装，音乐播放功能将被禁用")

# 配置日志
logger = logging.getLogger("MusicPlayer")

class MusicPlayer:
    """音乐播放器类 - 基于pi_mpv_player.py实现"""

    def __init__(self):
        """初始化音乐播放器"""
        self.player = None
        self.is_playing = False
        self.current_title = None
        self.monitor_thread = None

    def get_direct_stream_url(self, url):
        """
        从URL（包括YouTube URL）获取直接可播放的音频流URL

        Args:
            url: 原始URL，可以是YouTube链接或直接音频流URL

        Returns:
            tuple: (直接流URL, 标题)
        """
        # 检查是否是YouTube链接
        if 'youtube.com' not in url and 'youtu.be' not in url:
            return url, None

        logger.info(f"正在解析YouTube URL: {url}")

        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if 'url' in info:
                    logger.info("成功获取直接音频流URL")
                    return info['url'], info.get('title', 'Unknown')
                else:
                    logger.error("无法获取直接音频流URL")
                    return None, None
        except Exception as e:
            logger.error(f"解析URL时出错: {str(e)}")
            return None, None

    def play_url(self, url, volume=50):
        """
        播放URL（可以是YouTube链接或直接音频流URL）

        Args:
            url: 音频URL
            volume: 音量（0-100）

        Returns:
            bool: 是否成功开始播放
        """
        # 停止当前播放
        self.stop_playback()

        # 获取直接流URL（如果是YouTube链接）
        stream_url, title = self.get_direct_stream_url(url)
        if not stream_url:
            logger.error(f"无法获取音频流URL: {url}")
            return False

        try:
            # 创建MPV实例
            self.player = mpv.MPV(video=False, terminal=False, volume=volume)

            # 播放音频
            logger.info(f"开始播放: {title if title else stream_url}")
            self.player.play(stream_url)
            self.player.wait_until_playing()
            self.is_playing = True
            self.current_title = title

            # 启动监控线程
            def monitor():
                while self.player and not self.player.core_idle and self.is_playing:
                    time.sleep(0.5)

                if self.is_playing:  # 如果不是手动停止的
                    logger.info("播放结束")
                    self.is_playing = False
                    self.current_title = None

            self.monitor_thread = threading.Thread(target=monitor)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()

            return True

        except Exception as e:
            logger.error(f"播放出错: {str(e)}")
            self.is_playing = False
            return False

    def stop_playback(self):
        """停止当前播放"""
        if self.player and self.is_playing:
            try:
                # 记录当前标题（如果有）
                title = self.current_title
                logger.info(f"停止播放: {title if title else '未知标题'}")

                self.player.terminate()
                logger.info("播放已停止")
            except Exception as e:
                logger.error(f"停止播放时出错: {str(e)}")
            finally:
                self.is_playing = False
                self.current_title = None
                self.player = None

    def set_volume(self, volume):
        """
        设置音量

        Args:
            volume: 音量（0-100）

        Returns:
            bool: 是否成功设置音量
        """
        if not self.player:
            logger.warning("没有活动的播放器，无法设置音量")
            return False

        try:
            self.player.volume = volume
            logger.info(f"音量已设置为: {volume}")
            return True
        except Exception as e:
            logger.error(f"设置音量时出错: {str(e)}")
            return False

    def pause_playback(self):
        """
        暂停当前播放

        Returns:
            bool: 是否成功暂停播放
        """
        if not self.player or not self.is_playing:
            logger.warning("没有活动的播放或已经暂停，无法暂停")
            return False

        try:
            # 记录当前标题（如果有）
            title = self.current_title
            logger.info(f"暂停播放: {title if title else '未知标题'}")

            self.player.pause = True
            # 注意：我们不设置 is_playing = False，因为我们仍然认为音乐在"播放"状态
            # 只是暂停了。这样可以确保恢复播放时能够正确恢复状态。
            logger.info("播放已暂停")
            return True
        except Exception as e:
            logger.error(f"暂停播放时出错: {str(e)}")
            return False

    def resume_playback(self):
        """
        恢复暂停的播放

        Returns:
            bool: 是否成功恢复播放
        """
        if not self.player:
            logger.warning("没有活动的播放器，无法恢复播放")
            return False

        try:
            # 记录当前标题（如果有）
            title = self.current_title
            logger.info(f"恢复播放: {title if title else '未知标题'}")

            self.player.pause = False
            # 确保设置 is_playing 状态为 true
            self.is_playing = True

            # 确保标题信息不会丢失
            if not self.current_title and title:
                self.current_title = title
                logger.info(f"恢复标题信息: {title}")

            logger.info("播放已恢复")
            return True
        except Exception as e:
            logger.error(f"恢复播放时出错: {str(e)}")
            return False

    def get_status(self):
        """
        获取播放状态

        Returns:
            dict: 包含播放状态的字典
        """
        is_paused = False
        if self.player:
            try:
                is_paused = self.player.pause
            except:
                pass

        return {
            "is_playing": self.is_playing,
            "is_paused": is_paused,
            "title": self.current_title,
            "volume": self.player.volume if self.player else 0
        }
