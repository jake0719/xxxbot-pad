import os
import json
import time
from datetime import datetime
from loguru import logger
import aiomysql
from typing import Optional, List, Dict

# 数据库config
config = {
    "host": "0.0.0.0",
    "port": 3306,
    "user": "admin",
    "password": "admin123",
    "database": "ai_test"  
}

def ensure_db_config():
    """确保数据库配置存在"""
    try:
        # 从配置文件中读取通知设置
        from pathlib import Path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = Path(current_dir).parent / "main_config.toml"
        try:
            import tomllib
            with open(config_path, "rb") as f:
                # 使用全局导入的tomllib
                config_data = tomllib.load(f)
        except Exception as e:
            logger.error(f"读取main_config.toml 配置文件失败: {str(e)}")
            return

        # 获取通知设置
        mysql_config = config_data.get("MySQL", {})

        config["host"] = mysql_config.get("host", "0.0.0.0")
        config["port"] = mysql_config.get("port", 3306)
        config["user"] = mysql_config.get("user", "root")
        config["password"] = mysql_config.get("password", "admin123")
        config["database"] = mysql_config.get("database", "ai_test")

    except Exception as e:
        logger.error(f"获取数据库设置失败: {str(e)}")


# 数据库服务模块
class Database:
    pool: Optional[aiomysql.Pool] = None

    @classmethod
    async def get_pool(cls) -> aiomysql.Pool:
        if not cls.pool:
            cls.pool = await aiomysql.create_pool(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                db=config["database"],
                autocommit=True
            )
        return cls.pool

    @classmethod
    async def close_pool(cls):
        if cls.pool:
            cls.pool.close()
            await cls.pool.wait_closed()

    @classmethod
    async def create_contacts_table(cls):
        """创建联系人表"""
        async with (await cls.get_pool()).acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('''
                CREATE TABLE IF NOT EXISTS contacts (
                    wxid VARCHAR(255) PRIMARY KEY,
                    nickname VARCHAR(255),
                    remark VARCHAR(255),
                    avatar VARCHAR(255),
                    alias VARCHAR(255),
                    type VARCHAR(50),
                    region VARCHAR(255),
                    last_updated INT,
                    extra_data TEXT
                )
                ''')

    @classmethod
    async def get_contacts_from_db(cls, offset: Optional[int] = None, limit: Optional[int] = None) -> List[Dict]:
        """从数据库获取联系人，支持分页

        Args:
            offset: 偏移量，从第几条记录开始获取
            limit: 限制返回的记录数量

        Returns:
            联系人列表
        """
        async with (await cls.get_pool()).acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                # 构建查询语句，支持分页
                query = "SELECT * FROM contacts ORDER BY nickname COLLATE NOCASE"
                params = []

                # 添加分页参数
                if limit is not None:
                    query += " LIMIT %s"
                    params.append(limit)

                    if offset is not None:
                        query += " OFFSET %s"
                        params.append(offset)

                # 执行查询
                await cursor.execute(query, params)
                rows = await cursor.fetchall()

                contacts = []
                for row in rows:
                    contact = dict(row)
                    # 解析额外数据
                    if contact.get("extra_data"):
                        try:
                            extra_data = json.loads(contact["extra_data"])
                            contact.update(extra_data)
                        except:
                            pass
                    contacts.append(contact)

                # 记录日志，区分是否分页
                if offset is not None or limit is not None:
                    logger.info(f"从数据库加载了 {len(contacts)} 个联系人 (offset={offset}, limit={limit})")
                else:
                    logger.info(f"从数据库加载了所有 {len(contacts)} 个联系人")

                return contacts

    @classmethod
    async def save_contacts_to_db(cls, contacts: List[Dict]) -> bool:
        """保存联系人列表到数据库"""
        try:
            async with (await cls.get_pool()).acquire() as conn:
                async with conn.cursor() as cursor:
                    # 创建表（如果不存在）
                    await cls.create_contacts_table()

                    # 准备批量插入
                    current_time = int(time.time())
                    for contact in contacts:
                        # 提取基本字段
                        wxid = contact.get("wxid", "")
                        nickname = contact.get("nickname", "")
                        remark = contact.get("remark", "")
                        avatar = contact.get("avatar", "")
                        alias = contact.get("alias", "")

                        # 确定联系人类型
                        contact_type = contact.get("type", "")
                        if not contact_type:
                            if wxid.endswith("@chatroom"):
                                contact_type = "group"
                            elif wxid.startswith("gh_"):
                                contact_type = "official"
                            else:
                                contact_type = "friend"

                        # 其他字段
                        region = contact.get("region", "")

                        # 将其他字段存储为JSON
                        extra_data = {}
                        for key, value in contact.items():
                            if key not in ["wxid", "nickname", "remark", "avatar", "alias", "type", "region"]:
                                extra_data[key] = value

                        extra_data_json = json.dumps(extra_data, ensure_ascii=False)

                        # 插入或更新联系人
                        await cursor.execute('''
                        INSERT INTO contacts
                        (wxid, nickname, remark, avatar, alias, type, region, last_updated, extra_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            nickname = VALUES(nickname),
                            remark = VALUES(remark),
                            avatar = VALUES(avatar),
                            alias = VALUES(alias),
                            type = VALUES(type),
                            region = VALUES(region),
                            last_updated = VALUES(last_updated),
                            extra_data = VALUES(extra_data)
                        ''', (
                            wxid,
                            nickname,
                            remark,
                            avatar,
                            alias,
                            contact_type,
                            region,
                            current_time,
                            extra_data_json
                        ))

                    await conn.commit()
                    logger.success(f"成功保存 {len(contacts)} 个联系人到数据库")
                    return True
        except Exception as e:
            logger.error(f"保存联系人到数据库失败: {str(e)}")
            return False

    @classmethod
    async def update_contact_in_db(cls, contact: Dict) -> bool:
        """更新单个联系人信息"""
        wxid = contact.get("wxid", "")
        if not wxid:
            logger.error("更新联系人失败: 缺少wxid")
            return False

        try:
            async with (await cls.get_pool()).acquire() as conn:
                async with conn.cursor() as cursor:
                    # 创建表（如果不存在）
                    await cls.create_contacts_table()

                    # 提取基本字段
                    nickname = contact.get("nickname", "")
                    remark = contact.get("remark", "")
                    avatar = contact.get("avatar", "")
                    alias = contact.get("alias", "")

                    # 确定联系人类型
                    contact_type = contact.get("type", "")
                    if not contact_type:
                        if wxid.endswith("@chatroom"):
                            contact_type = "group"
                        elif wxid.startswith("gh_"):
                            contact_type = "official"
                        else:
                            contact_type = "friend"

                    # 其他字段
                    region = contact.get("region", "")
                    current_time = int(time.time())

                    # 将其他字段存储为JSON
                    extra_data = {}
                    for key, value in contact.items():
                        if key not in ["wxid", "nickname", "remark", "avatar", "alias", "type", "region"]:
                            extra_data[key] = value

                    extra_data_json = json.dumps(extra_data, ensure_ascii=False)

                    logger.info(f"保存联系人 extra_data_json : {extra_data_json}")

                    # 检查联系人是否存在
                    await cursor.execute("SELECT wxid FROM contacts WHERE wxid = %s", (wxid,))
                    exists = await cursor.fetchone()

                    if exists:
                        # 更新现有联系人
                        await cursor.execute('''
                        UPDATE contacts SET
                            nickname = %s,
                            remark = %s,
                            avatar = %s,
                            alias = %s,
                            type = %s,
                            region = %s,
                            last_updated = %s,
                            extra_data = %s
                        WHERE wxid = %s
                        ''', (
                            nickname,
                            remark,
                            avatar,
                            alias,
                            contact_type,
                            region,
                            current_time,
                            extra_data_json,
                            wxid
                        ))
                        logger.info(f"更新联系人: {wxid}")
                    else:
                        # 插入新联系人
                        await cursor.execute('''
                        INSERT INTO contacts
                        (wxid, nickname, remark, avatar, alias, type, region, last_updated, extra_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ''', (
                            wxid,
                            nickname,
                            remark,
                            avatar,
                            alias,
                            contact_type,
                            region,
                            current_time,
                            extra_data_json
                        ))
                        logger.info(f"新增联系人: {wxid}")

                    await conn.commit()
                    return True
        except Exception as e:
            logger.error(f"更新联系人 {wxid} 失败: {str(e)}")
            return False

    @classmethod
    async def get_contact_from_db(cls, wxid: str) -> Optional[Dict]:
        """从数据库获取单个联系人信息"""
        async with (await cls.get_pool()).acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                # 查询联系人
                await cursor.execute("SELECT * FROM contacts WHERE wxid = %s", (wxid,))
                row = await cursor.fetchone()

                if row:
                    contact = dict(row)
                    # 解析额外数据
                    if contact.get("extra_data"):
                        try:
                            extra_data = json.loads(contact["extra_data"])
                            contact.update(extra_data)
                        except:
                            pass
                    return contact
                else:
                    return None

    @classmethod
    async def delete_contact_from_db(cls, wxid: str) -> bool:
        """从数据库删除联系人"""
        try:
            async with (await cls.get_pool()).acquire() as conn:
                async with conn.cursor() as cursor:
                    # 删除联系人
                    await cursor.execute("DELETE FROM contacts WHERE wxid = %s", (wxid,))
                    await conn.commit()
                    logger.info(f"从数据库删除联系人: {wxid}")
                    return True
        except Exception as e:
            logger.error(f"从数据库删除联系人 {wxid} 失败: {str(e)}")
            return False

    @classmethod
    async def get_contacts_count(cls) -> int:
        """获取数据库中联系人数量"""
        try:
            async with (await cls.get_pool()).acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT COUNT(*) FROM contacts")
                    count = await cursor.fetchone()
                    return count[0]
        except Exception as e:
            logger.error(f"获取联系人数量失败: {str(e)}")
            return 0

    @classmethod
    async def get_all_contacts(cls) -> List[Dict]:
        """获取数据库中所有联系人

        Returns:
            联系人列表
        """
        # 直接调用不带分页参数的get_contacts_from_db函数
        return await cls.get_contacts_from_db()
