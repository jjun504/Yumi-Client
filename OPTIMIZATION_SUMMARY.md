# Pi Client 配置优化总结

## 优化目标
根据用户要求，对 `pi_client.py` 进行优化，移除所有默认配置参数，只依赖读取 `config.json`，parser 只保留对 `device_id` 的切换功能。

## 主要变更

### 1. 移除 DEFAULT_CONFIG 常量
- **之前**: 使用 `DEFAULT_CONFIG` 全局常量存储所有默认配置
- **现在**: 使用 `CONFIG` 全局变量，直接从 `config.json` 加载配置

### 2. 简化配置加载逻辑
- **之前**: 复杂的配置合并逻辑，需要处理默认值和文件值的合并
- **现在**: 直接加载 JSON 文件到 `CONFIG` 变量，如果文件不存在则创建默认配置文件

### 3. 移除扁平化配置 (_create_flat_config)
- **之前**: 使用 `_create_flat_config()` 方法创建向后兼容的扁平化配置
- **现在**: 直接使用分层的 `CONFIG` 结构，移除了向后兼容层

### 4. 简化命令行参数
- **之前**: 支持多种命令行参数（broker, port, server, device-id 等）
- **现在**: 只保留 `--device-id` 参数，用于设备ID切换

### 5. 更新所有配置引用
将代码中所有对配置的引用从扁平化结构改为分层结构：
- `self.config["mqtt_broker"]` → `CONFIG["mqtt"]["broker"]`
- `self.config["device_id"]` → `CONFIG["system"]["device_id"]`
- `self.config["audio_sample_rate"]` → `CONFIG["audio_settings"]["sample_rate"]`
- 等等...

## 代码变更统计

### 删除的代码
- `DEFAULT_CONFIG` 常量定义（约74行）
- `_create_flat_config()` 方法（约52行）
- 复杂的配置合并逻辑（约40行）
- 大量命令行参数定义（约30行）

### 修改的代码
- `load_config_from_file()` 函数：简化为直接JSON加载
- `PiClient.__init__()` 方法：移除扁平化配置创建
- 所有配置引用：从扁平化改为分层结构（约150处修改）

### 新增的代码
- 默认配置创建逻辑（当配置文件不存在时）

## 功能验证

### 测试用例
创建了 `test_config_optimization.py` 包含以下测试：

1. **配置加载测试**: 验证从现有配置文件正确加载配置
2. **默认配置创建测试**: 验证当配置文件不存在时创建默认配置
3. **设备ID切换测试**: 验证命令行参数正确更新设备ID
4. **PiClient初始化测试**: 验证优化后的代码能正确初始化

### 测试结果
```
Ran 4 tests in 1.429s
OK
```
所有测试通过，验证优化成功。

## 使用方式

### 基本使用
```bash
python pi_client.py
```
程序将：
1. 尝试加载 `config.json`
2. 如果文件不存在，创建默认配置文件
3. 使用配置启动客户端

### 设备ID切换
```bash
python pi_client.py --device-id yumi006
```
程序将：
1. 加载现有配置
2. 更新设备ID为 `yumi006`
3. 保存配置到文件
4. 使用新配置启动客户端

## 配置文件结构

配置文件 `config.json` 采用分层结构：

```json
{
    "system": {
        "device_id": "",
        "password": "",
        "user_id": null,
        "model": "raspberry_pi",
        "version": "1.0.0",
        "log_level": "DEBUG",
        "status": "offline"
    },
    "wake_word": {
        "enabled": true,
        "api_key": "...",
        "keyword_path": "wakeword_source/hello_chris.ppn",
        "sensitivity": 0.5
    },
    "audio_settings": {
        "sample_rate": 24000,
        "channels": 1,
        "chunk_size": 960,
        "format": "int16",
        "general_volume": 50,
        "music_volume": 50,
        "notification_volume": 50,
        "wake_sound_path": "sound/pvwake.wav"
    },
    "mqtt": {
        "broker": "broker.emqx.io",
        "port": 1883,
        "username": null,
        "password": null,
        "client_id_prefix": "smart_assistant_87",
        "topic_prefix": "smart0337187"
    },
    "network": {
        "server_ip": null,
        "server_udp_port": 8884,
        "server_udp_receive_port": 8885,
        "stt_bridge_ip": null,
        "stt_bridge_port": 8884,
        "discovery_port": 50000,
        "discovery_request": "DISCOVER_SERVER_REQUEST",
        "discovery_response_prefix": "DISCOVER_SERVER_RESPONSE_",
        "stt_mode": false
    },
    "recording": {
        "auto_stop": true,
        "timeout": 15.0,
        "silence_threshold": 300,
        "initial_silence_duration": 3.0,
        "speech_silence_duration": 1.0,
        "save_path": "recordings"
    },
    "debug": {
        "enabled": false
    }
}
```

## 优势

1. **简化代码结构**: 移除了复杂的配置合并和扁平化逻辑
2. **提高可维护性**: 配置结构更清晰，易于理解和修改
3. **减少代码量**: 删除了约200行冗余代码
4. **保持功能完整**: 所有原有功能都得到保留
5. **向前兼容**: 现有的配置文件可以直接使用

## 注意事项

1. 首次运行时会自动创建 `config.json` 文件
2. 设备ID切换会立即保存到配置文件
3. 所有配置修改都会持久化到文件
4. 保持了原有的MQTT、音频、录音等所有功能
