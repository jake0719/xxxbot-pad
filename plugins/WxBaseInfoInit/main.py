import asyncio
import json
import tomllib
from datetime import datetime
from typing import Dict, List

from loguru import logger
import mysql.connector
from mysql.connector import Error

from WechatAPI import WechatAPIClient
from utils.decorators import on_text_message
from utils.plugin_base import PluginBase

class WxBaseInfoInit(PluginBase):
    """
    采集微信通讯录好友、群、群成员信息并保存到MySQL的插件。
    """
    description = "采集微信通讯录好友、群、群成员信息并保存到MySQL"
    author = "CHH改写 by AI"
    version = "2.0.0"

    SYNC_CONTACTS_COMMAND = "同步通讯录"
    SYNC_GROUPS_COMMAND = "同步群信息"

    def __init__(self):
        super().__init__()
        try:
            with open("plugins/WxBaseInfoInit/config.toml", "rb") as f:
                config = tomllib.load(f)
            plugin_config = config["WxBaseInfoInit"]
            mysql_config = plugin_config["MySQL"]
            self.db_config = {
                "host": mysql_config["host"],
                "port": mysql_config["port"],
                "user": mysql_config["user"],
                "password": mysql_config["password"],
                "database": mysql_config["database"]
            }
            self.enable = plugin_config.get("enable", True)
            logger.info("WxBaseInfoInit 插件配置加载成功")
        except Exception as e:
            logger.exception(f"WxBaseInfoInit 插件初始化失败: {e}")
            self.enable = False
        self.mysql_db_connection = None
        self.initialize_database()

    def initialize_database(self):
        """初始化MySQL数据库连接和表结构"""
        try:
            self.mysql_db_connection = mysql.connector.connect(**self.db_config)
            cursor = self.mysql_db_connection.cursor()
            # 联系人表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS contacts (
                    wxid VARCHAR(64) PRIMARY KEY,
                    nickname VARCHAR(255),
                    remark VARCHAR(255),
                    avatar TEXT,
                    alias VARCHAR(255),
                    type VARCHAR(32),
                    region VARCHAR(64),
                    last_updated TIMESTAMP,
                    extra_data JSON
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            ''')
            # 群成员表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS group_members (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    group_wxid VARCHAR(64) NOT NULL,
                    member_wxid VARCHAR(64) NOT NULL,
                    nickname VARCHAR(255),
                    display_name VARCHAR(255),
                    avatar TEXT,
                    inviter_wxid VARCHAR(64),
                    join_time BIGINT,
                    last_updated TIMESTAMP,
                    extra_data JSON,
                    UNIQUE KEY uniq_group_member (group_wxid, member_wxid)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            ''')
            self.mysql_db_connection.commit()
            logger.info("MySQL 数据库连接已建立，表已创建或存在")
        except Error as e:
            logger.error(f"MySQL 数据库连接或表创建失败: {e}")
            self.enable = False

    async def sync_contacts_to_mysql(self, bot: WechatAPIClient, chat_id: str):
        """采集所有联系人（好友/群）信息并保存到MySQL"""
        try:
            logger.info("开始采集微信通讯录...")
            # 获取所有联系人（含好友和群）
            data = await bot.get_total_contract_list()
            contacts = data.get("ContactList", [])
            if not contacts:
                await bot.send_text_message(chat_id, "未获取到任何联系人信息！")
                return
            cursor = self.mysql_db_connection.cursor()
            now = datetime.now()
            count = 0
            for contact in contacts:
                wxid = contact.get("UserName") or contact.get("wxid")
                if not wxid:
                    continue
                nickname = contact.get("NickName") or contact.get("nickname")
                remark = contact.get("RemarkName") or contact.get("remark")
                avatar = contact.get("HeadImgUrl") or contact.get("avatar")
                alias = contact.get("Alias") or contact.get("alias")
                contact_type = "group" if wxid.endswith("@chatroom") else ("official" if wxid.startswith("gh_") else "friend")
                region = contact.get("Province") or contact.get("region")
                extra_data = {k: v for k, v in contact.items() if k not in ["UserName", "wxid", "NickName", "nickname", "RemarkName", "remark", "HeadImgUrl", "avatar", "Alias", "alias", "Province", "region"]}
                cursor.execute('''
                    INSERT INTO contacts (wxid, nickname, remark, avatar, alias, type, region, last_updated, extra_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        nickname=VALUES(nickname), remark=VALUES(remark), avatar=VALUES(avatar),
                        alias=VALUES(alias), type=VALUES(type), region=VALUES(region),
                        last_updated=VALUES(last_updated), extra_data=VALUES(extra_data)
                ''', (
                    wxid, nickname, remark, avatar, alias, contact_type, region, now, json.dumps(extra_data, ensure_ascii=False)
                ))
                count += 1
            self.mysql_db_connection.commit()
            logger.info(f"成功保存 {count} 个联系人到MySQL")
            await bot.send_text_message(chat_id, f"成功保存 {count} 个联系人到MySQL")
        except Exception as e:
            logger.exception(f"同步通讯录到MySQL失败: {e}")
            await bot.send_text_message(chat_id, f"同步通讯录失败: {e}")

    async def sync_groups_to_mysql(self, bot: WechatAPIClient, chat_id: str):
        """采集所有群信息及群成员信息并保存到MySQL"""
        try:
            logger.info("开始采集微信群及成员信息...")
            # 获取所有联系人，筛选出群聊
            data = await bot.get_total_contract_list()
            contacts = data.get("ContactList", [])
            group_list = [c for c in contacts if (c.get("UserName") or c.get("wxid", "")).endswith("@chatroom")]
            if not group_list:
                await bot.send_text_message(chat_id, "未获取到任何群聊信息！")
                return
            cursor = self.mysql_db_connection.cursor()
            now = datetime.now()
            group_count = 0
            member_count = 0
            for group in group_list:
                group_wxid = group.get("UserName") or group.get("wxid")
                # 获取群详细信息
                try:
                    group_info = await bot.get_chatroom_info(group_wxid)
                except Exception as e:
                    logger.warning(f"获取群 {group_wxid} 详细信息失败: {e}")
                    group_info = group
                nickname = group_info.get("NickName") or group_info.get("nickname")
                avatar = group_info.get("HeadImgUrl") or group_info.get("avatar")
                extra_data = {k: v for k, v in group_info.items() if k not in ["UserName", "wxid", "NickName", "nickname", "HeadImgUrl", "avatar"]}
                # 保存群信息到contacts表
                cursor.execute('''
                    INSERT INTO contacts (wxid, nickname, remark, avatar, alias, type, region, last_updated, extra_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        nickname=VALUES(nickname), avatar=VALUES(avatar), type=VALUES(type),
                        last_updated=VALUES(last_updated), extra_data=VALUES(extra_data)
                ''', (
                    group_wxid, nickname, None, avatar, None, "group", None, now, json.dumps(extra_data, ensure_ascii=False)
                ))
                group_count += 1
                # 获取群成员
                try:
                    members = await bot.get_chatroom_member_list(group_wxid)
                except Exception as e:
                    logger.warning(f"获取群 {group_wxid} 成员失败: {e}")
                    continue
                for member in members:
                    member_wxid = member.get("UserName") or member.get("wxid")
                    if not member_wxid:
                        continue
                    nickname = member.get("NickName") or member.get("nickname")
                    display_name = member.get("DisplayName") or member.get("display_name")
                    avatar = member.get("HeadImgUrl") or member.get("avatar")
                    inviter_wxid = member.get("InviterUserName")
                    join_time = member.get("JoinTime")
                    extra_data = {k: v for k, v in member.items() if k not in ["UserName", "wxid", "NickName", "nickname", "DisplayName", "display_name", "HeadImgUrl", "avatar", "InviterUserName", "JoinTime"]}
                    cursor.execute('''
                        INSERT INTO group_members (group_wxid, member_wxid, nickname, display_name, avatar, inviter_wxid, join_time, last_updated, extra_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            nickname=VALUES(nickname), display_name=VALUES(display_name), avatar=VALUES(avatar),
                            inviter_wxid=VALUES(inviter_wxid), join_time=VALUES(join_time), last_updated=VALUES(last_updated), extra_data=VALUES(extra_data)
                    ''', (
                        group_wxid, member_wxid, nickname, display_name, avatar, inviter_wxid, join_time, now, json.dumps(extra_data, ensure_ascii=False)
                    ))
                    member_count += 1
            self.mysql_db_connection.commit()
            logger.info(f"成功保存 {group_count} 个群及 {member_count} 个群成员到MySQL")
            await bot.send_text_message(chat_id, f"成功保存 {group_count} 个群及 {member_count} 个群成员到MySQL")
        except Exception as e:
            logger.exception(f"同步群信息到MySQL失败: {e}")
            await bot.send_text_message(chat_id, f"同步群信息失败: {e}")

    @on_text_message
    async def handle_text_message(self, bot: WechatAPIClient, message: Dict):
        if not self.enable:
            return True
        content = str(message.get("Content", "")).strip()
        chat_id = message.get("FromWxid")
        if content == self.SYNC_CONTACTS_COMMAND:
            await self.sync_contacts_to_mysql(bot, chat_id)
            return False
        elif content == self.SYNC_GROUPS_COMMAND:
            await self.sync_groups_to_mysql(bot, chat_id)
            return False
        return True

    async def close(self):
        if self.mysql_db_connection:
            self.mysql_db_connection.close()
            logger.info("MySQL 数据库连接已关闭")