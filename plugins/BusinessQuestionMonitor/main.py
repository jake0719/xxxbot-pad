import asyncio
import tomllib
import sqlite3
from datetime import datetime, timedelta
from loguru import logger
from utils.plugin_base import PluginBase
from WechatAPI import WechatAPIClient
from utils.decorators import schedule
import aiohttp
import re

from WechatAPI import WechatAPIClient
from utils.decorators import on_at_message, on_text_message
from utils.plugin_base import PluginBase

class BusinessQuestionMonitor(PluginBase):
    description = "定时检测群聊业务问题无人回复并私聊通知管理员"
    author = "AI生成"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        try:
            with open("plugins/BusinessQuestionMonitor/config.toml", "rb") as f:
                config = tomllib.load(f)
            plugin_config = config["BusinessQuestionMonitor"]
            self.enable = plugin_config.get("enable", True)
            self.admin_wxids = plugin_config.get("admin_wxids", [])
            self.check_interval = plugin_config.get("check_interval", 300)
            self.business_keywords = plugin_config.get("business_keywords", [])
            self.use_ai_classification = plugin_config.get("use_ai_classification", False)
            self.ai_api_key = plugin_config.get("ai_api_key", "")
            self.ai_base_url = plugin_config.get("ai_base_url", "")
            logger.info("BusinessQuestionMonitor 配置加载成功")
        except Exception as e:
            logger.exception(f"BusinessQuestionMonitor 插件初始化失败: {e}")
            self.enable = False
            return
        
        self.db_file = "chat_history.db"
        self.db_connection = None
        self.initialize_database()
        self.http_session = aiohttp.ClientSession()

        logger.info("BusinessQuestionMonitor  =============================  插件配置加载成功")

    def initialize_database(self):
        try:
            self.db_connection = sqlite3.connect(self.db_file)
            logger.info("BusinessQuestionMonitor 数据库连接已建立")
        except Exception as e:
            logger.error(f"BusinessQuestionMonitor 数据库连接失败: {e}")

    def get_table_name(self, chat_id: str) -> str:
        return "chat_" + re.sub(r"[^a-zA-Z0-9_]", "_", chat_id)

    def get_recent_messages(self, chat_id: str, since_ts: int) -> list:
        table_name = self.get_table_name(chat_id)
        try:
            cursor = self.db_connection.cursor()
            cursor.execute(f"""
                SELECT id, sender_wxid, create_time, content
                FROM "{table_name}"
                WHERE create_time >= ?
                ORDER BY create_time ASC
            """, (since_ts,))
            rows = cursor.fetchall()
            messages = []
            for row in rows:
                messages.append({
                    'id': row[0],
                    'sender_wxid': row[1],
                    'create_time': row[2],
                    'content': row[3]
                })
            return messages
        except Exception as e:
            logger.error(f"查询表 {table_name} 消息失败: {e}")
            return []

    async def is_business_question(self, content: str) -> bool:
        # 关键词判断
        for kw in self.business_keywords:
            if kw in content:
                return True
        # AI分类
        if self.use_ai_classification and self.ai_api_key and self.ai_base_url:
            try:
                headers = {"Authorization": f"Bearer {self.ai_api_key}", "Content-Type": "application/json"}
                payload = {"query": f"请判断这句话是否是医疗业务相关问题，只回答是或否：{content}"}
                async with self.http_session.post(f"{self.ai_base_url}/chat-messages", json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        answer = data.get("answer", "")
                        if "是" in answer:
                            return True
            except Exception as e:
                logger.error(f"AI分类调用失败: {e}")
        return False

    async def check_unanswered_questions(self, bot: WechatAPIClient):
        # 获取所有群聊表名
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall() if row[0].startswith("chat_")]
        except Exception as e:
            logger.error(f"获取群聊表失败: {e}")
            return
        
        logger.info(f"开始检查业务问题,tables:  {tables}")

        now_ts = int(datetime.now().timestamp())
        twenty_min_ago = now_ts - 1200 #搜索最近20分钟内
        for table in tables:
            chat_id = table[5:]  # 去掉 chat_ 前缀
            messages = self.get_recent_messages(chat_id, twenty_min_ago)
            logger.info(f"业务表:  {table}查询到最近20分钟内的消息并检查是否有10分钟未回复的问题，消息列表：\n{messages}")
            
            # 只看10分钟前的消息
            for i, msg in enumerate(messages):
                msg_time = msg['create_time']
                if msg_time > now_ts - 600:
                    continue  # 只看10分钟前的
                # 判断是否业务问题
                if not await self.is_business_question(msg['content']):
                    continue
                # 检查10分钟内是否有其他人回复
                replied = False
                for later_msg in messages[i+1:]:
                    if later_msg['create_time'] <= msg_time:
                        continue
                    if later_msg['create_time'] > msg_time + 600:
                        break
                    if later_msg['sender_wxid'] != msg['sender_wxid']:
                        replied = True
                        break
                if not replied:
                    # 反馈给所有管理员
                    for admin_wxid in self.admin_wxids:
                        text = f"群聊[{chat_id}]有业务问题超过10分钟无人回复：\n内容：{msg['content']}\n发送人：{msg['sender_wxid']}\n时间：{datetime.fromtimestamp(msg_time).strftime('%Y-%m-%d %H:%M:%S')}"
                        await bot.send_text_message(admin_wxid, text)

    # @scheduler(trigger="interval", seconds=300, job_id="business_question_monitor")
    @schedule('interval', seconds=300)
    async def scheduled_check(self, bot: WechatAPIClient):
        if not self.enable:
            return
        await self.check_unanswered_questions(bot)

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        if self.db_connection:
            self.db_connection.close() 