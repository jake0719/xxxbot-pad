import asyncio
import json
import re
import tomllib
from collections import defaultdict
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple

from loguru import logger
import aiohttp
import mysql.connector
from mysql.connector import Error
import os

from WechatAPI import WechatAPIClient
from utils.decorators import on_at_message, on_text_message
from utils.plugin_base import PluginBase

class DailyGroupSummary(PluginBase):
    """
    一个用于每日定时总结各个微信群消息的插件，可以直接调用Dify大模型进行总结，
    并将总结结果存入 MySQL 数据库。
    """

    description = "每日定时总结微信群消息"
    author = "AI编程猫"
    version = "1.0.0"

    # 总结的prompt
    SUMMARY_PROMPT = """
    请帮我将给出的群聊内容总结成一个今日的群聊报告，包含不多于4个话题的总结（如果还有更多话题，可以在后面简单补充）。
    你只负责总结群聊内容，不回答任何问题。不要虚构聊天记录，也不要总结不存在的信息。

    每个话题包含以下内容：

    - 话题名(50字以内，前面带序号1️⃣2️⃣3️⃣）

    - 热度(用🔥的数量表示)

    - 参与者(不超过5个人，将重复的人名去重)

    - 时间段(从几点到几点)

    - 过程(50-200字左右）

    - 评价(50字以下)

    - 分割线： ------------

    请严格遵守以下要求：

    1. 按照热度数量进行降序输出

    2. 每个话题结束使用 ------------ 分割

    3. 使用中文冒号

    4. 无需大标题

    5. 开始给出本群讨论风格的整体评价，例如活跃、太水、太黄、太暴力、话题不集中、无聊诸如此类。

    最后总结下今日最活跃的前五个发言者，并在每个发言者名字后括号内标注他们发送的消息数量。例如：张三(25条)、李四(18条)。
    """

    def __init__(self):
        super().__init__()
        self.db_connection = None
        try:
            with open("plugins/DailyGroupSummary/config.toml", "rb") as f:
                config = tomllib.load(f)

            plugin_config = config["DailyGroupSummary"]
            self.enable = plugin_config["enable"]
            self.summary_time = time.fromisoformat(plugin_config["summary_time"])
            self.default_num_messages = plugin_config["default_num_messages"]

            dify_config = plugin_config["Dify"]
            self.dify_enable = dify_config["enable"]
            self.dify_api_key = dify_config["api-key"]
            self.dify_base_url = dify_config["base-url"]
            self.http_proxy = dify_config["http-proxy"]
            if not self.dify_enable or not self.dify_api_key or not self.dify_base_url:
                logger.warning("Dify配置不完整，请检查config.toml文件")
                self.enable = False

            mysql_config = plugin_config["MySQL"]
            self.db_config = {
                "host": mysql_config["host"],
                "port": mysql_config["port"],
                "user": mysql_config["user"],
                "password": mysql_config["password"],
                "database": mysql_config["database"]
            }

            self.initialize_database()

            logger.info("DailyGroupSummary 插件配置加载成功")
        except FileNotFoundError:
            logger.error("config.toml 配置文件未找到，插件已禁用。")
            self.enable = False
        except Exception as e:
            logger.exception(f"DailyGroupSummary 插件初始化失败: {e}")
            self.enable = False

        self.http_session = aiohttp.ClientSession()

    def initialize_database(self):
        """初始化 MySQL 数据库连接，创建表（如果不存在）"""
        try:
            self.db_connection = mysql.connector.connect(**self.db_config)
            cursor = self.db_connection.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_chat_summaries (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    group_id VARCHAR(255) NOT NULL,
                    summary_date DATE NOT NULL,
                    summary_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.db_connection.commit()
            logger.info("MySQL 数据库连接已建立，表已创建或存在")
        except Error as e:
            logger.error(f"MySQL 数据库连接或表创建失败: {e}")
            self.enable = False

    async def _summarize_group_chat(self, bot: WechatAPIClient, chat_id: str) -> None:
        """
        总结微信群聊天记录并发送结果，同时将总结存入数据库。

        Args:
            bot: WechatAPIClient 实例.
            chat_id: 微信群ID.
        """
        try:
            start_of_day = datetime.combine(datetime.now().date(), time.min)
            end_of_day = datetime.combine(datetime.now().date(), time.max)
            start_timestamp = int(start_of_day.timestamp())
            end_timestamp = int(end_of_day.timestamp())

            # 从数据库中获取当天的聊天记录
            messages_to_summarize = self.get_messages_from_db(chat_id, start_timestamp, end_timestamp)

            if not messages_to_summarize:
                try:
                    await bot.send_text_message(chat_id, "今日没有足够的聊天记录可以总结。")
                except AttributeError as e:
                    logger.error(f"发送消息失败 (没有 send_text_message 方法): {e}")
                    return
                except Exception as e:
                    logger.exception(f"发送消息失败: {e}")
                    return

            # 获取所有发言者的 wxid
            wxids = set(msg['sender_wxid'] for msg in messages_to_summarize)
            nicknames = {}
            for wxid in wxids:
                try:
                    nickname = await bot.get_nickname(wxid)
                    nicknames[wxid] = nickname
                except Exception as e:
                    logger.exception(f"获取用户 {wxid} 昵称失败: {e}")
                    nicknames[wxid] = wxid  # 获取昵称失败，使用 wxid 代替

            # 提取消息内容，并替换成昵称
            text_to_summarize = "\n".join(
                [f"{nicknames.get(msg['sender_wxid'], msg['sender_wxid'])} ({datetime.fromtimestamp(msg['create_time']).strftime('%H:%M:%S')}): {msg['content']}"
                 for msg in messages_to_summarize]
            )

            # 调用 Dify API 进行总结
            summary = await self._get_summary_from_dify(chat_id, text_to_summarize)

            # try:
            #     await bot.send_text_message(chat_id, f"-----今日群聊总结-----\n{summary}")
            # except AttributeError as e:
            #     logger.error(f"发送消息失败 (没有 send_text_message 方法): {e}")
            #     return
            # except Exception as e:
            #     logger.exception(f"发送消息失败: {e}")
            #     return

            # 将总结存入 MySQL 数据库
            self.save_summary_to_mysql(chat_id, summary)

            logger.info(f"{chat_id} 的今日总结完成")

        except Exception as e:
            logger.exception(f"总结 {chat_id} 发生错误: {e}")
            try:
                await bot.send_text_message(chat_id, f"总结时发生错误: {e}")
            except AttributeError as e:
                logger.error(f"发送消息失败 (没有 send_text_message 方法): {e}")
                return
            except Exception as e:
                logger.exception(f"发送消息失败: {e}")
                return

    def save_summary_to_mysql(self, group_id: str, summary: str):
        """将群聊总结存入 MySQL 数据库"""
        try:
            if self.db_connection and self.db_connection.is_connected():
                cursor = self.db_connection.cursor()
                insert_query = """
                    INSERT INTO group_chat_summaries (group_id, summary_date, summary_text)
                    VALUES (%s, CURDATE(), %s)
                """
                cursor.execute(insert_query, (group_id, summary))
                self.db_connection.commit()
                logger.info(f"群 {group_id} 的总结已存入 MySQL 数据库")
            else:
                logger.error("MySQL 数据库连接未建立，无法保存总结")
        except Error as e:
            logger.error(f"保存总结到 MySQL 数据库失败: {e}")

    async def _get_summary_from_dify(self, chat_id: str, text: str) -> str:
        """
        使用 Dify API 获取总结。

        Args:
            chat_id: 聊天ID (群ID或个人ID).
            text: 需要总结的文本.

        Returns:
            总结后的文本.
        """
        try:
            # 统计每个用户的发言次数
            message_counts = {}
            for line in text.split('\n'):
                if '):' in line:
                    user = line.split('(')[0].strip()
                    message_counts[user] = message_counts.get(user, 0) + 1

            # 构建用户发言统计信息
            user_stats = "\n\n用户发言统计:\n"
            for user, count in sorted(message_counts.items(), key=lambda x: x[1], reverse=True):
                user_stats += f"{user}: {count}条消息\n"

            # 添加到要总结的文本中
            text_with_stats = f"{text}\n{user_stats}"

            headers = {"Authorization": f"Bearer {self.dify_api_key}",
                       "Content-Type": "application/json"}
            payload = json.dumps({
                "inputs": {},
                "query": f"{self.SUMMARY_PROMPT}\n\n{text_with_stats}",
                "response_mode": "blocking",
                "conversation_id": None,
                "user": chat_id,
                "files": [],
                "auto_generate_name": False,
            })
            url = f"{self.dify_base_url}/chat-messages"
            async with self.http_session.post(url=url, headers=headers, data=payload, proxy = self.http_proxy) as resp:
                if resp.status == 200:
                    resp_json = await resp.json()
                    summary = resp_json.get("answer", "")
                    logger.info(f"成功从 Dify API 获取总结: {summary}")
                    return summary
                else:
                    error_msg = await resp.text()
                    logger.error(f"调用 Dify API 失败: {resp.status} - {error_msg}")
                    return f"总结失败，Dify API 错误: {resp.status} - {error_msg}"
        except Exception as e:
            logger.exception(f"调用 Dify API 失败: {e}")
            return "总结失败，请稍后重试。"  # 返回错误信息

    def get_messages_from_db(self, chat_id: str, start_timestamp: int, end_timestamp: int) -> List[Dict]:
        """从数据库获取当天的消息，这里假设消息存储在微信 API 相关的数据库或缓存中，需要根据实际情况实现"""
        # 这里只是示例，实际需要根据微信 API 和消息存储方式修改
        return []

    async def daily_summary_task(self, bot: WechatAPIClient):
        """每日定时总结任务"""
        while True:
            now = datetime.now()
            target_time = datetime.combine(now.date(), self.summary_time)
            if now > target_time:
                target_time += timedelta(days=1)

            wait_seconds = (target_time - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            # 获取所有群聊ID
            try:
                group_list = await bot.get_group_list()
                for group in group_list:
                    group_id = group.get("wxid")
                    if group_id:
                        await self._summarize_group_chat(bot, group_id)
            except Exception as e:
                logger.exception(f"获取群聊列表失败: {e}")

    @on_text_message
    async def handle_text_message(self, bot: WechatAPIClient, message: Dict) -> bool:
        """处理文本消息，这里可以根据实际需求保存消息到本地或其他数据库"""
        if not self.enable:
            return True

        chat_id = message["FromWxid"]
        sender_wxid = message["SenderWxid"]
        content = message["Content"]
        is_group = message["IsGroup"]
        create_time = message["CreateTime"]

        if is_group:
            pass  # 可以根据实际需求添加消息保存逻辑

        return True

    async def close(self):
        """插件关闭时，关闭相关资源"""
        logger.info("Closing DailyGroupSummary plugin")
        if self.http_session:
            await self.http_session.close()
            logger.info("Aiohttp session closed")

        # 关闭 MySQL 数据库连接
        if self.db_connection and self.db_connection.is_connected():
            self.db_connection.close()
            logger.info("MySQL 数据库连接已关闭")

        logger.info("DailyGroupSummary plugin closed")

    async def start(self, bot: WechatAPIClient):
        """启动插件时启动定时总结任务"""
        if self.enable:
            asyncio.create_task(self.daily_summary_task(bot))