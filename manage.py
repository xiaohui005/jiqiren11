import os
import sys
import importlib.util
from flask import Flask, render_template, request, redirect, url_for, flash, request as flask_request, jsonify
import pymysql
import requests
import datetime
from flask import jsonify
from config import (
    SEND_INTERVAL, SEND_MODE, SEND_TIME, TOKEN, DB_CONFIG,
    TELEGRAM_API_BASE, TABLE_TELEGRAM_CHATS, TABLE_SEND_QUEUE,
    FIELD_CHAT_ID, FIELD_TITLE, FIELD_TYPE, FIELD_CONTENT, FIELD_STATUS, FIELD_SEND_TIME, FIELD_CREATE_TIME, FIELD_UPDATE_TIME, FIELD_ALLOW_SEND,
    FLASK_PORT
)
import threading
import time
import traceback

# 动态加载exe同目录下的config.py
def load_config():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, 'config.py')
    spec = importlib.util.spec_from_file_location("config", config_path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    return config

config = load_config()

# 兼容pyinstaller打包后templates路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
app.secret_key = 'your_secret_key'

send_lock = threading.Lock()

DB_CONFIG = dict(config.DB_CONFIG)
DB_CONFIG['cursorclass'] = pymysql.cursors.DictCursor

def get_db():
    return pymysql.connect(**DB_CONFIG)

def get_chats():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'SELECT {FIELD_CHAT_ID}, {FIELD_TITLE}, {FIELD_ALLOW_SEND} FROM {TABLE_TELEGRAM_CHATS}')
    chats = cursor.fetchall()
    cursor.close()
    conn.close()
    return chats

def send_message(chat_id, text):
    url = f'{TELEGRAM_API_BASE}{TOKEN}/sendMessage'
    data = {
        'chat_id': chat_id,
        'text': text
    }
    resp = requests.post(url, data=data)
    return resp.json()

def log_send_result(queue_id, chat_id, chat_title, status, error_msg):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO bot_send_log (queue_id, chat_id, chat_title, status, error_msg) VALUES (%s, %s, %s, %s, %s)',
        (queue_id, chat_id, chat_title, status, error_msg)
    )
    conn.commit()
    cursor.close()
    conn.close()

def get_pending_message():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'SELECT * FROM {TABLE_SEND_QUEUE} WHERE {FIELD_STATUS}=0 ORDER BY {FIELD_CREATE_TIME} LIMIT 1')
    msg = cursor.fetchone()
    cursor.close()
    conn.close()
    return msg

def mark_message_sent(msg_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=1, {FIELD_SEND_TIME}=NOW() WHERE id=%s', (msg_id,))
    conn.commit()
    cursor.close()
    conn.close()

def mark_message_failed(msg_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=2 WHERE id=%s', (msg_id,))
    conn.commit()
    cursor.close()
    conn.close()

def get_yesterday_pending_messages():
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    start = datetime.datetime.combine(yesterday, datetime.time.min)
    end = datetime.datetime.combine(yesterday, datetime.time.max)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM {TABLE_SEND_QUEUE} WHERE {FIELD_STATUS}=0 AND {FIELD_CREATE_TIME} BETWEEN %s AND %s ORDER BY {FIELD_CREATE_TIME}",
        (start, end)
    )
    msgs = cursor.fetchall()
    cursor.close()
    conn.close()
    return msgs

def get_all_pending_messages():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {TABLE_SEND_QUEUE} WHERE {FIELD_STATUS}=0 ORDER BY {FIELD_CREATE_TIME}")
    msgs = cursor.fetchall()
    cursor.close()
    conn.close()
    return msgs

@app.route('/')
def index():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'SELECT * FROM {TABLE_SEND_QUEUE} ORDER BY id DESC')
    messages = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('index.html', messages=messages)

@app.route('/add', methods=['POST'])
def add():
    content = flask_request.form[FIELD_CONTENT]
    if not content.strip():
        flash('消息内容不能为空')
        return redirect(url_for('index'))
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'INSERT INTO {TABLE_SEND_QUEUE} ({FIELD_CONTENT}) VALUES (%s)', (content,))
    conn.commit()
    cursor.close()
    conn.close()
    flash('消息已添加到队列')
    return redirect(url_for('index'))

@app.route('/delete/<int:msg_id>')
def delete(msg_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'DELETE FROM {TABLE_SEND_QUEUE} WHERE id=%s', (msg_id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash('消息已删除')
    return redirect(url_for('index'))

@app.route('/retry/<int:msg_id>')
def retry(msg_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=0 WHERE id=%s', (msg_id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash('消息已重置为待发送')
    return redirect(url_for('index'))

@app.route('/sendnow/<int:msg_id>')
def sendnow(msg_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'SELECT * FROM {TABLE_SEND_QUEUE} WHERE id=%s', (msg_id,))
    msg = cursor.fetchone()
    if not msg:
        flash('消息不存在')
        return redirect(url_for('index'))
    if msg[FIELD_STATUS] == 1:
        flash('该消息已发送')
        return redirect(url_for('index'))
    chats = get_chats()
    all_success = True
    for chat in chats:
        result = send_message(chat[FIELD_CHAT_ID], msg[FIELD_CONTENT])
        ok = result.get('ok')
        log_send_result(msg['id'], chat[FIELD_CHAT_ID], chat[FIELD_TITLE], 0 if ok else 1, '' if ok else str(result))
        if not ok:
            all_success = False
    if all_success:
        cursor.execute(f'UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=1, {FIELD_SEND_TIME}=NOW() WHERE id=%s', (msg_id,))
        flash('消息已立即群发并标记为已发送')
    else:
        cursor.execute(f'UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=2 WHERE id=%s', (msg_id,))
        flash('部分群聊发送失败，已标记为发送失败')
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('index'))

@app.route('/send_yesterday', methods=['POST'])
def send_yesterday():
    with send_lock:
        msgs = get_yesterday_pending_messages()
        if not msgs:
            flash('昨天没有待发送的消息')
            return redirect(url_for('index'))
        chats = get_chats()
        for idx, msg in enumerate(msgs):
            all_success = True
            for chat in chats:
                try:
                    result = send_message(chat[FIELD_CHAT_ID], msg[FIELD_CONTENT])
                    ok = result.get('ok')
                    log_send_result(msg['id'], chat[FIELD_CHAT_ID], chat[FIELD_TITLE], 0 if ok else 1, '' if ok else str(result))
                    if not ok:
                        all_success = False
                except Exception as e:
                    traceback.print_exc()
                    all_success = False
            if all_success:
                mark_message_sent(msg['id'])
            else:
                mark_message_failed(msg['id'])
            if idx < len(msgs) - 1:
                time.sleep(3)
        flash(f'已群发昨天的 {len(msgs)} 条消息')
    return redirect(url_for('index'))

@app.route('/send_all_pending', methods=['POST'])
def send_all_pending():
    with send_lock:
        msgs = get_all_pending_messages()
        if not msgs:
            flash('没有待发送的消息')
            return redirect(url_for('index'))
        chats = get_chats()
        for idx, msg in enumerate(msgs):
            all_success = True
            for chat in chats:
                try:
                    result = send_message(chat[FIELD_CHAT_ID], msg[FIELD_CONTENT])
                    ok = result.get('ok')
                    log_send_result(msg['id'], chat[FIELD_CHAT_ID], chat[FIELD_TITLE], 0 if ok else 1, '' if ok else str(result))
                    if not ok:
                        all_success = False
                except Exception as e:
                    traceback.print_exc()
                    all_success = False
            if all_success:
                mark_message_sent(msg['id'])
            else:
                mark_message_failed(msg['id'])
            if idx < len(msgs) - 1:
                time.sleep(3)
        flash(f'已群发所有待发送的 {len(msgs)} 条消息')
    return redirect(url_for('index'))

@app.route('/logs')
def logs():
    queue_id = flask_request.args.get('queue_id')
    conn = get_db()
    cursor = conn.cursor()
    if queue_id:
        cursor.execute('SELECT * FROM bot_send_log WHERE queue_id=%s ORDER BY id DESC LIMIT 100', (queue_id,))
    else:
        cursor.execute('SELECT * FROM bot_send_log ORDER BY id DESC LIMIT 100')
    logs = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('logs.html', logs=logs)

@app.route('/chats')
def chats():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'SELECT * FROM {TABLE_TELEGRAM_CHATS} ORDER BY id DESC')
    chats = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('chats.html', chats=chats)

@app.route('/toggle_allow_send/<int:chat_id>')
def toggle_allow_send(chat_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'SELECT {FIELD_ALLOW_SEND} FROM {TABLE_TELEGRAM_CHATS} WHERE {FIELD_CHAT_ID}=%s', (chat_id,))
    row = cursor.fetchone()
    if row is not None:
        new_val = 0 if row[FIELD_ALLOW_SEND] else 1
        cursor.execute(f'UPDATE {TABLE_TELEGRAM_CHATS} SET {FIELD_ALLOW_SEND}=%s WHERE {FIELD_CHAT_ID}=%s', (new_val, chat_id))
        conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('chats'))

@app.route('/batch_action', methods=['POST'])
def batch_action():
    action = flask_request.form.get('action')
    msg_ids = flask_request.form.getlist('msg_ids')
    if not msg_ids:
        flash('请至少选择一条消息')
        return redirect(url_for('index'))
    conn = get_db()
    cursor = conn.cursor()
    if action == 'delete':
        cursor.execute(f"DELETE FROM {TABLE_SEND_QUEUE} WHERE id IN ({','.join(['%s']*len(msg_ids))})", msg_ids)
        conn.commit()
        flash('批量删除成功')
    elif action == 'retry':
        cursor.execute(f"UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=0 WHERE id IN ({','.join(['%s']*len(msg_ids))})", msg_ids)
        conn.commit()
        flash('批量重发已设置')
    elif action == 'sendnow':
        chats = get_chats()
        for msg_id in msg_ids:
            cursor.execute(f'SELECT * FROM {TABLE_SEND_QUEUE} WHERE id=%s', (msg_id,))
            msg = cursor.fetchone()
            if not msg or msg[FIELD_STATUS] == 1:
                continue
            all_success = True
            for chat in chats:
                result = send_message(chat[FIELD_CHAT_ID], msg[FIELD_CONTENT])
                ok = result.get('ok')
                log_send_result(msg['id'], chat[FIELD_CHAT_ID], chat[FIELD_TITLE], 0 if ok else 1, '' if ok else str(result))
                if not ok:
                    all_success = False
            if all_success:
                cursor.execute(f'UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=1, {FIELD_SEND_TIME}=NOW() WHERE id=%s', (msg_id,))
            else:
                cursor.execute(f'UPDATE {TABLE_SEND_QUEUE} SET {FIELD_STATUS}=2 WHERE id=%s', (msg_id,))
        conn.commit()
        flash('批量手动发送已完成')
    else:
        flash('未知操作')
    cursor.close()
    conn.close()
    return redirect(url_for('index'))

@app.route('/batch_toggle_allow_send', methods=['POST'])
def batch_toggle_allow_send():
    action = flask_request.form.get('action')
    chat_ids = flask_request.form.getlist('chat_ids')
    if not chat_ids:
        flash('请至少选择一个群聊')
        return redirect(url_for('chats'))
    conn = get_db()
    cursor = conn.cursor()
    new_val = 1 if action == 'allow' else 0
    cursor.execute(
        f"UPDATE {TABLE_TELEGRAM_CHATS} SET {FIELD_ALLOW_SEND}=%s WHERE {FIELD_CHAT_ID} IN ({','.join(['%s']*len(chat_ids))})",
        [new_val] + chat_ids
    )
    conn.commit()
    cursor.close()
    conn.close()
    flash('批量操作成功')
    return redirect(url_for('chats'))

def auto_send_job():
    import importlib.util
    import sys, os
    def load_config_runtime():
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, 'config.py')
        spec = importlib.util.spec_from_file_location("config", config_path)
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        return config

    while True:
        print("[定时群发] 定时线程心跳，准备加载配置并判断是否群发...")
        config_rt = load_config_runtime()
        if config_rt.SEND_MODE == 'daily':
            now = datetime.datetime.now()
            send_hour, send_minute = map(int, config_rt.SEND_TIME.split(':'))
            next_send = now.replace(hour=send_hour, minute=send_minute, second=0, microsecond=0)
            if next_send <= now:
                next_send += datetime.timedelta(days=1)
            seconds = int((next_send - now).total_seconds())
            print(f"[定时群发] 距离下次群发还有 {seconds} 秒")
            time.sleep(seconds)
        else:
            print(f"[定时群发] 等待 {config_rt.SEND_INTERVAL} 秒后检测队列...")
            time.sleep(config_rt.SEND_INTERVAL)
        with send_lock:
            msgs = get_all_pending_messages()
            print(f"[定时群发] 本次将要发送 {len(msgs)} 条消息。")
            if not msgs:
                continue
            chats = get_chats()
            for idx, msg in enumerate(msgs):
                all_success = True
                for chat in chats:
                    try:
                        result = send_message(chat[FIELD_CHAT_ID], msg[FIELD_CONTENT])
                        ok = result.get('ok')
                        log_send_result(msg['id'], chat[FIELD_CHAT_ID], chat[FIELD_TITLE], 0 if ok else 1, '' if ok else str(result))
                        if not ok:
                            all_success = False
                    except Exception as e:
                        traceback.print_exc()
                        all_success = False
                if all_success:
                    mark_message_sent(msg['id'])
                else:
                    mark_message_failed(msg['id'])
                if idx < len(msgs) - 1:
                    time.sleep(3)

def start_auto_send():
    t = threading.Thread(target=auto_send_job, daemon=True)
    t.start()

start_auto_send()

def load_config_runtime():
    import sys, os, importlib.util
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, 'config.py')
    spec = importlib.util.spec_from_file_location("config", config_path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    return config

@app.route('/next_send_time')
def next_send_time():
    config_rt = load_config_runtime()  # 每次都动态加载
    now = datetime.datetime.now()
    if config_rt.SEND_MODE == 'daily':
        send_hour, send_minute = map(int, config_rt.SEND_TIME.split(':'))
        next_send = now.replace(hour=send_hour, minute=send_minute, second=0, microsecond=0)
        if next_send <= now:
            next_send += datetime.timedelta(days=1)
        ts = int(next_send.timestamp())
    else:
        ts = int((now + datetime.timedelta(seconds=config_rt.SEND_INTERVAL)).timestamp())
    return jsonify({'next_send_time': ts})

if __name__ == '__main__' or getattr(sys, 'frozen', False):
    app.run(debug=True, port=config.FLASK_PORT) 