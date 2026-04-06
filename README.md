# Yumi Client - Smart  Assistant Client

A client interface of [Yumi-Server](https://github.com/jjun504/Yumi-Server) for Raspberry Pi and Windows, which supports wake word detection, audio recording, MQTT communication, and music playback functionality.

## Features

### Audio Processing
- **Wake Word Detection**: Local wake word detection using Porcupine engine
- **Audio Recording**: High-quality audio recording and real-time transmission
- **Audio Playback**: Support for audio playback and music streaming
- **Smart Volume Control**: Automatic music volume adjustment during recording and audio reception

### Network Communication
- **MQTT Communication**: Real-time command and status synchronization with server
- **UDP Audio Transmission**: Efficient audio data transmission
- **Server Discovery**: Automatic discovery of servers on the network
- **STT Bridge Mode**: Support for Speech-to-Text bridge processing

### Music Playback
- **YouTube Playback**: Direct playback of YouTube links
- **Volume Control**: Independent music volume control
- **Playback Control**: Play, pause, stop, and resume functionality
- **Auto-Continue**: Automatic request for next song after completion

### Configuration Management
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
git clone https://github.com/jjun504/Yumi-Client.git
cd Yumi-Client
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

### 4. Configuration File Setup

```bash
cp config.example.json config.json
nano config.json
```

### 5. Audio Device Configuration

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

The `.ppn` wake word model files are bound to a specific Picovoice account and are not included in this repository. You need to generate your own:

1. Create a free account at [Picovoice Console](https://console.picovoice.ai/)
2. Create a custom wake word under **Wake Word > Porcupine**
3. Download the `.ppn` file for **Raspberry Pi** (or your target platform)
4. Place the file in the `wakeword_source/` directory
5. Update `keyword_path` in `config.json` to match the filename

Your free Picovoice API key is shown on the console dashboard — copy it into `wake_word.api_key` in `config.json`.

### 6. Audio Files
Ensure notification sound files exist:
```bash
# Check audio files
ls sound/
# Should contain: pvwake.wav
```

### 7. Native Library Dependencies

The `libmpv/` and `opuslib/` directories are **not included** in the repository. Install the libraries via your system package manager:

```bash
# libmpv (required for music playback)
sudo apt install libmpv-dev

# Opus codec (required for audio encoding)
sudo apt install libopus-dev
pip install opuslib
```

On Windows (for development/testing), download the prebuilt `libmpv` DLL from [mpv.io/installation](https://mpv.io/installation/) and place the `.dll` in the `libmpv/` directory.

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

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

**Third-party notices:**
- Wake word detection powered by [Porcupine](https://github.com/Picovoice/porcupine) (Picovoice) — subject to [Picovoice Terms of Use](https://picovoice.ai/docs/terms-of-use/)
- Music playback via [mpv](https://mpv.io/) and [yt-dlp](https://github.com/yt-dlp/yt-dlp)

## Contributing

Issues and Pull Requests are welcome to improve the project.

## Contact / Issues

For questions, support, or collaboration opportunities, please reach out to me:

Email: [chen.jun.xu@student.mmu.edu.my](mailto:chen.jun.xu@student.mmu.edu.my)