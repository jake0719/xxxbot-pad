# BusinessQuestionMonitor 插件

## 插件简介

BusinessQuestionMonitor 是一个用于微信群聊的自动监控插件，能够定时检测群聊中"业务问题"消息是否超过10分钟无人回复，并将相关消息私聊反馈给指定管理员微信号。支持关键词和AI分类（如医疗业务问题），适用于需要及时响应业务咨询的场景。

---

## 主要功能

- **定时检测**：每隔指定时间（默认10分钟，可配置）自动扫描所有群聊。
- **业务问题识别**：支持通过关键词和AI分类（如Dify）识别业务相关问题，闲聊类消息不计入。
- **无人回复判定**：检测10分钟前的业务问题消息，若10分钟内无其他成员回复，则视为"无人回复"。
- **多管理员通知**：支持配置多个管理员微信号，发现无人回复的业务问题后自动私聊所有管理员。
- **与ChatSummary插件共用sqlite数据库**，无需重复存储消息。

---

## 安装与配置

1. 将 `BusinessQuestionMonitor` 文件夹放入 `plugins/` 目录下。
2. 安装依赖：
   - `pip install aiohttp loguru`
3. 配置 `config.toml`：

```toml
[BusinessQuestionMonitor]
enable = true
admin_wxids = ["wxid_admin1", "wxid_admin2"]  # 管理员微信号列表
check_interval = 600  # 检查间隔秒数，默认10分钟
business_keywords = ["报错", "无法登录", "订单", "支付", "系统", "故障", "异常"]
use_ai_classification = true  # 是否用AI判断业务类型
ai_api_key = ""  # AI分类API密钥（如用Dify等）
ai_base_url = ""  # AI分类API Base URL
```

- `admin_wxids`：需要接收通知的管理员微信号列表。
- `business_keywords`：业务问题关键词列表。
- `use_ai_classification`：是否启用AI分类（如医疗业务问题）。
- `ai_api_key`/`ai_base_url`：如需AI分类，填写Dify等API参数。

---

## 工作原理

1. 插件定时（如每10分钟）遍历所有群聊消息表。
2. 检查10分钟前的消息，判断是否为业务问题（关键词或AI分类）。
3. 若该消息10分钟内无其他成员回复，则视为"无人回复"。
4. 自动将该消息内容、发送人、时间等信息私聊反馈给所有管理员。

---

## 注意事项

- 本插件依赖于 `chat_history.db`，需配合如 ChatSummary 插件等消息入库插件使用。
- AI分类需正确配置API密钥和URL，否则只用关键词判断。
- 管理员微信号需为实际可用的微信ID。
- 插件支持多管理员，所有人都会收到提醒。

---

## 常见问题

- **Q: 插件如何判断"业务问题"？**
  - A: 先用关键词匹配，若启用AI分类则进一步用AI判断（如医疗相关）。
- **Q: 插件会不会重复提醒？**
  - A: 只会针对10分钟前且10分钟内无人回复的业务问题提醒一次。
- **Q: 支持哪些AI分类平台？**
  - A: 目前支持Dify等兼容OpenAI风格API的平台。

---

## 联系与支持

如有问题或建议，请联系插件作者或在项目主页提交 issue。 