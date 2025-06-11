#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Music Player Module - Responsible for music playback functionality
Based on pi_mpv_player.py implementation
"""

import logging
import threading
import time

# Try to import mpv and yt_dlp libraries for music playback
try:
    import mpv
    from yt_dlp import YoutubeDL
    MPV_AVAILABLE = True
except ImportError:
    MPV_AVAILABLE = False
    logging.warning("mpv or yt_dlp library not installed, music playback functionality will be disabled")

# Configure logging
logger = logging.getLogger("MusicPlayer")

class MusicPlayer:
    """Music Player Class - Based on pi_mpv_player.py implementation"""

    def __init__(self):
        """Initialize music player"""
        self.player = None
        self.is_playing = False
        self.current_title = None
        self.monitor_thread = None
        self.completion_callback = None  # Callback function when song completes
        self.manually_stopped = False  # Flag to mark if manually stopped

    def get_direct_stream_url(self, url):
        """
        Get direct playable audio stream URL from URL (including YouTube URL)

        Args:
            url: Original URL, can be YouTube link or direct audio stream URL

        Returns:
            tuple: (direct stream URL, title)
        """
        # Check if it's a YouTube link
        if 'youtube.com' not in url and 'youtu.be' not in url:
            return url, None

        logger.info(f"Parsing YouTube URL: {url}")

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
                    logger.info("Successfully obtained direct audio stream URL")
                    return info['url'], info.get('title', 'Unknown')
                else:
                    logger.error("Unable to obtain direct audio stream URL")
                    return None, None
        except Exception as e:
            logger.error(f"Error parsing URL: {str(e)}")
            return None, None

    def play_url(self, url, volume=50):
        """
        Play URL (can be YouTube link or direct audio stream URL)

        Args:
            url: Audio URL
            volume: Volume (0-100)

        Returns:
            bool: Whether playback started successfully
        """
        # Stop current playback
        self.stop_playback()

        # Get direct stream URL (if it's a YouTube link)
        stream_url, title = self.get_direct_stream_url(url)
        if not stream_url:
            logger.error(f"Unable to get audio stream URL: {url}")
            return False

        try:
            # Create MPV instance
            self.player = mpv.MPV(video=False, terminal=False, volume=volume)

            # Play audio
            logger.info(f"Starting playback: {title if title else stream_url}")
            self.player.play(stream_url)
            self.player.wait_until_playing()
            self.is_playing = True
            self.current_title = title

            # Reset manual stop flag
            self.manually_stopped = False

            # Start monitoring thread
            def monitor():
                while self.player and self.is_playing:
                    try:
                        # Check player status
                        if self.player.core_idle:
                            # Player is idle, possibly playback completed or error occurred
                            break

                        # Check if paused (but not playback completed)
                        if hasattr(self.player, 'pause') and self.player.pause:
                            # Paused state, continue monitoring but don't exit
                            time.sleep(0.5)
                            continue

                        time.sleep(0.5)
                    except Exception as e:
                        logger.error(f"Monitoring thread error: {e}")
                        break

                # Monitoring thread exits, check if it's natural playback completion
                if self.is_playing and not self.manually_stopped:
                    logger.info("Song naturally completed playback")
                    self.is_playing = False
                    finished_title = self.current_title  # Save completed song title
                    self.current_title = None

                    # Call completion callback function
                    if self.completion_callback:
                        try:
                            self.completion_callback(finished_title)
                        except Exception as e:
                            logger.error(f"Error calling song completion callback: {e}")
                else:
                    logger.debug("Monitoring thread exited, but not natural playback completion (manually stopped or paused)")

            self.monitor_thread = threading.Thread(target=monitor)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()

            return True

        except Exception as e:
            logger.error(f"Playback error: {str(e)}")
            self.is_playing = False
            return False

    def stop_playback(self):
        """Stop current playback"""
        if self.player and self.is_playing:
            try:
                # Record current title (if any)
                title = self.current_title
                logger.info(f"Manually stopping playback: {title if title else 'Unknown title'}")

                # Set manual stop flag
                self.manually_stopped = True

                self.player.terminate()
                logger.info("Playback stopped")
            except Exception as e:
                logger.error(f"Error stopping playback: {str(e)}")
            finally:
                self.is_playing = False
                self.current_title = None
                self.player = None

    def set_volume(self, volume):
        """
        Set volume

        Args:
            volume: Volume (0-100)

        Returns:
            bool: Whether volume was set successfully
        """
        if not self.player:
            logger.warning("No active player, cannot set volume")
            return False

        try:
            self.player.volume = volume
            logger.info(f"Volume set to: {volume}")
            return True
        except Exception as e:
            logger.error(f"Error setting volume: {str(e)}")
            return False

    def pause_playback(self):
        """
        Pause current playback

        Returns:
            bool: Whether playback was paused successfully
        """
        if not self.player or not self.is_playing:
            logger.warning("No active playback or already paused, cannot pause")
            return False

        try:
            # Record current title (if any)
            title = self.current_title
            logger.info(f"Manually pausing playback: {title if title else 'Unknown title'}")

            # Set manual stop flag to prevent triggering next song
            self.manually_stopped = True

            self.player.pause = True
            # Note: We don't set is_playing = False, because we still consider music in "playing" state
            # just paused. This ensures proper state recovery when resuming playback.
            logger.info("Playback paused")
            return True
        except Exception as e:
            logger.error(f"Error pausing playback: {str(e)}")
            return False

    def resume_playback(self):
        """
        Resume paused playback

        Returns:
            bool: Whether playback was resumed successfully
        """
        if not self.player:
            logger.warning("No active player, cannot resume playback")
            return False

        try:
            # Record current title (if any)
            title = self.current_title
            logger.info(f"Resuming playback: {title if title else 'Unknown title'}")

            self.player.pause = False
            # Ensure is_playing status is set to true
            self.is_playing = True

            # Reset manual stop flag since we're resuming playback now
            self.manually_stopped = False

            # Ensure title information is not lost
            if not self.current_title and title:
                self.current_title = title
                logger.info(f"Restored title information: {title}")

            logger.info("Playback resumed")
            return True
        except Exception as e:
            logger.error(f"Error resuming playback: {str(e)}")
            return False

    def set_completion_callback(self, callback):
        """
        Set callback function for when song completes

        Args:
            callback: Callback function that receives one parameter (completed song title)
        """
        self.completion_callback = callback
        logger.debug("Song completion callback function has been set")

    def get_status(self):
        """
        Get playback status

        Returns:
            dict: Dictionary containing playback status
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
