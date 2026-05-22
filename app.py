import os
import uuid
import datetime
import threading
import time
from flask import Flask, render_template, request, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import diff_match_patch as dmp_module

# --------------------
# 應用程式設定
# --------------------
def load_secret_key():
    secret_key = os.environ.get('SECRET_KEY')
    if secret_key:
        return secret_key
    # Secure fallback for local/dev usage when SECRET_KEY is not provided.
    print('WARNING: SECRET_KEY is not set. Using an ephemeral key for this process.')
    return os.urandom(32).hex()


def load_cors_allowed_origins():
    raw_value = os.environ.get('CORS_ALLOWED_ORIGINS', '').strip()
    if not raw_value:
        # Default to same-origin only.
        return None
    origins = [item.strip() for item in raw_value.split(',') if item.strip()]
    return origins if origins else None


app = Flask(__name__)
app.config['SECRET_KEY'] = load_secret_key()
socketio = SocketIO(
    app,
    cors_allowed_origins=load_cors_allowed_origins(),
    ping_interval=10,
    ping_timeout=5
)
dmp = dmp_module.diff_match_patch()

# --------------------
# 核心狀態管理 (簡化版)
# --------------------
class ScriptManager:
    """每個「房間」都會有一個獨立的 ScriptManager 實例。"""
    def __init__(self):
        # 初始文本 (已更新為純文字版使用指引)
        self.raw_text = """========================================
   歡迎使用 - 協作語音字幕編輯器（新版）
========================================

[1] 語音辨識規則
    - 同一房間同時間只允許一位導播啟動語音辨識。
    - 啟動後，未落地文字會在底部浮動區顯示；確認落地後才寫入文本區。
    - 「連續 / 換行 / 空行」段落模式可在辨識中即時切換。
    - 語音辨識進行中，僅辨識端可調整段落模式；其他端會同步顯示目前模式。

[2] 協作編修規則
    - 點入文本區即進入「編輯模式」，此端會暫停套用新的落地同步。
    - 編修完成請按 Esc 跳出編輯模式；若閒置 3 秒未動作也會自動退出編輯，並恢復同步最新內容。
    - 若超過「自動跳出編輯區秒數」無動作，系統會自動跳出編輯模式。
    - 此秒數設定為全房共享，任一端調整後全員立即生效。

[3] 其他功能
    - 右上可分享觀眾連結與 QR Code。
    - 左側可用常用片段（Ctrl + 數字）快速插入。
    - 支援匯入 / 匯出 .txt 與 Ctrl+H 批次取代。

----------------------------------------
請直接開始說話或編輯，祝使用順利。

小彩蛋：請點擊右上角作者的名字看看會發生什麼事!"""
        self.quick_inputs = {str(i): '' for i in range(1, 11)}
        # 樣式設定（拆分為導播端與觀眾端）
        self.director_settings = {
            'fontFamily': "'Microsoft JhengHei', '蘋方-繁', sans-serif",
            'fontSize': '24px',
            'lineHeight': 1.2,
            'fontStyle': 'normal',
            'fontWeight': 'normal',
            'color': '#000000',
            'backgroundColor': '#FFFFFF',
            'theme': 'light',
            'speechBreakMode': 'newline',
            'editIdleSeconds': 3
        }
        self.viewer_settings = {
            'fontFamily': "'Microsoft JhengHei', '蘋方-繁', sans-serif",
            'fontSize': '60px',
            'lineHeight': 1.2,
            'fontStyle': 'normal',
            'fontWeight': 'normal',
            'color': '#FFFFFF',
            'backgroundColor': '#000000',
            'theme': 'dark',
            'forceScrollBottom': False
        }
        self.speech_user = None
        self.interim_text = ''

    def get_full_state(self):
        # 返回完整狀態
        return {
            'raw_text': self.raw_text,
            'quick_inputs': self.quick_inputs,
            'director_settings': self.director_settings,
            'viewer_settings': self.viewer_settings,
            'speech_user': self.speech_user,
            'interim_text': self.interim_text
        }

    def update_script(self, new_raw_text):
        self.raw_text = new_raw_text

    def update_quick_inputs(self, new_inputs):
        for key, value in new_inputs.items():
            if key in self.quick_inputs:
                self.quick_inputs[key] = str(value)

    def _update_settings_helper(self, settings_dict, new_settings: dict):
        if not isinstance(new_settings, dict):
            return
        # 僅更新已知鍵，避免注入
        for k in ['fontFamily','fontSize','lineHeight','fontStyle','fontWeight','color','backgroundColor','theme']:
            if k in new_settings:
                settings_dict[k] = new_settings[k]

    def update_director_settings(self, new_settings: dict):
        self._update_settings_helper(self.director_settings, new_settings)
        if not isinstance(new_settings, dict):
            return
        speech_break_mode = new_settings.get('speechBreakMode')
        if speech_break_mode in {'join', 'newline', 'double-newline'}:
            self.director_settings['speechBreakMode'] = speech_break_mode
        if 'editIdleSeconds' in new_settings:
            try:
                idle_seconds = int(new_settings.get('editIdleSeconds', 3))
            except (TypeError, ValueError):
                idle_seconds = 3
            self.director_settings['editIdleSeconds'] = max(1, min(60, idle_seconds))

    def update_viewer_settings(self, new_settings: dict):
        self._update_settings_helper(self.viewer_settings, new_settings)
        if not isinstance(new_settings, dict):
            return
        if 'forceScrollBottom' in new_settings:
            value = new_settings['forceScrollBottom']
            if isinstance(value, str):
                self.viewer_settings['forceScrollBottom'] = value.lower() in {'1', 'true', 'yes', 'on'}
            else:
                self.viewer_settings['forceScrollBottom'] = bool(value)

    def patch_script(self, patch_text):
        try:
            patches = dmp.patch_fromText(patch_text)
            new_text, results = dmp.patch_apply(patches, self.raw_text)
            if all(results):
                self.raw_text = new_text
                return True
            else:
                print(f"補丁應用失敗: {results}")
                return False
        except Exception as e:
            print(f"應用補丁時發生錯誤: {e}")
            return False

# --------------------
# 房間管理
# --------------------
rooms = {}
viewer_to_room = {}
lock = threading.Lock()

# --------------------
# 網頁路由
# --------------------
@app.route('/')
def home():
    return """
    <style>
      :root { --bg: #f4f7fb; --card: #ffffff; --line: #dbe3ee; --ink: #17212f; --muted: #4b5b72; --accent: #1565d8; --warn: #fff7e5; --warn-line: #f2d28b; }
      * { box-sizing: border-box; }
      body { margin: 0; background: radial-gradient(circle at 0% 0%, #eaf2ff 0, #f4f7fb 45%, #eef3fb 100%); color: var(--ink); font-family: "Microsoft JhengHei", "PingFang TC", "Noto Sans TC", sans-serif; }
      .wrap { max-width: 980px; margin: 32px auto 48px; padding: 0 18px; }
      .hero { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 22px 24px; box-shadow: 0 6px 18px rgba(23,33,47,0.06); }
      h1 { margin: 0 0 10px; font-size: 30px; }
      .sub { margin: 0; font-size: 17px; color: var(--muted); line-height: 1.7; }
      .title { margin: 24px 0 12px; font-size: 22px; font-weight: 700; }
      .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
      .card h3 { margin: 0 0 10px; font-size: 18px; }
      .card p { margin: 0; color: var(--muted); line-height: 1.75; }
      .warn { margin-top: 12px; background: var(--warn); border: 1px solid var(--warn-line); border-radius: 10px; padding: 10px 12px; font-size: 15px; line-height: 1.7; }
      .cta { margin-top: 24px; display: inline-block; text-decoration: none; background: var(--accent); color: #fff; font-size: 20px; font-weight: 700; padding: 12px 20px; border-radius: 10px; box-shadow: 0 6px 14px rgba(21,101,216,0.28); }
      @media (max-width: 760px) { .grid { grid-template-columns: 1fr; } h1 { font-size: 26px; } .sub { font-size: 16px; } }
    </style>
    <div class="wrap">
      <section class="hero">
        <h1>協作聽打工具</h1>
        <p class="sub">本工具係為了降低人工聽打負擔而開發，整合語音轉錄、多人同步編修與觀眾字幕分享。</p>
      </section>
      <h2 class="title">使用建議（30 秒上手）</h2>
      <section class="grid">
        <article class="card">
          <h3>1. 語音轉錄端（建議 Edge）</h3>
          <p>請使用 Edge 瀏覽器啟動語音轉錄以取得最佳效果。<br>同一房間同時間僅一位可啟動語音辨識，且請勿由語音轉錄端進行文字編修，避免轉錄與編輯互相干擾。</p>
        </article>
        <article class="card">
          <h3>2. 編修端加入同房</h3>
          <p>請複製目前網址給其他編修者開啟，即可進入同一房間。<br>若同一台電腦要編修，可改用其他瀏覽器（例如 Chrome）開啟相同網址。<br>語音進行中僅辨識端可切換段落模式（連續／換行／空行）。</p>
        </article>
        <article class="card">
          <h3>3. 編修同步規則</h3>
          <p>人工編輯端點入文本後會暫停落地同步。<br>按 Esc 可立即退出編輯模式，或在閒置 3 秒未動作時自動退出；退出後會恢復同步到最新內容。</p>
        </article>
        <article class="card">
          <h3>4. 觀眾字幕分享</h3>
          <p>可在上方開啟觀眾端連結與 QR Code，分享給需要觀看字幕的觀眾。</p>
        </article>
      </section>
      <div class="warn">重要提醒：語音轉錄端與人工編修端分工操作時，整體穩定性與體感最佳。</div>
      <a class="cta" href="/new_room">建立新房間</a>
    </div>
    """

@app.route('/new_room')
def new_room():
    director_id = str(uuid.uuid4().hex)[:6]
    viewer_id = str(uuid.uuid4().hex)[:6]
    with lock:
        rooms[director_id] = {
            'manager': ScriptManager(),
            'last_active': datetime.datetime.now(),
            'directors': set(),
            'viewers': set(),
            'viewer_id': viewer_id
        }
        viewer_to_room[viewer_id] = director_id
    print(f"新房間已建立: director={director_id}, viewer={viewer_id}")
    return redirect(url_for('director_room', room_id=director_id))

@app.route('/room/<string:room_id>')
def director_room(room_id):
    if room_id not in rooms:
        return "房間不存在或已過期！<a href='/'>返回首頁</a>", 404
    return render_template('index.html', room_id=room_id)

@app.route('/view/<string:viewer_id>')
def viewer_room(viewer_id):
    with lock:
        director_id = viewer_to_room.get(viewer_id)
    if not director_id or director_id not in rooms:
        return "房間不存在或已過期！<a href='/'>返回首頁</a>", 404
    return render_template('viewer.html', room_id=director_id)

# --------------------
# 即時通訊事件 (簡化版)
# --------------------
def get_room_manager(room_id):
    with lock:
        room_data = rooms.get(room_id)
    if room_data:
        return room_data.get('manager')
    return None

def update_last_active(room_id):
    with lock:
        if room_id in rooms:
            rooms[room_id]['last_active'] = datetime.datetime.now()

def broadcast_connection_counts(room_id):
    payload = None
    with lock:
        room_data = rooms.get(room_id)
        if room_data:
            director_count = len(room_data.get('directors', set()))
            viewer_count = len(room_data.get('viewers', set()))
            payload = {
                'directors': director_count,
                'viewers': viewer_count,
                'total': director_count + viewer_count
            }
    if payload is not None:
        socketio.emit('connection_update', payload, to=room_id)

@socketio.on('join')
def on_join(data):
    room_id = data.get('room')
    role = data.get('role', 'director')
    if role not in {'director', 'viewer'}:
        role = 'director'
    sid = request.sid
    if room_id and room_id in rooms:
        join_room(room_id)
        with lock:
            room_data = rooms[room_id]
            room_data.setdefault('directors', set()).discard(sid)
            room_data.setdefault('viewers', set()).discard(sid)
            if role == 'viewer':
                room_data['viewers'].add(sid)
            else:
                room_data['directors'].add(sid)
        print(f"客戶端 {sid} 已加入房間 {room_id}（role={role}）")
        broadcast_connection_counts(room_id)
        manager = get_room_manager(room_id)
        if manager:
            state = manager.get_full_state()
            # 回傳 viewer_id 讓導演端可分享
            with lock:
                viewer_id = rooms.get(room_id, {}).get('viewer_id')
            state['viewer_id'] = viewer_id
            emit('state_update', state)
            update_last_active(room_id)
    else:
        print(f"客戶端 {request.sid} 嘗試加入不存在的房間 {room_id}")

@socketio.on('update_script')
def handle_script_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.update_script(data.get('raw_text', ''))
        state = manager.get_full_state()
        emit('state_update', state, to=room_id)
        update_last_active(room_id)

@socketio.on('update_director_settings')
def handle_update_director_settings(data):
    room_id = data.get('room')
    settings = data.get('settings', {})
    manager = get_room_manager(room_id)
    if manager:
        if isinstance(settings, dict) and 'speechBreakMode' in settings:
            with lock:
                speech_owner = manager.speech_user
            if speech_owner and speech_owner != request.sid:
                settings = dict(settings)
                settings.pop('speechBreakMode', None)
        manager.update_director_settings(settings)
        # 廣播給房間內所有導演端
        emit('director_settings_update', { 'settings': manager.director_settings }, to=room_id)
        update_last_active(room_id)

@socketio.on('update_viewer_settings')
def handle_update_viewer_settings(data):
    room_id = data.get('room')
    settings = data.get('settings', {})
    manager = get_room_manager(room_id)
    if manager:
        manager.update_viewer_settings(settings)
        # 廣播給房間內所有連線（導演端與觀眾端）
        emit('viewer_settings_update', { 'settings': manager.viewer_settings }, to=room_id)
        update_last_active(room_id)

@socketio.on('patch_script')
def handle_script_patch(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        patch_text = data.get('patch', '')
        success = manager.patch_script(patch_text)
        if success:
            emit('script_patched', data, to=room_id, include_self=False)
            # Force the patch sender to re-sync against canonical room state.
            # This prevents long-running sessions from drifting after local edits.
            state = manager.get_full_state()
            with lock:
                viewer_id = rooms.get(room_id, {}).get('viewer_id')
            state['viewer_id'] = viewer_id
            emit('state_update', state, to=request.sid)
            update_last_active(room_id)
        else:
            # If patch fails, force a state update to re-sync all clients
            state = manager.get_full_state()
            emit('state_update', state, to=room_id)

@socketio.on('update_quick_inputs')
def handle_quick_inputs_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.update_quick_inputs(data.get('inputs', {}))
        state = manager.get_full_state()
        emit('state_update', state, to=room_id)
        update_last_active(room_id)

@socketio.on('cursor_sync')
def handle_cursor_sync(data):
    room_id = data.get('room')
    if room_id in rooms:
        emit('cursor_update', data, to=room_id, include_self=False)
        update_last_active(room_id)

@socketio.on('interim_text')
def handle_interim_text(data):
    room_id = data.get('room')
    text = data.get('text', '')
    manager = get_room_manager(room_id)
    if manager:
        manager.interim_text = str(text)
        emit('interim_update', { 'text': manager.interim_text }, to=room_id)
        update_last_active(room_id)

@socketio.on('request_speech_start')
def handle_request_speech_start(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if not manager:
        return {'granted': False, 'speech_user': None, 'reason': 'room_not_found'}

    granted = False
    with lock:
        if manager.speech_user in (None, request.sid):
            manager.speech_user = request.sid
            granted = True
        speech_user = manager.speech_user

    emit('speech_state_update', {'speech_user': speech_user}, to=room_id)
    update_last_active(room_id)
    return {'granted': granted, 'speech_user': speech_user}


@socketio.on('request_speech_stop')
def handle_request_speech_stop(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if not manager:
        return {'stopped': False, 'speech_user': None, 'reason': 'room_not_found'}

    stopped = False
    with lock:
        if manager.speech_user == request.sid:
            manager.speech_user = None
            manager.interim_text = ''
            stopped = True
        speech_user = manager.speech_user
        interim_text = manager.interim_text

    emit('speech_state_update', {'speech_user': speech_user}, to=room_id)
    if stopped:
        emit('interim_update', {'text': interim_text}, to=room_id)
    update_last_active(room_id)
    return {'stopped': stopped, 'speech_user': speech_user}

@socketio.on('speech_activity')
def handle_speech_activity(data):
    room_id = data.get('room')
    is_active = data.get('active', False)
    manager = get_room_manager(room_id)
    if manager:
        with lock:
            if is_active:
                # If no one is speaking, grant the lock to the current user
                if manager.speech_user is None:
                    manager.speech_user = request.sid
                    emit('speech_state_update', {'speech_user': manager.speech_user}, to=room_id)
                # If someone else is speaking, do nothing, the frontend will handle the locked state
            else:
                # Only the person holding the lock can release it
                if manager.speech_user == request.sid:
                    manager.speech_user = None
                    manager.interim_text = ''
                    emit('speech_state_update', {'speech_user': None}, to=room_id)
                    emit('interim_update', {'text': ''}, to=room_id)
        update_last_active(room_id)


@socketio.on('ping')
def handle_ping(data):
    room_id = data.get('room')
    if room_id and room_id in rooms:
        emit('pong', {'timestamp': data.get('timestamp')})
        update_last_active(room_id)

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"客戶端已離線: {sid}")
    room_to_update = None
    speech_lock_released = False
    clear_interim = False
    with lock:
        for room_id, data in rooms.items():
            is_director = sid in data.get('directors', set())
            is_viewer = sid in data.get('viewers', set())
            if is_director or is_viewer:
                if is_director:
                    data['directors'].remove(sid)
                if is_viewer:
                    data['viewers'].remove(sid)
                room_to_update = room_id
                
                # Check and release speech lock
                manager = data.get('manager')
                if manager and manager.speech_user == sid:
                    manager.speech_user = None
                    manager.interim_text = ''
                    speech_lock_released = True
                    clear_interim = True
                break
    if room_to_update:
        if speech_lock_released:
            socketio.emit('speech_state_update', {'speech_user': None}, to=room_to_update)
        if clear_interim:
            socketio.emit('interim_update', {'text': ''}, to=room_to_update)
        # 【新增】廣播使用者離線事件，以便前端移除其游標
        socketio.emit('user_disconnected', {'id': sid}, to=room_to_update)
        # 更新在線人數（這個您已經有了）
        broadcast_connection_counts(room_to_update)

# --------------------
# 房間清理機制
# --------------------
CLEANUP_INTERVAL_SECONDS = 60 * 5
INACTIVITY_TIMEOUT_SECONDS = 60 * 60 * 24

def cleanup_inactive_rooms():
    """定期檢查並刪除不活躍的房間"""
    print("啟動房間清理線程...")
    while True:
        now = datetime.datetime.now()
        rooms_to_delete = []
        with lock:
            for director_id, data in rooms.items():
                last_active_time = data['last_active']
                if (now - last_active_time).total_seconds() > INACTIVITY_TIMEOUT_SECONDS:
                    rooms_to_delete.append(director_id)
        if rooms_to_delete:
            print(f"發現不活躍房間，將刪除: {', '.join(rooms_to_delete)}")
            with lock:
                # 為了安全地獲取 viewer_id，我們先收集它們
                viewer_ids_to_delete = []
                for director_id in rooms_to_delete:
                    if director_id in rooms:
                        # 在刪除房間前，先取得 viewer_id
                        viewer_id = rooms[director_id].get('viewer_id')
                        if viewer_id:
                            viewer_ids_to_delete.append(viewer_id)
                        
                        del rooms[director_id]
                        print(f"房間 {director_id} 已被清理。")
                
                # 現在清理 viewer_to_room
                for viewer_id in viewer_ids_to_delete:
                    if viewer_id in viewer_to_room:
                        del viewer_to_room[viewer_id]
                        print(f"觀眾連結 {viewer_id} 已被清理。")
        time.sleep(CLEANUP_INTERVAL_SECONDS)


def start_cleanup_thread():
    cleanup_thread = threading.Thread(target=cleanup_inactive_rooms, daemon=True, name='room-cleanup')
    cleanup_thread.start()


start_cleanup_thread()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8080, debug=True)
