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
                    province VARCHAR(255),
                    city VARCHAR(255),
                    signature VARCHAR(255),
                    avatar TEXT,
                    inviter_wxid VARCHAR(64),
                    join_time BIGINT,
                    last_updated TIMESTAMP,
                    extra_data JSON,
                    memo VARCHAR(255),
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
            contacts = await self.getContactsList(bot)
            # contacts = data.get("ContactList", [])
            if not contacts:
                await bot.send_text_message(chat_id, "未获取到任何联系人信息！")
                return
            
            
            if self.mysql_db_connection and not self.mysql_db_connection.is_connected():
                try:
                    self.mysql_db_connection = mysql.connector.connect(**self.db_config)
                    logger.info(f"MySQL 数据库重新建立连接")
                except Error as e:
                    logger.error("MySQL 数据库连接未建立，无法保存总结")
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
            contacts = await self.getContactsList(bot)
            # contacts = data.get("ContactList", [])
            group_list = [c for c in contacts if (c.get("UserName") or c.get("wxid", "")).endswith("@chatroom")]
            if not group_list:
                await bot.send_text_message(chat_id, "未获取到任何群聊信息！")
                return
            
            if self.mysql_db_connection and not self.mysql_db_connection.is_connected():
                try:
                    self.mysql_db_connection = mysql.connector.connect(**self.db_config)
                    logger.info(f"MySQL 数据库重新建立连接")
                except Error as e:
                    logger.error("MySQL 数据库连接未建立，无法保存总结")
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

                logger.info(f"group_info: {group_info}")

                nickname = group_info.get('NickName', {}) or group_info.get("nickname", {})
                if nickname:
                    nickname = nickname.get('string')
                else:
                    nickname = ""

                avatar = group_info.get("HeadImgUrl") or group_info.get("avatar") or group_info.get("SmallHeadImgUrl")
                # SmallHeadImgUrl

                # extra_data = {k: v for k, v in group_info.items() if k not in ["UserName", "wxid", "NickName", "nickname", "HeadImgUrl", "avatar"]}
                # logger.info(f"group_wxid, nickname, avatar, extra_data: {group_wxid}, {nickname}, {avatar}, {json.dumps(extra_data, ensure_ascii=False)}")

                # 保存群信息到contacts表
                cursor.execute('''
                    INSERT INTO contacts (wxid, nickname, remark, avatar, alias, type, region, last_updated, extra_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        nickname=VALUES(nickname), avatar=VALUES(avatar), type=VALUES(type),
                        last_updated=VALUES(last_updated), extra_data=VALUES(extra_data)
                ''', (
                    group_wxid, nickname, "", avatar, "", "group", "", now, json.dumps(group_info, ensure_ascii=False)
                ))
                group_count += 1
                # 获取群成员
                # try:
                #     members = await bot.get_chatroom_member_list(group_wxid)
                # except Exception as e:
                #     logger.warning(f"获取群 {group_wxid} 成员失败: {e}")
                #     continue

                members = group_info.get("NewChatroomData", {}).get("ChatRoomMember", [])

                # print members
                logger.info(f"members: {members}")

                for memberWxId in members:

                    member = await self.get_contact_info_by_wxid(bot, memberWxId.get("UserName"))

                    logger.info(f"member: {member}")

                    if not member:
                        continue

                    member_wxid = member.get("UserName").get("string") or member.get("wxid")
                    if not member_wxid:
                        continue
                    nickname = member.get("NickName").get("string") or member.get("nickname")
                    # display_name = member.get("DisplayName") or member.get("display_name")
                    province = member.get("Province") or member.get("province")
                    city = member.get("City") or member.get("city")
                    signature = member.get("Signature") or member.get("signature")
                    avatar = member.get("SmallHeadImgUrl") or member.get("avatar")
                    # inviter_wxid = member.get("InviterUserName")
                    # join_time = member.get("JoinTime")
                    # extra_data = {k: v for k, v in member.items() if k not in ["UserName", "wxid", "NickName", "nickname", "DisplayName", "display_name", "HeadImgUrl", "avatar", "InviterUserName", "JoinTime"]}
                    
                    
                    cursor.execute('''
                        INSERT INTO group_members (group_wxid, member_wxid, nickname, display_name, province, city, signature, avatar, inviter_wxid, join_time, last_updated, extra_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            nickname=VALUES(nickname), display_name=VALUES(display_name), avatar=VALUES(avatar),province=VALUES(province),city=VALUES(city),signature=VALUES(signature),
                            inviter_wxid=VALUES(inviter_wxid), join_time=VALUES(join_time), last_updated=VALUES(last_updated), extra_data=VALUES(extra_data)
                    ''', (
                        group_wxid, member_wxid, nickname, nickname, province, city, signature, avatar, "", "", now, json.dumps(member, ensure_ascii=False)
                    ))
                    member_count += 1
            self.mysql_db_connection.commit()
            logger.info(f"成功保存 {group_count} 个群及 {member_count} 个群成员到MySQL")
            await bot.send_text_message(chat_id, f"成功保存 {group_count} 个群及 {member_count} 个群成员到MySQL")
        except Exception as e:
            logger.exception(f"同步群信息到MySQL失败: {e}")
            await bot.send_text_message(chat_id, f"同步群信息失败: {e}")

    async def getContactsList(self, bot: WechatAPIClient) -> dict:
        # 初始化序列号
        wx_seq = 0
        chatroom_seq = 0
        all_contacts_data = {"ContactUsernameList": []}
       # 递归获取所有联系人
        iteration = 0
        total_contacts = 0

        # 不设置最大迭代次数，由系统自动识别何时已获取所有联系人
        while True:
            iteration += 1
            logger.info(f"获取联系人批次 {iteration}，当前序列号: wx_seq={wx_seq}, chatroom_seq={chatroom_seq}")

            # 获取当前批次的联系人
            if asyncio.get_event_loop().is_running():
                batch_data = await bot.get_contract_list(wx_seq=wx_seq, chatroom_seq=chatroom_seq)
            else:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    batch_data = loop.run_until_complete(bot.get_contract_list(wx_seq=wx_seq, chatroom_seq=chatroom_seq))
                finally:
                    loop.close()

            # 检查返回数据
            if not batch_data or not isinstance(batch_data, dict) or 'ContactUsernameList' not in batch_data:
                logger.warning(f"批次 {iteration} 返回数据无效或格式不正确")
                break

            # 获取当前批次的联系人数量
            batch_contacts = batch_data.get('ContactUsernameList', [])
            batch_count = len(batch_contacts)
            total_contacts += batch_count
            logger.info(f"批次 {iteration} 获取到 {batch_count} 个联系人，累计 {total_contacts} 个")

            # 合并联系人列表
            if iteration == 1:
                # 第一批次，直接使用返回数据
                all_contacts_data = batch_data
            else:
                # 后续批次，合并联系人列表
                all_contacts_data['ContactUsernameList'].extend(batch_contacts)

            # 检查是否有新的序列号
            new_wx_seq = batch_data.get('CurrentWxcontactSeq', 0)
            new_chatroom_seq = batch_data.get('CurrentChatroomContactSeq', 0)

            # 如果序列号没有变化或者没有返回联系人，说明已经获取完所有联系人
            if (new_wx_seq == wx_seq and new_chatroom_seq == chatroom_seq) or batch_count == 0:
                logger.info(f"序列号没有变化或者没有新的联系人，结束获取")
                break

            logger.info(f"new_wx_seq: {new_wx_seq}")
            logger.info(f"new_chatroom_seq: {new_chatroom_seq}")
            logger.info(f"batch_contacts: {batch_contacts}")

            # 更新序列号继续获取
            wx_seq = new_wx_seq
            chatroom_seq = new_chatroom_seq

        # 提取联系人列表
        contact_usernames = all_contacts_data['ContactUsernameList']
        logger.info(f"找到{len(contact_usernames)}个联系人ID")

        # 构建联系人对象
        contact_list = []

        # 检查是否支持获取联系人详情
        has_contract_detail_method = hasattr(bot, 'get_contract_detail')

        if has_contract_detail_method:
            logger.info("使用get_contract_detail方法获取联系人详细信息")

            # 分批获取联系人详情 (每批最多20个)
            batch_size = 20
            all_contact_details = {}

            # 计算总批次数
            total_batches = (len(contact_usernames) + batch_size - 1) // batch_size
            logger.info(f"联系人总数: {len(contact_usernames)}, 批次大小: {batch_size}, 总批次: {total_batches}")

            # 不限制批次数量，获取所有联系人
            max_batches = total_batches  # 获取所有批次

            for i in range(0, min(max_batches * batch_size, len(contact_usernames)), batch_size):
                batch = contact_usernames[i:i+batch_size]
                logger.debug(f"获取联系人详情批次 {i//batch_size+1}/{total_batches}: {batch}")

                try:
                    # 调用API获取联系人详情
                    if asyncio.get_event_loop().is_running():
                        contact_details = await bot.get_contract_detail(batch)
                    else:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            contact_details = loop.run_until_complete(bot.get_contract_detail(batch))
                        finally:
                            loop.close()

                    # 改为INFO级别确保输出
                    logger.info(f"批次{i//batch_size+1}获取到联系人详情: {len(contact_details)}个")

                    # 强制打印每个批次的第一个联系人信息作为样本
                    if contact_details and len(contact_details) > 0:
                        logger.info(f"联系人详情样本结构: {json.dumps(contact_details[0], ensure_ascii=False)}")

                    # 显式记录关键字段是否存在
                    if contact_details and len(contact_details) > 0:
                        first_contact = contact_details[0]
                        logger.info(f"联系人[{first_contact.get('UserName', 'unknown')}]有以下字段: {sorted(first_contact.keys())}")

                        # 检查并记录关键字段
                        for field in ['UserName', 'NickName', 'Remark', 'SmallHeadImgUrl', 'BigHeadImgUrl']:
                            if field in first_contact:
                                logger.info(f"字段[{field}]存在，值为: {first_contact[field]}")
                            else:
                                logger.info(f"字段[{field}]不存在")

                    # 将联系人详情与wxid关联
                    for contact_detail in contact_details:
                        # 处理各种可能的用户ID字段
                        wxid_found = False

                        # 处理UserName字段
                        if 'UserName' in contact_detail and contact_detail['UserName']:
                            # 处理嵌套结构，UserName可能是{"string": "wxid"}格式
                            if isinstance(contact_detail['UserName'], dict) and 'string' in contact_detail['UserName']:
                                wxid = contact_detail['UserName']['string']
                                wxid_found = True
                            else:
                                wxid = str(contact_detail['UserName'])
                                wxid_found = True

                        # 如果没有UserName，尝试Username字段
                        elif 'Username' in contact_detail and contact_detail['Username']:
                            if isinstance(contact_detail['Username'], dict) and 'string' in contact_detail['Username']:
                                wxid = contact_detail['Username']['string']
                                wxid_found = True
                            else:
                                wxid = str(contact_detail['Username'])
                                wxid_found = True

                        # 如果没有Username，尝试wxid字段
                        elif 'wxid' in contact_detail and contact_detail['wxid']:
                            wxid = contact_detail['wxid']
                            wxid_found = True

                        # 如果没有找到任何ID字段，跳过这个联系人
                        if not wxid_found:
                            logger.warning(f"联系人缺少ID字段: {contact_detail}")
                            continue

                        all_contact_details[wxid] = contact_detail
                        if wxid in batch[:3]:  # 只记录前3个，避免日志过多
                            logger.info(f"联系人[{wxid}]头像信息: " +
                                        f"SmallHeadImgUrl={contact_detail.get('SmallHeadImgUrl', 'None')}, " +
                                        f"BigHeadImgUrl={contact_detail.get('BigHeadImgUrl', 'None')}")
                except Exception as e:
                    logger.error(f"获取联系人详情批次失败 ({i}~{i+batch_size-1}): {e}")
                    # logger.error(traceback.format_exc())

            # 根据获取的详细信息创建联系人对象
            for username in contact_usernames:
                # 根据wxid格式确定联系人类型
                contact_type = "friend"
                if username.endswith("@chatroom"):
                    contact_type = "group"
                elif username.startswith("gh_"):
                    contact_type = "official"

                # 获取联系人详情
                contact_detail = all_contact_details.get(username, {})

                # 提取字段
                nickname = ""
                remark = ""
                avatar = "/static/img/favicon.ico"

                # 提取昵称 - 处理各种可能的字段名称和结构
                nickname = ""
                if contact_detail:
                    # 处理NickName字段
                    if 'NickName' in contact_detail:
                        if isinstance(contact_detail['NickName'], dict) and 'string' in contact_detail['NickName']:
                            nickname = contact_detail['NickName']['string']
                        else:
                            nickname = str(contact_detail['NickName'])
                    # 处理nickname字段
                    elif 'nickname' in contact_detail:
                        nickname = contact_detail.get('nickname')

                # 如果昵称为空，使用用户名
                if not nickname or nickname == '{}':
                    nickname = username

                # 提取备注 - 处理各种可能的字段名称和结构
                remark = ""
                if contact_detail:
                    # 处理Remark字段
                    if 'Remark' in contact_detail:
                        if isinstance(contact_detail['Remark'], dict) and 'string' in contact_detail['Remark']:
                            remark = contact_detail['Remark']['string']
                        elif isinstance(contact_detail['Remark'], str):
                            remark = contact_detail['Remark']
                    # 处理remark字段
                    elif 'remark' in contact_detail:
                        remark = contact_detail.get('remark')

                # 提取头像 URL - 处理各种可能的字段名称
                avatar = "/static/img/favicon.ico"  # 默认头像
                if contact_detail:
                    # 优先使用小头像
                    if 'SmallHeadImgUrl' in contact_detail and contact_detail['SmallHeadImgUrl']:
                        avatar = contact_detail['SmallHeadImgUrl']
                        logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                    # 其次使用大头像
                    elif 'BigHeadImgUrl' in contact_detail and contact_detail['BigHeadImgUrl']:
                        avatar = contact_detail['BigHeadImgUrl']
                        logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                    # 最后尝试avatar字段
                    elif 'avatar' in contact_detail and contact_detail['avatar']:
                        avatar = contact_detail['avatar']
                        logger.debug(f"使用avatar作为头像: {avatar}")

                # 确定显示名称（优先使用备注，其次昵称，最后是wxid）
                display_name = remark or nickname or username

                # 创建联系人对象
                contact = {
                    "wxid": username,
                    "name": display_name,
                    "nickname": nickname,
                    "remark": remark,
                    "avatar": avatar,
                    "type": contact_type,
                    "online": True,
                    "starred": False
                }
                contact_list.append(contact)
        else:
            # 回退到使用昵称API
            has_nickname_method = hasattr(bot, 'get_nickname')
            if has_nickname_method:
                logger.info("使用get_nickname方法获取联系人昵称")

                # 分批获取联系人昵称 (每批最多20个)
                batch_size = 20
                all_nicknames = {}

                # 计算总批次数
                total_batches = (len(contact_usernames) + batch_size - 1) // batch_size
                logger.info(f"联系人总数: {len(contact_usernames)}, 批次大小: {batch_size}, 总批次: {total_batches}")

                # 不限制批次数量，获取所有联系人
                max_batches = total_batches  # 获取所有批次

                # 分批处理联系人
                for i in range(0, min(max_batches * batch_size, len(contact_usernames)), batch_size):
                    batch = contact_usernames[i:i+batch_size]
                    logger.debug(f"获取联系人昵称批次 {i//batch_size+1}/{total_batches}: {batch}")
                    try:
                        # 调用API获取昵称
                        if asyncio.get_event_loop().is_running():
                            nicknames = await bot.get_nickname(batch)
                        else:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                nicknames = loop.run_until_complete(bot.get_nickname(batch))
                            finally:
                                loop.close()

                        # 将昵称与wxid关联
                        for j, wxid in enumerate(batch):
                            if j < len(nicknames) and nicknames[j]:
                                all_nicknames[wxid] = nicknames[j]
                            else:
                                all_nicknames[wxid] = wxid
                    except Exception as e:
                        logger.error(f"获取昵称批次失败 ({i}~{i+batch_size-1}): {e}")
                        # 对失败批次使用wxid作为昵称
                        for wxid in batch:
                            all_nicknames[wxid] = wxid
            else:
                logger.warning("bot既没有get_contract_detail也没有get_nickname方法，将使用wxid作为昵称")
                all_nicknames = {username: username for username in contact_usernames}

            # 根据获取的昵称创建联系人对象
            for username in contact_usernames:
                # 根据wxid格式确定联系人类型
                contact_type = "friend"
                if username.endswith("@chatroom"):
                    contact_type = "group"
                elif username.startswith("gh_"):
                    contact_type = "official"

                # 获取昵称（如果有）
                nickname = all_nicknames.get(username, username)
                display_name = nickname if nickname else username

                # 创建联系人对象
                contact = {
                    "wxid": username,
                    "name": display_name,
                    "nickname": nickname,
                    "remark": "",
                    "avatar": "/static/img/favicon.ico",
                    "type": contact_type,
                    "online": True,
                    "starred": False
                }
                contact_list.append(contact)

        logger.info(f"contact_list: {contact_list}")
        # 使用合并后的数据
        return contact_list

    async def get_contact_info_by_wxid(self, bot: WechatAPIClient, wxid: str) -> dict:
        """
        根据wxid查询微信用户详细信息。
        Args:
            bot: WechatAPIClient 实例
            wxid: 微信用户ID
        Returns:
            dict: 用户详细信息，获取失败返回None
        """
        try:
            logger.info(f"查询微信用户信息: wxid={wxid}")
            details = await bot.get_contract_detail([wxid])
            if details and isinstance(details, list) and len(details) > 0:
                logger.info(f"用户{wxid}信息: {json.dumps(details[0], ensure_ascii=False)}")
                return details[0]
            else:
                logger.warning(f"未获取到用户{wxid}的详细信息")
                return None
        except Exception as e:
            logger.error(f"获取用户{wxid}信息失败: {e}")
            return None

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