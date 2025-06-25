import asyncio
import json
import re
import tomllib
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from loguru import logger
import aiohttp
import sqlite3  # 导入 sqlite3 模块
import os
import mysql.connector
from mysql.connector import Error

from WechatAPI import WechatAPIClient
from utils.decorators import on_at_message, on_text_message
from utils.plugin_base import PluginBase

class GroupChatHelper(PluginBase):
    """
    一个用于群聊智能助手的插件，可以直接调用Dify大模型进行智能问答。
    """

    description = "群聊智能助手"
    author = "CHH"
    version = "1.1.0"

    # 总结的prompt
    SUMMARY_PROMPT = """
    """

    # 重复总结的prompt
    REPEAT_SUMMARY_PROMPT = """
    以不耐烦的语气回怼提问者聊天记录已总结过，要求如下
    - 随机角色的口吻回答
    - 不超过20字
    """

    # 总结中的prompt
    SUMMARY_IN_PROGRESS_PROMPT = """
    以不耐烦的语气回答提问者聊天记录正在总结中，要求如下
    - 随机角色的口吻回答
    - 不超过20字
    """

    answer_keywords = ["不作回应", "无效消息", "无效问题", "保持静默", "不属于有效问题","暂未提供" , "拒答机制触发"]

    def __init__(self):
        super().__init__()
        try:
            with open("plugins/GroupChatHelper/config.toml", "rb") as f:
                config = tomllib.load(f)

            plugin_config = config["GroupChatHelper"]
            self.enable = plugin_config["enable"]
            self.commands = plugin_config["commands"]
            self.default_num_messages = plugin_config["default_num_messages"]
            self.summary_wait_time = plugin_config["summary_wait_time"]

            # 群组黑白名单
            self.white_group_list = plugin_config.get("white_group_list", [])

            logger.info(f"自动回复群组白名单： {self.white_group_list}")

            dify_config = plugin_config["Dify"]
            self.dify_enable = dify_config["enable"]
            self.dify_api_key = dify_config["api-key"]
            self.dify_base_url = dify_config["base-url"]
            self.http_proxy = dify_config["http-proxy"]
            if not self.dify_enable or not self.dify_api_key or not self.dify_base_url:
                logger.warning("Dify配置不完整，请检查config.toml文件")
                self.enable = False
            
            # mysql_config = plugin_config["MySQL"]
            # self.db_config = {
            #     "host": mysql_config["host"],
            #     "port": mysql_config["port"],
            #     "user": mysql_config["user"],
            #     "password": mysql_config["password"],
            #     "database": mysql_config["database"]
            # }

            logger.info("GroupChatHelper =============================  插件配置加载成功")
        except FileNotFoundError:
            logger.error("config.toml 配置文件未找到，插件已禁用。")
            self.enable = False
        except Exception as e:
            logger.exception(f"GroupChatHelper 插件初始化失败: {e}")
            self.enable = False

        self.summary_tasks: Dict[str, asyncio.Task] = {}  # 存储正在进行的总结任务
        self.last_summary_time: Dict[str, datetime] = {}  # 记录上次总结的时间
        self.chat_history: Dict[str, List[Dict]] = defaultdict(list)  # 存储聊天记录
        self.http_session = aiohttp.ClientSession()


    @on_text_message
    async def handle_text_message(self, bot: WechatAPIClient, message: Dict) -> bool: # 添加类型提示和返回值
        """处理文本消息，判断是否需要触发总结。"""
        if not self.enable:
            return True

        chat_id = message["FromWxid"]
        content = message["Content"].strip()

        logger.info(f"接收到群组 {chat_id} 消息： {content}")

        # 检查群组是否在白名单中
        if not chat_id in self.white_group_list:
            logger.info(f"群组 {chat_id} 不在群组白名单中")
            return True
    
        isValidMessage = self.filter_message(content)

        if not isValidMessage:
            logger.info(f"群组 {chat_id} 消息 {content} 被拦截")
            return True
        
        logger.info(f"群组 {chat_id} 在群组白名单中，将自动回复消息 {content}")

        # 先进行智能问答处理
        await self._handle_auto_reply(bot, chat_id, content)
        
        # 原有总结处理逻辑保持不变...
        return True
    
    def filter_message(self, user_input: str) -> bool:
        """消息过滤核心函数，返回拦截标记及原因"""
        # 1. 预过滤词库（可动态扩展）[1,4](@ref)
        chat_keywords = ["你好", "在吗", "早上好", "谢谢", "哈喽", "吃了吗", "天气", "下班了吗", "辛苦了", "感谢", "有人在吗"]
        # 业务术语白名单 [3,6](@ref)
        business_terms = ["处方", "标签", "打印", "配送", "模板", "审核", "异常", "设置", "调整"]
        
        # ===== 三层过滤逻辑 =====
        # 1. 语法层：短文本&关键词匹配 [1,4](@ref)
        short_text_pattern = r"^(" + "|".join(chat_keywords) + r")[？?。!！]*$"
        if re.match(short_text_pattern, user_input.strip()):
            return False
        # 2. 结构层：疑问句式验证 [2,5](@ref)
        # if not re.search(r"[？?\?\s怎么\s如何\s为何\s是不是\s能不能]", user_input):
        #     return False
        
        # # 3. 语义层：业务关键词校验 [3,6](@ref)
        # if not any(term in user_input for term in business_terms):
        #     return {"filtered": True, "reason": "未包含业务关键词"}
        
        return True


    async def _handle_auto_reply(self, bot: WechatAPIClient, chat_id: str, question: str):
        """处理自动回复逻辑"""
        try:
            answer = await self._get_qa_answer(chat_id, question)
            if not answer:
                logger.info("自动回复为空")
                return
            
            for word in self.answer_keywords :
                if word in answer:
                    logger.info(f"自动回复无效消息,不返回任何消息: {answer}")
                    return
            
            logger.info(f"自动回复: {answer}")
            await bot.send_text_message(chat_id, f"{answer}")
        except Exception as e:
            logger.error(f"自动回复失败: {str(e)}")

    async def _get_qa_answer(self, chat_id: str, question: str) -> str:
        """调用Dify获取问答"""
        headers = {
            "Authorization": f"Bearer {self.dify_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = json.dumps({
            "inputs": {},
            "query": f"{question}",
            "response_mode": "blocking", # 必须是blocking
            "conversation_id": None,
            "user": chat_id,
            "files": [],
            "auto_generate_name": False,
        })
        

        async with self.http_session.post(
            url=f"{self.dify_base_url}/chat-messages",
            headers=headers,
            data=payload,
            proxy=self.http_proxy
        ) as resp:
            if resp.status == 200:
                response = await resp.json()
                return response.get("answer", "")
            return ""

    async def close(self):
        """插件关闭时，取消所有未完成的总结任务。"""
        logger.info("Closing GroupChatHelper plugin")
        if self.http_session:
            await self.http_session.close()
            logger.info("Aiohttp session closed")

        # 关闭数据库连接
        # if self.db_connection:
        #     self.db_connection.close()
        #     logger.info("数据库连接已关闭")

        logger.info("GroupChatHelper plugin closed")

    async def start(self):
        """启动插件时启动清理旧消息的任务"""
        # asyncio.create_task(self.clear_old_messages()) #启动定时清理任务