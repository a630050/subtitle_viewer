import os
import uuid
import secrets  # [NEW] 用於生成安全的隨機字串
import datetime
import threading
import time
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import diff_match_patch as dmp_module

# --------------------
# 應用程式設定
# --------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_super_secret_key!')
socketio = SocketIO(app, cors_allowed_origins="*")
dmp = dmp_module.diff_match_patch()

# --------------------
# 核心狀態管理
# --------------------
BOOKMARK_CHAR = "§"

class ScriptManager:
    """每個「房間」都會有一個獨立的 ScriptManager 實例。"""
    def __init__(self):
        self.raw_text = "# 歡迎使用字幕神器！\n\n§ 導播端核心操作\n*   推播字幕：使用鍵盤 ↑ ↓ 鍵，或用滑鼠單擊左側行號，即可推播該行。\n*   進入編輯：按 Enter 或 → 鍵，或在編輯區內任一處單擊滑鼠左鍵。\n*   退出編輯：在編輯模式中，按 Esc 鍵可快速跳出。\n*   黑屏/恢復：在非編輯狀態下，按 Space 空白鍵可切換黑屏或恢復顯示。\n\n§ 進階編輯與書籤\n*   即時推播：編輯時，將游標移至目標行，按 Insert 鍵或滑鼠右鍵可立即推播。\n*   書籤標記：用滑鼠「雙擊」左側行號，可新增或移除書籤，方便快速跳轉。\n\n§ 強大的視覺與推播設定\n*   字體縮放：使用鍵盤 Ctrl 搭配 + 或 - 鍵，可隨時調整觀眾端字體大小。\n*   排版設定：在右側面板可調整字體、顏色、對齊、邊距等細節。\n*   推播設定：可設定「顯示行數」、「過渡動畫」與三種「投放模式」。\n    *   人工投播：預設模式，手動控制。\n    *   自動最新行：語音或打字時，自動推播最新一行。\n    *   跟隨游標：推播位置會跟隨您在編輯區的游標所在行。\n\n§ 語音識別 (AI 聽寫)\n*   啟用：點擊「開始語音識別」按鈕，並授權瀏覽器使用麥克風。\n*   操作：可隨時開始/停止，並支援多國語言(中/英/日)切換。\n*   自動標點與換行：AI 會根據您的語氣停頓，智慧地加入標點符號與換行。\n*   【重要提示】\n*   此功能仰賴瀏覽器內建的語音服務引擎。\n*   為了獲得最佳的識別率與穩定性，我們強烈推薦使用微軟 Edge 瀏覽器。\n*   在 Chrome 中也能使用，但 Edge 的表現通常更為出色。\n\n§ 分享、匯出與匯入\n*   分享字幕：點擊右上角的「分享字幕」按鈕，即可獲得安全的觀眾端連結與 QR Code。\n*   文本操作：可隨時「匯入」本地文檔、「匯出」當前腳本，或「清除」所有內容。\n*   組態存取：可將您精心調整的「排版與推播設定」儲存為組態檔，方便下次載入使用。\n\n# 準備就緒，現在就開始您的精彩演說吧！"
        self.lines = []
        self.bookmarks = {}
        self.current_index = 0
        self.style_settings = { 'font_size': 100, 'fg_color': '#FFFF00', 'bg_color': '#000000', 'font_family': "'Microsoft JhengHei', '蘋方-繁', sans-serif", 'text_align': 'left', 'margin': 100, 'vertical_align': 'center', 'font_variant': 'normal' }
        self.push_settings = { 'display_lines': 1, 'transition_mode': 'direct', 'broadcast_mode': 'manual' }
        self.parse_raw_text()

    def parse_raw_text(self):
        self.lines = self.raw_text.splitlines()
        self.bookmarks = {}
        for i, line in enumerate(self.lines):
            if line.strip().startswith(BOOKMARK_CHAR):
                clean_line = line.lstrip(BOOKMARK_CHAR).strip()
                self.bookmarks[i] = clean_line if clean_line else f"書籤 {i+1}"
        if self.current_index >= len(self.lines): self.current_index = max(0, len(self.lines) - 1)

    def get_full_state(self):
        return { 'raw_text': self.raw_text, 'lines': self.lines, 'bookmarks': self.bookmarks, 'current_index': self.current_index, 'style_settings': self.style_settings, 'push_settings': self.push_settings }

    def update_script(self, new_raw_text): self.raw_text = new_raw_text; self.parse_raw_text()
    def set_index(self, new_index):
        if 0 <= new_index < len(self.lines): self.current_index = new_index
        elif not self.lines: self.current_index = 0
    def update_styles(self, new_styles): self.style_settings.update(new_styles)

    def update_push_settings(self, new_settings):
        if 'display_lines' in new_settings:
            try:
                val = int(new_settings['display_lines'])
                if 1 <= val <= 10: self.push_settings['display_lines'] = val
            except (ValueError, TypeError): pass
        if 'transition_mode' in new_settings and new_settings['transition_mode'] in ['fade', 'direct', 'scroll', 'scroll-normal']:
            self.push_settings['transition_mode'] = new_settings['transition_mode']
        if 'broadcast_mode' in new_settings and new_settings['broadcast_mode'] in ['manual', 'automatic', 'follow_cursor']:
            self.push_settings['broadcast_mode'] = new_settings['broadcast_mode']

    def patch_script(self, patch_text):
        """應用補丁來更新 raw_text"""
        try:
            patches = dmp.patch_fromText(patch_text)
            new_text, results = dmp.patch_apply(patches, self.raw_text)
            if all(results):
                self.raw_text = new_text
                self.parse_raw_text()
                return True
            else:
                print(f"補丁應用失敗: {results}")
                return False
        except Exception as e:
            print(f"應用補丁時發生錯誤: {e}")
            return False

# --------------------
# 房間管理 [MODIFIED]
# --------------------
rooms = {}  # director_id -> {'manager': ScriptManager(), 'viewer_id': str, 'last_active': datetime}
viewer_to_director = {}  # viewer_id -> director_id 的映射表
lock = threading.Lock()

# --------------------
# 網頁路由 [MODIFIED]
# --------------------
@app.route('/')
def home():
    return """<h1>字幕神器</h1><p>點選下方連結來建立一個新的、獨立的提詞房間。</p><a href=\"/new_room\" style=\"font-size: 20px; padding: 10px 20px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px;\">建立新房間</a>"""

@app.route('/new_room')
def new_room():
    director_id = str(uuid.uuid4().hex)[:8]  # 導播端 ID (8位)
    viewer_id = secrets.token_urlsafe(12)    # 觀眾端 ID (16位隨機字串)
    
    with lock:
        rooms[director_id] = {
            'manager': ScriptManager(),
            'viewer_id': viewer_id,
            'last_active': datetime.datetime.now(),
            'directors': set(),
            'viewers': set()
        }
        viewer_to_director[viewer_id] = director_id
    
    print(f"新房間已建立: 導播端={director_id}, 觀眾端={viewer_id}")
    return redirect(url_for('director_room', room_id=director_id))

@app.route('/room/<string:room_id>')
def director_room(room_id):
    if room_id not in rooms: 
        return "房間不存在或已過期！<a href='/'>返回首頁</a>", 404
    return render_template('index.html', room_id=room_id)

# [MODIFIED] 觀眾端路由改用 viewer_id
@app.route('/viewer/<string:viewer_id>')
def viewer_room(viewer_id):
    with lock:
        director_id = viewer_to_director.get(viewer_id)
    
    if not director_id or director_id not in rooms:
        return "字幕房間不存在或已過期！<a href='/'>返回首頁</a>", 404
    
    # 傳遞 director_id 給模板，讓前端知道要連接哪個房間
    return render_template('viewer.html', room_id=director_id, viewer_id=viewer_id)

# -------------------- 
# 即時通訊事件
# --------------------
def get_room_manager(room_id):
    with lock:
        room_data = rooms.get(room_id)
    if room_data:
        return room_data.get('manager')
    return None

def update_last_active(room_id):
    """輔助函式，用於更新房間的最後活動時間"""
    with lock:
        if room_id in rooms:
            rooms[room_id]['last_active'] = datetime.datetime.now()

def broadcast_connection_counts(room_id):
    """廣播連線數量給房間內的所有客戶端"""
    with lock:
        if room_id in rooms:
            director_count = len(rooms[room_id].get('directors', []))
            viewer_count = len(rooms[room_id].get('viewers', []))
            socketio.emit('connection_update', {
                'directors': director_count,
                'viewers': viewer_count
            }, to=room_id)

@socketio.on('join')
def on_join(data):
    room_id = data.get('room')
    client_type = data.get('client_type', 'viewer') # 預設為 viewer
    sid = request.sid

    if room_id and room_id in rooms:
        join_room(room_id)
        with lock:
            if client_type == 'director':
                rooms[room_id]['directors'].add(sid)
            else:
                rooms[room_id]['viewers'].add(sid)
        
        print(f"客戶端 {sid} ({client_type}) 已加入房間 {room_id}")
        broadcast_connection_counts(room_id) # 廣播更新後的連線數

        manager = get_room_manager(room_id)
        if manager:
            # [NEW] 在狀態中加入 viewer_id，讓導播端知道觀眾端連結
            state = manager.get_full_state()
            with lock:
                state['viewer_id'] = rooms[room_id]['viewer_id']
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
        with lock:
            state['viewer_id'] = rooms[room_id]['viewer_id']
        emit('state_update', state, to=room_id)
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
            update_last_active(room_id)
        else:
            state = manager.get_full_state()
            with lock:
                state['viewer_id'] = rooms[room_id]['viewer_id']
            emit('state_update', state, to=room_id)

@socketio.on('update_index')
def handle_index_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        if 'raw_text' in data:
            manager.update_script(data.get('raw_text', ''))
            
        manager.set_index(data.get('index', 0))
        push_settings_from_data = {}
        if 'display_lines' in data: push_settings_from_data['display_lines'] = data['display_lines']
        if 'transition_mode' in data: push_settings_from_data['transition_mode'] = data['transition_mode']
        if push_settings_from_data: manager.update_push_settings(push_settings_from_data)
        
        state = manager.get_full_state()
        with lock:
            state['viewer_id'] = rooms[room_id]['viewer_id']
        emit('state_update', state, to=room_id)
        update_last_active(room_id)

@socketio.on('update_styles')
def handle_style_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.update_styles(data.get('styles', {}))
        state = manager.get_full_state()
        with lock:
            state['viewer_id'] = rooms[room_id]['viewer_id']
        emit('state_update', state, to=room_id)
        update_last_active(room_id)

@socketio.on('update_push_settings')
def handle_push_settings_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.update_push_settings(data.get('settings', {}))
        state = manager.get_full_state()
        with lock:
            state['viewer_id'] = rooms[room_id]['viewer_id']
        emit('state_update', state, to=room_id)
        update_last_active(room_id)

@socketio.on('cursor_sync')
def handle_cursor_sync(data):
    room_id = data.get('room')
    if room_id in rooms:
        emit('cursor_update', data, to=room_id, include_self=False)
        update_last_active(room_id)

@socketio.on('editor_change')
def handle_editor_change(data):
    room_id = data.get('room')
    if room_id in rooms:
        emit('editor_update', data, to=room_id, include_self=False)
        update_last_active(room_id)

@socketio.on('send_content')
def handle_send_content(data):
    room_id = data.get('room')
    text = data.get('text', '')
    if room_id in rooms:
        emit('force_subtitle', {'text': text}, to=room_id)
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
    with lock:
        # 這邊需要遍歷房間來找到離線的客戶端
        for room_id, data in rooms.items():
            if sid in data.get('directors', set()):
                data['directors'].remove(sid)
                room_to_update = room_id
                break
            elif sid in data.get('viewers', set()):
                data['viewers'].remove(sid)
                room_to_update = room_id
                break
    
    if room_to_update:
        broadcast_connection_counts(room_to_update)

# -------------------- 
# 房間清理機制 [MODIFIED]
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
                for director_id in rooms_to_delete:
                    if director_id in rooms:
                        # 清理雙向映射
                        viewer_id = rooms[director_id]['viewer_id']
                        if viewer_id in viewer_to_director:
                            del viewer_to_director[viewer_id]
                        del rooms[director_id]
                        print(f"房間 {director_id} 已被清理。")
        
        time.sleep(CLEANUP_INTERVAL_SECONDS)

# -------------------- 
# 程式主入口
# --------------------
if __name__ == '__main__':
    cleanup_thread = threading.Thread(target=cleanup_inactive_rooms, daemon=True)
    cleanup_thread.start()
    
    print("伺服器準備在本機啟動于 http://127.0.0.1:5000")
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
