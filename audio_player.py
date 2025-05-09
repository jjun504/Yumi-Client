import mpv
import threading
import time
import re
import subprocess
from youtubeAPI import YouTubeAPI
from config import config
from loguru import logger
import os
import pickle
from pathlib import Path
import asyncio
from yt_dlp import YoutubeDL



class AudioPlayer:
    def __init__(self, tts_manager=None, rechat_saver=None):
        self.chat_saver = rechat_saver  # 接收传入的聊天记录管理器
        self.player = mpv.MPV(video=False, terminal=False, volume=config.get("audio_settings.music_volume", 50))
        self.youtube_api = YouTubeAPI()
        self.music_volume = config.get("audio_settings.music_volume", 50)
        self.is_playing = False
        self.current_song = None
        self.play_history = []
        self.auto_play = True
        self.play_queue = []
        self.current_index = 0
        self.command = ""  # For receiving control commands
        self.play_thread = None  # Thread control variable
        self.history_file = Path('youtube/music_history.data')
        self.play_history = self.read_history()  # Initialize by reading history
        self.tts_manager = tts_manager  # 接收传入的TTS管理器
        self.auto_play_lock = threading.Lock()  # Lock to protect auto-play state
        # Start control thread
        threading.Thread(target=self.inter, daemon=True).start()

    def _get_local_audio_file(self, youtube_url):
        """
        使用 yt-dlp 下载最差音质的音频并转换为 WAV 格式，
        以获得更快响应速度并兼容播放。
        """
        # 创建下载目录
        download_dir = Path("downloads")
        download_dir.mkdir(exist_ok=True)

        # 生成基于时间戳的唯一文件名
        timestamp = time.strftime('%Y%m%d_%H%M%S', time.localtime())
        temp_file = download_dir / f"temp_{timestamp}"
        wav_file = download_dir / f"temp_{timestamp}.wav"

        logger.debug(f"开始下载音频: {youtube_url}")

        ydl_opts = {
            # 选择最差音频以加快下载速度
            'format': 'bestaudio/best',
            'outtmpl': str(temp_file),
            'nopart': True,
            'quiet': True,
            'no_warnings': True,
            # 使用FFmpeg提取音频并转换为WAV格式（更兼容）
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',  # 使用WAV格式，几乎所有FFmpeg都支持
                'preferredquality': '64',  # 数值越低音质越差但下载更快
            }],
            # 设置采样率为16000Hz以加快处理并与系统其他部分兼容
            'postprocessor_args': [
                '-ar', '16000'
            ]
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=True)
                title = info.get('title', 'Unknown')
                logger.debug(f"下载的音频标题: {title}")

            # 检查WAV文件是否存在
            if wav_file.exists():
                logger.success(f"成功下载并转换为WAV: {wav_file}")
                return str(wav_file)
            else:
                # 检查是否有其他格式的文件生成
                for ext in ['wav', 'mp3', 'aac', 'm4a', 'webm', 'opus']:
                    possible_file = download_dir / f"temp_{timestamp}.{ext}"
                    if possible_file.exists():
                        logger.success(f"成功下载音频: {possible_file}")
                        return str(possible_file)

                # 检查是否有原始下载文件（没有扩展名）
                if temp_file.exists():
                    logger.warning(f"找到原始下载文件，但未转换: {temp_file}")
                    return str(temp_file)

                logger.error("下载成功但找不到输出文件")
        except Exception as e:
            logger.error(f"下载或转换音频失败: {str(e)}")
            # 尝试查找是否有部分下载的文件
            if temp_file.exists():
                logger.warning(f"找到部分下载的文件: {temp_file}")
                return str(temp_file)

        return None

    def _get_direct_stream_url(self, youtube_url):
        """从YouTube URL获取直接可播放的音频流URL"""
        logger.debug(f"正在解析YouTube URL: {youtube_url}")

        # 检查是否是YouTube链接
        if 'youtube.com' not in youtube_url and 'youtu.be' not in youtube_url:
            return youtube_url, None

        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)

                if 'url' in info:
                    logger.debug("成功获取直接音频流URL")
                    return info['url'], info.get('title', 'Unknown')
                else:
                    logger.error("无法获取直接音频流URL")
                    return None, None
        except Exception as e:
            logger.error(f"解析YouTube URL时出错: {str(e)}")
            return None, None

    def inter(self):
        """Control command processing loop"""

        while True:
            if self.command != "":
                if self.command == "+":
                    self.increase_volume()
                elif self.command == "-":
                    self.decrease_volume()
                elif self.command == "p":
                    self.toggle_pause()
                elif self.command == "n":
                    self.play_next()
                elif self.command == "b":
                    self.play_previous()
                elif self.command == "a":
                    self.toggle_auto_play()

                self.command = ""  # Clear command after processing

            time.sleep(0.5)  # Reduce CPU usage

    def increase_volume(self, volume=10):
        """Increase volume"""
        self.player.volume += volume
        logger.info(f"[Music] Current volume: {self.player.volume}")
        config.set("audio_settings.music_volume", self.player.volume)

    def decrease_volume(self, volume=10):
        """Decrease volume"""
        self.player.volume -= volume
        logger.info(f"[Music] Current volume: {self.player.volume}")
        config.set("audio_settings.music_volume", self.player.volume)

    def change_command(self, command):
        """Change command"""
        self.command = command

    def _check_music_permission(self):
        """Check music playback permission"""
        if not config.get("music.enabled"):
            if self.is_playing:
                self.stop_playback()
            raise PermissionError("Music playback is disabled")

    def stop_playback(self):
        """Stop playback"""
        try:
            if self.is_playing:
                self.player.stop()
                self.is_playing = False
                logger.debug("[Music] Playback stopped")
                self.save_history()  # Save history when stopping playback
                logger.debug("History saved")
        except Exception as e:
            logger.error(f"Error stopping playback: {str(e)}")

    def get_recommendations(self, song_info):
        """Get recommended songs"""
        try:
            # Search for related songs based on current song name
            song_title = song_info['title']
            # Remove possible artist name, keep only song name
            parts = re.split(r'[-–—]', song_title, maxsplit=1)
            song_name = parts[-1].strip()
            logger.debug(f"Getting recommendations for song '{song_title}'")

            # Get more recommendations
            recommended_songs = self.youtube_api.search_song(f"{song_name}", max_results=30)

            # Get titles of all played songs
            # played_titles = [s['title'] for s in self.play_history]
            queue_titles = [s['title'] for s in self.play_queue]
            all_played = set(queue_titles + [song_info['title']])

            # Filter out already played songs
            new_recommendations = [
                song for song in recommended_songs
                if song['title'] not in all_played
            ]

            logger.debug(f"Found {len(new_recommendations)} unplayed recommended songs")
            return new_recommendations[:10]  # Ensure returning max 10 songs
        except Exception as e:
            logger.error(f"Error getting recommended songs: {str(e)}")
            return []

    def play_next(self):
        """Play next song"""
        try:
            if not self.play_queue:
                logger.debug("Play queue is empty")
                return

            # If it's the last song, get recommendations and append to queue
            if self.current_index >= len(self.play_queue) - 1:
                logger.debug("Getting recommended songs...")
                recommended_songs = self.get_recommendations(self.current_song)
                if recommended_songs:
                    self.play_queue.extend(recommended_songs)
                    logger.info("[Music] Recommended songs for you:")
                    for i, song in enumerate(recommended_songs, 1):
                        logger.info(f"{i}. {song['title']} - by {song['author']}")

            self.current_index += 1
            next_song = self.play_queue[self.current_index]
            logger.info(f"[Music] Switching to next: {next_song['title']} - by {next_song['author']}")

            # Stop current playback and start new one
            self.stop_playback()
            self.notify(f"Now playing {next_song['title']} by {next_song['author']}")

            self.chat_saver.save_chat_history(next_song, sender="assistant", message_type="music")
            self.play_audio_stream(self.play_queue, auto_recommend=True)
        except Exception as e:
            logger.error(f"Error switching to next song: {str(e)}")

    def play_previous(self):
        """Play previous song"""
        try:
            if not self.play_queue:
                logger.debug("Play queue is empty")
                return

            if self.current_index > 0:
                self.current_index -= 1
                previous_song = self.play_queue[self.current_index]
                logger.info(f"[Music] Switching to previous: {previous_song['title']} - by {previous_song['author']}")
                # Stop current playback and start new one
                self.stop_playback()
                self.notify(f"Now playing {previous_song['title']} by {previous_song['author']}")
                self.chat_saver.save_chat_history(previous_song, sender="assistant", message_type="music")
                self.play_audio_stream(self.play_queue, auto_recommend=True)
            else:
                logger.info("[Music] Already at the first song")
        except Exception as e:
            logger.error(f"Error switching to previous song: {str(e)}")

    def play_audio_stream(self, songs_info, auto_recommend=True, record_history=True):
        """Play audio stream list"""
        # If there's a running playback thread, stop it first
        if self.play_thread and self.play_thread.is_alive():
            self.stop_playback()
            time.sleep(0.1)  # Give some time for thread to end

        def play_thread():
            try:
                self._check_music_permission()

                # Update play queue logic remains unchanged
                if auto_recommend:
                    is_same_queue = (len(self.play_queue) > 0 and len(songs_info) > 0 and
                                    self.play_queue[0]['title'] == songs_info[0]['title'])
                    if not is_same_queue:
                        self.play_queue = songs_info.copy()
                        self.current_index = 0

                # Play current song
                while self.current_index < len(self.play_queue):
                    song_info = self.play_queue[self.current_index]
                    self._check_music_permission()
                    logger.success(f"[music] Now playing: {song_info['title']} - by {song_info['author']}")
                    self.player.pause = False
                    self.current_song = song_info

                    # Record history logic remains unchanged
                    if record_history:
                        if not self.play_history or self.play_history[-1]['title'] != song_info['title']:
                            self.play_history.append(song_info)
                            self.save_history()

                    # 关键修改：先下载音频文件到本地，再播放
                    local_file = self._get_local_audio_file(song_info["url"])
                    if not local_file:
                        # 如果下载失败，尝试使用直接流URL作为备选方案
                        logger.warning(f"下载音频文件失败，尝试使用直接流URL: {song_info['title']}")
                        direct_url, _ = self._get_direct_stream_url(song_info["url"])
                        if not direct_url:
                            logger.error(f"无法获取音频: {song_info['title']}")
                            self.current_index += 1
                            continue
                        # 使用直接流URL播放
                        self.player.play(direct_url)
                    else:
                        # 使用本地PCM文件播放
                        logger.debug(f"使用本地文件播放: {local_file}")
                        self.player.play(local_file)
                    self.player.wait_until_playing()
                    self.is_playing = True

                    # 完全重写歌曲播放持续检测逻辑
                    # 1. 不依赖MPV报告的持续时间，改用核心播放状态检测
                    time.sleep(5)  # 给播放一些初始时间

                    # 2. 使用core_idle和播放时间检测结合的方式
                    start_time = time.time()
                    song_ended = False
                    last_position = 0
                    stuck_count = 0
                    min_play_time = 60  # 最少播放60秒，除非手动停止

                    logger.debug(f"开始播放歌曲'{song_info['title']}'")

                    while self.is_playing:
                        # 检查是否手动停止
                        if not self.is_playing:
                            logger.debug("检测到手动停止")
                            break

                        try:
                            # 获取当前播放位置
                            current_position = self.player.time_pos

                            # 检查是否出现播放卡住
                            if current_position is not None and last_position == current_position:
                                stuck_count += 1
                                if stuck_count > 5:  # 连续5次相同位置，判断为卡住
                                    logger.debug(f"播放卡住在位置 {current_position:.1f}，准备结束")
                                    song_ended = True
                                    break
                            else:
                                stuck_count = 0

                            # 更新上次位置
                            if current_position is not None:
                                last_position = current_position

                            # 检查是否核心已空闲（播放已经结束）
                            if self.player.core_idle:
                                elapsed = time.time() - start_time
                                # 确保至少播放了最小时间
                                if elapsed >= min_play_time:
                                    logger.debug(f"检测到播放结束，核心空闲，已播放 {elapsed:.1f} 秒")
                                    song_ended = True
                                    break
                                else:
                                    logger.debug(f"检测到核心空闲，但未达到最小播放时间 ({elapsed:.1f}/{min_play_time}秒)")

                            # 每3分钟记录一次播放进度
                            elapsed = time.time() - start_time
                            if elapsed > 0 and elapsed % 180 < 1:
                                if current_position:
                                    logger.debug(f"歌曲'{song_info['title']}'已播放 {elapsed:.1f} 秒，位置 {current_position:.1f}")
                        except:
                            pass  # 忽略任何错误

                        # 短暂休眠以减少CPU使用
                        time.sleep(1)

                    # 正常结束播放
                    logger.debug(f"歌曲'{song_info['title']}'{'播放完成' if song_ended else '被手动停止'}")
                    self.stop_playback()

                    # 如果被手动停止，退出循环
                    if not song_ended:
                        break

                    # 如果歌曲正常播放完成，检查是否继续播放下一首
                    with self.auto_play_lock:
                        if not self.auto_play:
                            logger.info("[Music] Auto-play is off, not continuing to next song")
                            break

                        # 只有在自动播放开启时才增加索引并继续
                        self.current_index += 1

                        # 如果到达队列末尾且允许推荐，获取推荐歌曲
                        if self.current_index >= len(self.play_queue) and auto_recommend:
                            logger.debug("Trying to get recommendations...")
                            recommended_songs = self.get_recommendations(self.current_song)
                            if recommended_songs:
                                self.play_queue.extend(recommended_songs)
                                logger.debug(f"Added {len(recommended_songs)} recommended songs")
                                logger.info("[Music] Recommended songs for you:")
                                for i, song in enumerate(recommended_songs, 1):
                                    logger.info(f"{i}. {song['title']} - by {song['author']}")

            except PermissionError as e:
                logger.error(str(e))
            except Exception as e:
                logger.error(f"Playback thread error: {str(e)}")

        # Create new thread for playback
        self.play_thread = threading.Thread(target=play_thread)
        self.play_thread.start()

    def handle_song_end(self):
        """Handle song end event"""
        try:
            logger.debug("Handling song end")
            # Increment index
            self.current_index += 1

            # If reached end of queue, get recommendations
            if self.current_index >= len(self.play_queue) and self.auto_play:
                logger.debug("Queue finished, getting recommendations")
                logger.debug("Getting recommended songs...")
                recommended_songs = self.get_recommendations(self.current_song)

                if recommended_songs:
                    logger.debug(f"Got {len(recommended_songs)} recommended songs")
                    # Add recommended songs to queue
                    self.play_queue.extend(recommended_songs)
                    logger.info("[Music] Recommended songs for you:")
                    for i, song in enumerate(recommended_songs, 1):
                        logger.info(f"{i}. {song['title']} - by {song['author']}")
                else:
                    logger.error("[Music] No recommended songs found, playback ended")
                    return

            # Play next song
            if self.current_index < len(self.play_queue):
                logger.debug(f"Playing next song, index: {self.current_index}")
                # Stop current playback
                self.stop_playback()
                # Play next
                next_song = [self.play_queue[self.current_index]]
                self.play_audio_stream(next_song, auto_recommend=False)
            else:
                logger.debug("Play queue finished")
                self.stop_playback()

        except Exception as e:
            logger.error(f"Error handling song end: {str(e)}")

    def toggle_pause(self):
        """Toggle pause/resume state"""
        try:
            if self.is_playing:
                self.player.pause = True
                self.is_playing = False
                logger.info("[Music] Paused")
            else:
                self.player.pause = False
                self.is_playing = True
                logger.info("[Music] Resumed")
        except Exception as e:
            logger.error(f"Error toggling playback state: {str(e)}")

    def pause_audio(self):
        """Pause audio"""
        self.player.pause = True
        self.is_playing = False
        logger.info("[Music] Paused")

    def resume_audio(self):
        """Resume playback"""
        self.player.pause = False
        self.is_playing = True
        logger.info("[Music] Resumed")

    def toggle_auto_play(self):
        """Toggle auto-play state"""
        with self.auto_play_lock:
            self.auto_play = not self.auto_play
            status = "enabled" if self.auto_play else "disabled"
            logger.info(f"[Music] Auto-play {status}")
            self.notify(f"Auto-play {status}")

            # If auto-play is disabled, clear unplayed recommended songs
            if not self.auto_play and self.is_playing:
                self.play_queue = self.play_queue[:self.current_index + 1]
                logger.debug("Cleared unplayed recommended songs")

    def check_auto_play(self):
        """Check auto-play state"""
        with self.auto_play_lock:
            return self.auto_play

    def adjust_settings(self, cmd=None):
        """Handle audio control commands"""
        try:
            if cmd == "+":
                self.player.volume += 10
                logger.info(f"[Music] Current volume: {self.player.volume}")
            elif cmd == "-":
                self.player.volume -= 10
                logger.info(f"[Music] Current volume: {self.player.volume}")
            elif cmd.lower() == "p":
                self.toggle_pause()
            elif cmd.lower() == "n":
                self.play_next()
            elif cmd.lower() == "b":
                self.play_previous()
            elif cmd.lower() == "a":
                self.toggle_auto_play()
            else:
                logger.error("Invalid command")
        except Exception as e:
            logger.error(f"Operation error: {str(e)}")

    def play_single_song(self, song_name):
        """Play a single song"""
        try:
            # logger.debug("2")
            self._check_music_permission()
            # logger.debug("2.1")
            songs_info = self.youtube_api.search_song(song_name)
            if songs_info:
                # Add notification after successful playback
                song = songs_info[0]
                self.notify(f"Now playing {song['title']} by {song['author']}")
                self.chat_saver.save_chat_history(song, sender="assistant", message_type="music")
                threading.Thread(target=self.play_audio_stream, args=(songs_info,)).start()
                return True
            else:
                logger.error("No matching songs found")
                return False
        except PermissionError as e:
            logger.error(str(e))
            return False

    def play_last_song(self):
        """Play the last song"""
        try:
            self._check_music_permission()
            if self.play_history:
                # Only take the last song
                last_song = [self.play_history[-1]]
                logger.info(f"[Music] Playing from history: {last_song[0]['title']} - by {last_song[0]['author']}")

                # Reset playback state
                self.stop_playback()
                self.notify(f"Now playing {last_song[0]['title']} by {last_song[0]['author']}")
                self.chat_saver.save_chat_history(last_song, sender="assistant", message_type="music")
                # Play last song with auto recommendations enabled
                self.play_audio_stream(last_song, auto_recommend=True, record_history=False)
                return True
            else:
                logger.error("No history found")
                return False
        except PermissionError as e:
            logger.error(str(e))
            return False

    def play_playlist(self, playlist_index):
        """Play playlist"""
        try:
            self._check_music_permission()
            playlists = self.youtube_api.get_self_playlists()
            if playlists:
                if 0 <= playlist_index < len(playlists):
                    playlist_id = playlists[playlist_index]["id"]
                    songs = self.youtube_api.get_playlist_songs(playlist_id)
                    if songs:
                        self.notify(f"Now playing playlist {playlists[playlist_index]['title']}")
                        # chat_saver.save_chat_history(f"[System] play playlist: {playlists[playlist_index]['title']}")
                        threading.Thread(target=self.play_audio_stream, args=(songs,)).start()
                        return True
                    else:
                        logger.error("Playlist is empty")
                        return False
                else:
                    logger.error("Invalid playlist number")
                    return False
            else:
                logger.error("No playlists found")
                return False
        except PermissionError as e:
            logger.error(str(e))
            return False

    def save_history(self):
        """Save playback history to file"""
        try:
            # Ensure directory exists
            self.history_file.parent.mkdir(parents=True, exist_ok=True)

            # Save history
            with open(self.history_file, 'wb') as f:
                pickle.dump(self.play_history, f)
            logger.debug(f"Playback history saved to {self.history_file}")
        except Exception as e:
            logger.error(f"Error saving playback history: {str(e)}")

    def read_history(self) -> list:
        """Read playback history from file"""
        try:
            if self.history_file.exists():
                with open(self.history_file, 'rb') as f:
                    history = pickle.load(f)
                logger.debug(f"Read playback history, {len(history)} records")
                return history
            else:
                logger.debug("Playback history file doesn't exist, returning empty list")
                return []
        except Exception as e:
            logger.error(f"Error reading playback history: {str(e)}")
            return []

    def pause_and_remember_state(self):
        """Pause playback and remember current state for later resume"""
        try:
            self.was_playing = self.is_playing  # Remember if currently playing
            if self.is_playing:
                self.player.pause = True
                self.is_playing = False
                logger.info("[Music] Music paused")
                return True  # Return whether successfully paused playing music
            return False  # Return False if no music was playing
        except Exception as e:
            logger.error(f"Error pausing music: {str(e)}")
            return False

    def resume_if_was_playing(self):
        """Resume playback if was playing before"""
        try:
            if hasattr(self, 'was_playing') and self.was_playing:
                self.player.pause = False
                self.is_playing = True
                logger.info("[Music] Music resumed")
                return True  # Return whether successfully resumed playback
            return False  # Return False if wasn't playing before
        except Exception as e:
            logger.error(f"Error resuming music: {str(e)}")
            return False

    def notify(self, message, speak=True):
        """
        Display notification message and optionally speak it

        Args:
            message: Notification message
            speak: Whether to speak via TTS
        """
        logger.debug(f"Music notification: {message}")
        timestamp = time.strftime('%Y%m%d_%H%M%S', time.localtime())

        # Only play TTS if notification feature is enabled and no conversation is ongoing
        if speak and self.tts_manager and config.get("music.tts_notify") is True and config.get("state_flags.chat_active") is False and (config.get("state_flags.notification_active") is False):
            # Record current volume and temporarily lower background music
            current_volume = None
            if self.is_playing:
                current_volume = self.player.volume
                # Temporarily lower volume, but not below 40
                self.player.volume = 40

            try:
                # 根据TTS管理器类型选择合适的方法处理通知
                # self.chat_saver.save_chat_history(message, sender="assistant", audio_path=f"sound/volcano/{timestamp}.pcm")
                # self.tts_manager.text_to_speech(message, save_to_file=f"sound/volcano/{timestamp}.pcm")
                # if hasattr(self.tts_manager, 'text_to_speech'):
                #     # 对于Volcano TTS使用同步方法
                # elif hasattr(self.tts_manager, 'speech_synthesizer'):
                #     # 对于Azure TTS，使用SSML处理
                #     ssml_text = f"""
                #     <speak xmlns="http://www.w3.org/2001/10/synthesis"
                #         xmlns:mstts="http://www.w3.org/2001/mstts"
                #         xmlns:emo="http://www.w3.org/2009/10/emotionml"
                #         version="1.0" xml:lang="zh-CN">
                #         <voice name="zh-CN-XiaoxiaoMultilingualNeural"
                #             commasilence-exact="100ms" semicolonsilence-exact="100ms" enumerationcommasilence-exact="100ms">
                #             <mstts:express-as style="chat-casual" styledegree="0.5">
                #                 <lang xml:lang="zh-CN">
                #                     <prosody rate="+23.00%" pitch="+5.00%">{message}</prosody>
                #                 </lang>
                #             </mstts:express-as><s />
                #         </voice>
                #     </speak>
                #     """
                #     # 使用SSML方式播放
                #     task = self.tts_manager.speech_synthesizer.speak_ssml_async(ssml_text)
                #     result = task.get()
                # else:
                #     logger.warning("未找到可用的TTS方法，无法播放通知音频")

                # Restore original volume
                if current_volume is not None:
                    self.player.volume = current_volume
            except Exception as e:
                logger.error(f"TTS playback error: {str(e)}")
                # Ensure volume is restored regardless
                if current_volume is not None:
                    self.player.volume = current_volume

def main():
    """测试音频播放器功能"""
    from const_config import USE_AZURE, USE_VOLCANO
    import sys
    from  chat_saver import ChatSaver
    # 根据配置选择TTS后端

    from bytedanceTTS import TTSManager
    tts_manager = TTSManager()

    try:
        player = AudioPlayer(tts_manager, ChatSaver())

        print("\n1. 播放单首歌曲")
        print("2. 播放播放列表")
        print("3. 播放YouTube链接")
        print("4. 退出")

        choice = input("请选择功能: ")

        if choice == "1":
            song_name = input("请输入歌曲名称: ")
            try:
                player.play_single_song(song_name)
            except PermissionError as e:
                print(str(e))
        elif choice == "2":
            try:
                player.play_playlist(0)  # 播放第一个播放列表
            except PermissionError as e:
                print(str(e))
        elif choice == "3":
            url = input("请输入YouTube链接: ")
            try:
                # 使用本地下载方式
                print("正在下载音频文件...")
                local_file = player._get_local_audio_file(url)
                if local_file:
                    print(f"正在播放本地文件: {local_file}")
                    player.player.play(local_file)
                    player.player.wait_until_playing()
                    player.is_playing = True

                    # 等待播放结束或用户中断
                    try:
                        while player.is_playing:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        player.stop_playback()
                else:
                    print("尝试使用直接流URL...")
                    # 如果本地下载失败，尝试直接流
                    direct_url, title = player._get_direct_stream_url(url)
                    if direct_url:
                        print(f"正在播放: {title}")
                        player.player.play(direct_url)
                        player.player.wait_until_playing()
                        player.is_playing = True

                        # 等待播放结束或用户中断
                        try:
                            while player.is_playing:
                                time.sleep(1)
                        except KeyboardInterrupt:
                            player.stop_playback()
                    else:
                        print("无法获取音频")
            except Exception as e:
                print(f"播放错误: {str(e)}")
        elif choice == "4":
            pass
        else:
            print("无效选择")
    except Exception as e:
        print(f"程序错误: {str(e)}")

if __name__ == "__main__":
    main()
    # config.set(music_enable=False)
