# GroupChatHelper - 聊天智能助手插件 📝

[![Version](https://img.shields.io/github/v/release/your_username/GroupChatHelper)](https://github.com/your_username/GroupChatHelper/releases)
[![Author](https://img.shields.io/badge/Author-%E8%80%81%E5%A4%8F%E7%9A%84%E9%87%91%E5%BA%93-blue)](https://github.com/your_username)
[![License](https://img.shields.io/github/license/your_username/GroupChatHelper)](LICENSE)

**本插件是 [XYBotv2](https://github.com/HenryXiaoYang/XYBotv2) 的一个插件。**

<img src="https://github.com/user-attachments/assets/a2627960-69d8-400d-903c-309dbeadf125" width="400" height="600">

## 简介

`GroupChatHelper` 是一款强大的聊天助手插件！ 它可以自动参与话题。 插件支持通过 Dify 大模型提供更智能的回复 🧠。

## 功能

*   **群聊自动回复助手：** 自动回复群里相关用户问题🧾。
*   **Dify 集成：** 通过 Dify 大模型智能体提供智能问答 ✨。


## 安装

1.  确保你已经安装了 [XYBotv2]([https://github.com/HenryXiaoYang/XYBotV2])。
2.  将插件文件复制到 XYBotv2 的插件目录中 📂。
3.  安装依赖：`pip install loguru aiohttp tomli` (如果需要) 🛠️
4.  配置插件（见配置章节）⚙️。
5.  重启你的 XYBotv2 应用程序 🔄。

## 配置

插件的配置位于 `config.toml` 文件中 📝。以下是配置示例：

```toml
[GroupChatHelper.Dify]
enable = true              # 是否启用 Dify 集成
api-key = "你的 Dify API 密钥"   # 你的 Dify API 密钥
base-url = "你的 Dify API Base URL"  # 你的 Dify API Base URL
http-proxy = ""               # HTTP 代理服务器地址 (可选)，如 "http://127.0.0.1:7890"

[GroupChatHelper]
enable = true
commands = ["$总结", "总结"]  # 触发总结的命令
default_num_messages = 100 # 默认总结 100 条消息
summary_wait_time = 60      # 总结等待时间（秒）
```

**给个 ⭐ Star 支持吧！** 😊

**开源不易，感谢打赏支持！**

![image](https://github.com/user-attachments/assets/2dde3b46-85a1-4f22-8a54-3928ef59b85f)
