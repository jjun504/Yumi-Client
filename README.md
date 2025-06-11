# Pi Client - Smart Voice Assistant Client

A Raspberry Pi-based smart voice assistant client that supports wake word detection, audio recording, MQTT communication, and music playback functionality.

## Features

### 🎤 Audio Processing
- **Wake Word Detection**: Local wake word detection using Porcupine engine
- **Audio Recording**: High-quality audio recording and real-time transmission
- **Audio Playback**: Support for audio playback and music streaming
- **Smart Volume Control**: Automatic music volume adjustment during recording and audio reception

### 🌐 Network Communication
- **MQTT Communication**: Real-time command and status synchronization with server
- **UDP Audio Transmission**: Efficient audio data transmission
- **Server Discovery**: Automatic discovery of servers on the network
- **STT Bridge Mode**: Support for Speech-to-Text bridge processing

### 🎵 Music Playback
- **YouTube Playback**: Direct playback of YouTube links
- **Volume Control**: Independent music volume control
- **Playback Control**: Play, pause, stop, and resume functionality
- **Auto-Continue**: Automatic request for next song after completion

### ⚙️ Configuration Management
- **JSON Configuration**: All settings stored in `config.json` file
- **Dynamic Configuration**: Runtime configuration updates supported
- **Device Management**: Support for multiple device ID switching

## System Requirements

### Hardware Requirements
- Raspberry Pi (Pi 4 or higher recommended)
- USB microphone or audio input device
- Audio output device (speakers/headphones)
- Network connection (WiFi or Ethernet)

### Software Requirements
- Python 3.7+
- PyAudio (audio processing)
- ALSA audio system configuration

## Installation Guide

### 1. Clone the Project
```bash
git clone <repository-url>
cd pi_client
```

### 2. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 3. Install System Dependencies

#### Ubuntu/Debian:
```bash
sudo apt update
sudo apt install portaudio19-dev python3-pyaudio alsa-utils
```

#### Music Playback (Optional):
```bash
# Install mpv and yt-dlp
sudo apt install mpv
pip install python-mpv yt-dlp
```

### 4. Audio Device Configuration

#### Configure ALSA Audio Devices:
```bash
# List available audio devices
aplay -l
arecord -l

# Edit ALSA configuration (optional)
sudo nano /etc/asound.conf
```

Example ALSA configuration:
```
pcm.input {
    type hw
    card 2
    device 0
}

pcm.output {
    type hw
    card 0
    device 0
}

pcm.!default {
    type asym
    playback.pcm "output"
    capture.pcm "input"
}
```

### 5. Wake Word Files
Ensure wake word files exist:
```bash
# Check wake word files
ls wakeword_source/
# Should contain: hello_chris.ppn, hello_chris_pi.ppn
```

### 6. Audio Files
Ensure notification sound files exist:
```bash
# Check audio files
ls sound/
# Should contain: pvwake.wav
```

## Configuration

### Configuration File Structure (`config.json`)

```json
{
    "system": {
        "device_id": "your_device_id",    // Unique device identifier
        "password": "device_password",     // Device password
        "user_id": "user_id",             // User ID
        "model": "raspberry_pi",          // Device model
        "version": "1.0.0",               // Version number
        "log_level": "DEBUG"              // Log level
    },
    "wake_word": {
        "enabled": true,                  // Enable wake word detection
        "api_key": "your_porcupine_key", // Porcupine API key
        "keyword_path": "wakeword_source/hello_chris_pi.ppn",
        "sensitivity": 0.5                // Wake word sensitivity (0.0-1.0)
    },
    "audio_settings": {
        "sample_rate": 24000,             // Audio sample rate
        "channels": 1,                    // Audio channels
        "chunk_size": 960,                // Audio chunk size
        "general_volume": 50,             // General volume
        "music_volume": 50,               // Music volume
        "notification_volume": 50         // Notification volume
    },
    "mqtt": {
        "broker": "broker.emqx.io",       // MQTT broker address
        "port": 1883,                     // MQTT port
        "client_id_prefix": "smart_assistant_87",
        "topic_prefix": "smart0337187"    // MQTT topic prefix
    },
    "network": {
        "server_ip": "192.168.1.100",     // Server IP address
        "server_udp_port": 8884,          // UDP audio transmission port
        "server_udp_receive_port": 8885,  // UDP audio reception port
        "discovery_port": 50000,          // Server discovery port
        "stt_mode": false                 // STT bridge mode
    },
    "recording": {
        "auto_stop": true,                // Auto-stop recording
        "timeout": 15.0,                  // Recording timeout (seconds)
        "silence_threshold": 300,         // Silence threshold
        "initial_silence_duration": 3.0,  // Initial silence duration
        "speech_silence_duration": 1.0    // Post-speech silence duration
    },
    "debug": {
        "enabled": false                  // Debug mode
    }
}
```

### Important Configuration Items

1. **device_id**: Unique device identifier for MQTT communication
2. **wake_word.api_key**: Porcupine wake word detection API key
3. **server_ip**: Audio server IP address
4. **mqtt settings**: MQTT broker server connection information

## Usage

### Basic Execution
```bash
# Run with default configuration
python pi_client.py

# Run with specified device ID
python pi_client.py --device-id your_device_id
```

### Command Line Arguments
```bash
python pi_client.py --help
```

Available parameters:
- `--device-id`: Specify device ID
- Other configurations are managed through the `config.json` file

### Execution Flow

1. **Startup**: Program automatically loads configuration file on startup
2. **Initialization**: Initialize audio devices, MQTT connection, wake word detection
3. **Standby**: Wait for wake word trigger
4. **Wake**: Start audio recording after wake word detection
5. **Transmission**: Send audio data to server via UDP
6. **Response**: Receive and play audio returned from server

### MQTT Commands

The client supports the following MQTT commands:

#### Music Control
```json
{
    "type": "play_music",
    "url": "https://www.youtube.com/watch?v=VIDEO_ID",
    "volume": 50
}
```

```json
{
    "type": "stop_music"
}
```

```json
{
    "type": "pause_music"
}
```

```json
{
    "type": "resume_music"
}
```

#### Volume Control
```json
{
    "type": "set_volume",
    "volume_type": "music",
    "volume": 70
}
```

#### Recording Control
```json
{
    "type": "start_recording"
}
```

```json
{
    "type": "stop_recording"
}
```

#### Configuration Updates
```json
{
    "type": "update_config",
    "config": {
        "wake_word": {
            "enabled": false
        }
    }
}
```

## Troubleshooting

### Common Issues

#### 1. Audio Device Problems
```bash
# Check audio devices
aplay -l
arecord -l

# Test audio recording
arecord -d 5 test.wav

# Test audio playback
aplay test.wav
```

#### 2. Wake Word Not Working
- Check if Porcupine API key is correct
- Confirm wake word file path is correct
- Adjust wake word sensitivity
- Check microphone permissions and volume

#### 3. MQTT Connection Issues
- Check network connection
- Verify MQTT broker address and port
- Check firewall settings

#### 4. Music Playback Issues
- Confirm mpv and yt-dlp are installed
- Check network connection
- Verify YouTube link validity

### Debug Logging
```bash
# Enable debug logging
# Set in config.json:
"system": {
    "log_level": "DEBUG"
}

# Or enable debug mode:
"debug": {
    "enabled": true
}
```

### Performance Optimization

1. **Audio Buffering**: Adjust `chunk_size` to optimize latency and stability
2. **Network Optimization**: Ensure stable network connection
3. **Resource Management**: Periodic restart to free memory

## Development

### Project Structure
```
pi_client/
├── pi_client.py          # Main program
├── music_player.py       # Music playback module
├── wake_word_detector.py # Wake word detection module
├── config.json          # Configuration file
├── requirements.txt     # Python dependencies
├── sound/              # Audio files directory
│   └── pvwake.wav     # Wake notification sound
├── wakeword_source/    # Wake word files directory
│   ├── hello_chris.ppn
│   └── hello_chris_pi.ppn
└── recordings/         # Recording files directory (debug mode)
```

### Extension Development
- Add new MQTT command handlers
- Integrate other wake word engines
- Support more audio formats
- Add local speech recognition

### Module Overview

#### `pi_client.py`
Main application module that orchestrates all functionality:
- MQTT communication and command handling
- Audio recording and transmission
- Device state management
- Configuration management

#### `music_player.py`
Music playback functionality:
- YouTube URL processing with yt-dlp
- MPV-based audio playback
- Volume and playback control
- Completion callbacks

#### `wake_word_detector.py`
Wake word detection using Porcupine:
- Real-time audio processing
- Wake word sensitivity configuration
- Pre-buffer audio capture
- Thread-safe detection callbacks

## API Reference

### MQTT Topics

The client uses the following MQTT topic structure:
- **Command Topic**: `{topic_prefix}/server/command/{device_id}`
- **Status Topic**: `{topic_prefix}/client/status/{device_id}`
- **Config Topic**: `{topic_prefix}/client/config/{device_id}`
- **Request Topic**: `{topic_prefix}/client/request/{device_id}`

### Device States
- `idle`: Waiting for wake word
- `listening`: Recording audio
- `processing`: Processing audio
- `playing`: Playing music

## License

[Add license information here]

## Contributing

Issues and Pull Requests are welcome to improve the project.

## Contact

[Add contact information here]
