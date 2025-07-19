import requests
import pymysql
import time
import os
import datetime
from config import (
    SEND_INTERVAL, SEND_MODE, SEND_TIME, TOKEN, DB_CONFIG,
    TELEGRAM_API_BASE, TABLE_TELEGRAM_CHATS, TABLE_SEND_QUEUE,
    FIELD_CHAT_ID, FIELD_TITLE, FIELD_TYPE, FIELD_CONTENT, FIELD_STATUS, FIELD_SEND_TIME, FIELD_CREATE_TIME, FIELD_UPDATE_TIME, FIELD_ALLOW_SEND
)

DB_CONFIG = dict(DB_CONFIG)
DB_CONFIG['cursorclass'] = pymysql.cursors.DictCursor

# 记录已发送的期数，防止重复群发
done_qishu = set()

def get_chats_from_mysql():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute(f'SELECT {FIELD_CHAT_ID}, {FIELD_TITLE}, {FIELD_TYPE} FROM {TABLE_TELEGRAM_CHATS} WHERE {FIELD_ALLOW_SEND}=1')
    chats = cursor.fetchall()
    cursor.close()
    conn.close()
    return chats

def get_chat_ids_from_telegram():
    url = f'{TELEGRAM_API_BASE}{TOKEN}/getUpdates'
    resp = requests.get(url)
    data = resp.json()
    chat_ids = {}
    for result in data.get('result', []):
        message = result.get('message') or result.get('edited_message')
        if not message:
            continue
        chat = message['chat']
        chat_id = chat['id']
        title = chat.get('title', chat.get('username', chat.get('first_name', '')))
        chat_type = chat['type']
        chat_ids[chat_id] = {'title': title, 'type': chat_type}
    return chat_ids

def save_chats_to_mysql(chat_ids):
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    for chat_id, info in chat_ids.items():
        cursor.execute(f'''
            INSERT INTO {TABLE_TELEGRAM_CHATS} ({FIELD_CHAT_ID}, {FIELD_TITLE}, {FIELD_TYPE}, {FIELD_ALLOW_SEND})
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE {FIELD_TITLE}=VALUES({FIELD_TITLE}), {FIELD_TYPE}=VALUES({FIELD_TYPE})
        ''', (chat_id, info['title'], info['type'], 0))  # 默认禁止
    conn.commit()
    cursor.close()
    conn.close()

def create_send_queue_table():
    conn = pymysql.connect(
        host='localhost',
        user='root',
        password='root',
        database='lottery1',
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_send_queue (
            id INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
            content TEXT NOT NULL COMMENT '要发送的消息内容',
            status TINYINT NOT NULL DEFAULT 0 COMMENT '发送状态：0待发送 1已发送 2发送失败',
            send_time DATETIME DEFAULT NULL COMMENT '实际发送时间',
            create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='机器人待发送消息队列表';
    ''')
    conn.commit()
    cursor.close()
    conn.close()

def get_pending_message():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute(f'SELECT * FROM {TABLE_SEND_QUEUE} WHERE {FIELD_STATUS}=0 ORDER BY id ASC LIMIT 1')
    msg = cursor.fetchone()
    cursor.close()
    conn.close()
    return msg

def mark_message_sent(msg_id):
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute(f'UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=1, {FIELD_SEND_TIME}=NOW() WHERE id=%s', (msg_id,))
    conn.commit()
    cursor.close()
    conn.close()

def mark_message_failed(msg_id):
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute(f'UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=2 WHERE id=%s', (msg_id,))
    conn.commit()
    cursor.close()
    conn.close()

def get_latest_lottery():
    conn = pymysql.connect(
        host='localhost',
        user='root',
        password='root',
        database='lottery1',
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM antapp_lotterydraw ORDER BY id DESC LIMIT 1')
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row

def format_lottery_message(lottery):
    msg = (
        f"【最新彩票开奖】\n"
        f"期数: {lottery['qishu']}\n"
        f"开奖时间: {lottery['draw_time']}\n"
        f"开奖号码: {lottery['number1']}, {lottery['number2']}, {lottery['number3']}, "
        f"{lottery['number4']}, {lottery['number5']}, {lottery['number6']}, {lottery['number7']}\n"
    )
    if lottery.get('remark'):
        msg += f"备注: {lottery['remark']}"
    return msg

def send_message(chat_id, text):
    url = f'{TELEGRAM_API_BASE}{TOKEN}/sendMessage'
    data = {
        'chat_id': chat_id,
        'text': text
    }
    resp = requests.post(url, data=data)
    return resp.json()

def get_next_send_seconds(send_time_str):
    now = datetime.datetime.now()
    send_hour, send_minute = map(int, send_time_str.split(":"))
    next_send = now.replace(hour=send_hour, minute=send_minute, second=0, microsecond=0)
    if next_send <= now:
        next_send += datetime.timedelta(days=1)
    return int((next_send - now).total_seconds())

if __name__ == '__main__':
    if SEND_MODE == 'daily':
        print(f"启动机器人消息队列群发，每天 {SEND_TIME} 自动发送一次...")
    else:
        print(f"启动机器人消息队列群发，每{SEND_INTERVAL}秒检测一次是否有待发送消息...")
    # 不要在循环外查chats，循环内每次查最新
    if not get_chats_from_mysql():
        print("数据库中没有群聊信息，自动采集...请确保机器人已进群并群里有消息。")
        chat_ids = get_chat_ids_from_telegram()
        if not chat_ids:
            print("未检测到任何群聊或私聊消息，请先在群或私聊中发消息给机器人。")
            exit(1)
        save_chats_to_mysql(chat_ids)
        print(f"已采集并写入{len(chat_ids)}个群聊信息。请重启本脚本。")
        exit(0)
    while True:
        if SEND_MODE == 'daily':
            seconds = get_next_send_seconds(SEND_TIME)
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 距离下次群发还有 {seconds} 秒")
            time.sleep(seconds)
        else:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 检测队列...无消息则等待{SEND_INTERVAL}秒")
        msg = get_pending_message()
        if not msg:
            if SEND_MODE == 'daily':
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 没有待发送消息，等待下一个定时点...")
                continue
            else:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 没有待发送消息，等待{SEND_INTERVAL}秒...")
                time.sleep(SEND_INTERVAL)
                continue
        chats = get_chats_from_mysql()  # 每次群发前都查最新允许群聊
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 检测到待发送消息(id={msg['id']})，开始群发...")
        all_success = True
        for chat in chats:
            result = send_message(chat['chat_id'], msg[FIELD_CONTENT])
            if result.get('ok'):
                print(f"发送到 {chat['title']}({chat['chat_id']}) 成功！")
            else:
                print(f"发送到 {chat['title']}({chat['chat_id']}) 失败: {result}")
                all_success = False
        if all_success:
            mark_message_sent(msg['id'])
            print(f"消息(id={msg['id']})群发完成，状态已更新为已发送。")
        else:
            mark_message_failed(msg['id'])
            print(f"消息(id={msg['id']})群发有失败，状态已更新为发送失败。")
        if SEND_MODE == 'interval':
            time.sleep(SEND_INTERVAL) 