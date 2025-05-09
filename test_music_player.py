#!/usr/bin/env python3
"""
测试音乐播放功能
发送MQTT命令到Pi客户端，播放YouTube链接
"""
import paho.mqtt.client as mqtt
import json
import time
import argparse
import sys

# 默认配置
DEFAULT_CONFIG = {
    "mqtt_broker": "broker.emqx.io",
    "mqtt_port": 1883,
    "mqtt_username": None,
    "mqtt_password": None,
    "topic_prefix": "smart0337187",
    "device_id": "rasp1"
}

def on_connect(client, userdata, flags, rc):
    """MQTT连接回调"""
    if rc == 0:
        print(f"已连接到MQTT代理: {DEFAULT_CONFIG['mqtt_broker']}")
        
        # 订阅音乐状态主题
        music_status_topic = f"{DEFAULT_CONFIG['topic_prefix']}/client/music_status/{DEFAULT_CONFIG['device_id']}"
        client.subscribe(music_status_topic)
        print(f"已订阅主题: {music_status_topic}")
    else:
        print(f"连接失败，返回码: {rc}")
        sys.exit(1)

def on_message(client, userdata, msg):
    """MQTT消息回调"""
    try:
        payload = json.loads(msg.payload.decode())
        print(f"\n收到音乐状态更新: {msg.topic}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"处理消息时出错: {e}")



def send_play_music_command(client, youtube_url, volume=None):
    """发送播放音乐命令"""
    command_topic = f"{DEFAULT_CONFIG['topic_prefix']}/server/command/{DEFAULT_CONFIG['device_id']}"
    
    # 构建命令
    command = {
        "type": "play_music",
        "url": youtube_url
    }
    
    # 如果指定了音量，添加到命令中
    if volume is not None:
        command["volume"] = volume
    
    # 发送命令
    result = client.publish(
        command_topic,
        json.dumps(command),
        qos=1
    )
    
    if result.rc == 0:
        print(f"已发送播放音乐命令: {youtube_url}")
        return True
    else:
        print(f"发送命令失败，错误码: {result.rc}")
        return False
    

def send_command(client, command):
    """发送播放音乐命令"""
    command_topic = f"{DEFAULT_CONFIG['topic_prefix']}/server/command/{DEFAULT_CONFIG['device_id']}"
    
    # 构建命令
    command = {
        "type": command
    }
    
    # 发送命令
    result = client.publish(
        command_topic,
        json.dumps(command),
        qos=1
    )
    
    if result.rc == 0:
        return True
    else:
        print(f"发送命令失败，错误码: {result.rc}")
        return False

def send_stop_music_command(client):
    """发送停止音乐命令"""
    command_topic = f"{DEFAULT_CONFIG['topic_prefix']}/server/command/{DEFAULT_CONFIG['device_id']}"
    
    # 构建命令
    command = {
        "type": "stop_music"
    }
    
    # 发送命令
    result = client.publish(
        command_topic,
        json.dumps(command),
        qos=1
    )
    
    if result.rc == 0:
        print("已发送停止音乐命令")
        return True
    else:
        print(f"发送命令失败，错误码: {result.rc}")
        return False

def send_set_volume_command(client, volume):
    """发送设置音量命令"""
    command_topic = f"{DEFAULT_CONFIG['topic_prefix']}/server/command/{DEFAULT_CONFIG['device_id']}"
    
    # 构建命令
    command = {
        "type": "set_volume",
        "volume": volume
    }
    
    # 发送命令
    result = client.publish(
        command_topic,
        json.dumps(command),
        qos=1
    )
    
    if result.rc == 0:
        print(f"已发送设置音量命令: {volume}")
        return True
    else:
        print(f"发送命令失败，错误码: {result.rc}")
        return False

def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="测试音乐播放功能")
    parser.add_argument("--broker", help="MQTT代理地址", default=DEFAULT_CONFIG["mqtt_broker"])
    parser.add_argument("--port", type=int, help="MQTT代理端口", default=DEFAULT_CONFIG["mqtt_port"])
    parser.add_argument("--username", help="MQTT用户名", default=DEFAULT_CONFIG["mqtt_username"])
    parser.add_argument("--password", help="MQTT密码", default=DEFAULT_CONFIG["mqtt_password"])
    parser.add_argument("--device-id", help="设备ID", default=DEFAULT_CONFIG["device_id"])
    parser.add_argument("--url", help="YouTube链接", required=True)
    parser.add_argument("--volume", type=int, help="音量（0-100）", default=None)
    
    args = parser.parse_args()
    
    # 更新配置
    DEFAULT_CONFIG["mqtt_broker"] = args.broker
    DEFAULT_CONFIG["mqtt_port"] = args.port
    DEFAULT_CONFIG["mqtt_username"] = args.username
    DEFAULT_CONFIG["mqtt_password"] = args.password
    DEFAULT_CONFIG["device_id"] = args.device_id
    
    # 创建MQTT客户端
    client = mqtt.Client()
    
    # 设置回调
    client.on_connect = on_connect
    client.on_message = on_message
    
    # 设置用户名密码（如果有）
    if args.username and args.password:
        client.username_pw_set(args.username, args.password)
    
    # 连接到MQTT代理
    try:
        client.connect(args.broker, args.port, 60)
    except Exception as e:
        print(f"连接MQTT代理失败: {e}")
        sys.exit(1)
    
    # 启动MQTT循环
    client.loop_start()
    
    # 等待连接成功
    time.sleep(2)
    
    # 发送播放音乐命令
    send_play_music_command(client, args.url, args.volume)
    
    # 等待用户输入
    print("\n命令:")
    print("1. 停止播放")
    print("2. 设置音量")
    print("3. 自定义")
    print("0. 退出")
    
    try:
        while True:
            choice = input("\n请选择命令: ")
            
            if choice == "1":
                send_stop_music_command(client)
            elif choice == "2":
                try:
                    volume = int(input("请输入音量（0-100）: "))
                    if 0 <= volume <= 100:
                        send_set_volume_command(client, volume)
                    else:
                        print("音量必须在0-100之间")
                except ValueError:
                    print("无效的音量值")
            elif choice == "3":
                command = input("Command: ")
                send_command(client, command)
            elif choice == "0":
                break
            else:
                print("无效的命令")
    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        # 停止MQTT循环
        client.loop_stop()
        client.disconnect()
        print("已断开连接")

if __name__ == "__main__":
    main()
