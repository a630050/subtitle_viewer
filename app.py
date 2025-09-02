import os
import uuid # 用於產生隨機的房間 ID
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room

# --------------------
# 應用程式設定
# --------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_super_secret_key!')
socketio = SocketIO(app, cors_allowed_origins="*")

# --------------------
# 核心狀態管理
# --------------------
BOOKMARK_CHAR = "§"

class ScriptManager:
    """每個「房間」都會有一個獨立的 ScriptManager 實例。"""
    def __init__(self):
        self.raw_text = "歡迎來到新的提詞房間！\n請開始編輯劇本..."
        self.raw_text = "歡迎使用即時提詞機！\n\n§ 基本操作\n*   推播：使用鍵盤 ↑ ↓ 鍵、或直接用滑鼠單擊左側行號，即可推播該行。\n*   黑屏/恢復：在非編輯狀態下，按空白鍵 (Space) 可切換黑屏與恢復顯示。\n*   編輯：按 Enter 或 → 鍵，或在畫面任一處按滑鼠左鍵，可直接跳入編輯區。\n*   即時推播：在編輯區中，將游標移至目標行，按 Insert 鍵或滑鼠右鍵可立即推播該行。\n*   退出編輯：在編輯區中，按 Esc 鍵可跳出編輯模式。\n\n§ 進階功能\n*   書籤：用滑鼠快速雙擊左側的行號，可以為該行新增或移除書籤，方便快速跳轉。\n*   調整字體：使用鍵盤 Ctrl 搭配 + 或 - 鍵，可隨時調整觀眾端字幕的字體大小。\n\n# 現在，開始您的表演吧！"
        self.lines = []
        self.bookmarks = {}
        self.current_index = 0
        self.style_settings = { 'font_size': 100, 'fg_color': '#FFFF00', 'bg_color': '#000000', 'font_family': '\'Microsoft JhengHei\', \'蘋方-繁\', sans-serif', 'text_align': 'left', 'margin': 100, 'vertical_align': 'center' }
        # [新增] 推播設定，儲存顯示行數、過渡方式與投放模式
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
        # [修改] 將 push_settings 加入廣播的完整狀態中
        return { 'raw_text': self.raw_text, 'lines': self.lines, 'bookmarks': self.bookmarks, 'current_index': self.current_index, 'style_settings': self.style_settings, 'push_settings': self.push_settings }

    def update_script(self, new_raw_text): self.raw_text = new_raw_text; self.parse_raw_text()
    def set_index(self, new_index):
        if 0 <= new_index < len(self.lines): self.current_index = new_index
        elif not self.lines: self.current_index = 0
    def update_styles(self, new_styles): self.style_settings.update(new_styles)

    # [新增] 更新推播設定的方法，並包含基本驗證
    def update_push_settings(self, new_settings):
        if 'display_lines' in new_settings:
            try:
                val = int(new_settings['display_lines'])
                if 1 <= val <= 10: self.push_settings['display_lines'] = val
            except (ValueError, TypeError): pass
        if 'transition_mode' in new_settings and new_settings['transition_mode'] in ['fade', 'direct', 'scroll', 'scroll-normal']:
            self.push_settings['transition_mode'] = new_settings['transition_mode']
        if 'broadcast_mode' in new_settings and new_settings['broadcast_mode'] in ['manual', 'automatic']:
            self.push_settings['broadcast_mode'] = new_settings['broadcast_mode']

# --------------------
# 房間管理 (無需改動)
# --------------------
rooms = {}

# --------------------
# 網頁路由 (HTTP Routes) (無需改動)
# --------------------
@app.route('/')
def home():
    return """<h1>字幕提詞機</h1><p>點選下方連結來建立一個新的、獨立的提詞房間。</p><a href="/new_room" style="font-size: 20px; padding: 10px 20px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px;">建立新房間</a>"""

@app.route('/new_room')
def new_room():
    room_id = str(uuid.uuid4().hex)[:6]
    rooms[room_id] = ScriptManager()
    print(f"新房間已建立: {room_id}")
    return redirect(url_for('director_room', room_id=room_id))

@app.route('/room/<string:room_id>')
def director_room(room_id):
    if room_id not in rooms: return "房間不存在！<a href='/'>返回首頁</a>", 404
    return render_template('index.html', room_id=room_id)

@app.route('/viewer/<string:room_id>')
def viewer_room(room_id):
    if room_id not in rooms: return "房間不存在！<a href='/'>返回首頁</a>", 404
    return render_template('viewer.html', room_id=room_id)

# --------------------
# 即時通訊事件 (WebSocket Events)
# --------------------
def get_room_manager(room_id):
    return rooms.get(room_id)

@socketio.on('join')
def on_join(data):
    room_id = data['room']
    if room_id in rooms:
        join_room(room_id)
        print(f"客戶端 {request.sid} 已加入房間 {room_id}")
        emit('state_update', get_room_manager(room_id).get_full_state())
    else:
        print(f"客戶端 {request.sid} 嘗試加入不存在的房間 {room_id}")

@socketio.on('update_script')
def handle_script_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.update_script(data.get('raw_text', ''))
        emit('state_update', manager.get_full_state(), to=room_id)

@socketio.on('update_index')
def handle_index_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        # 如果請求中包含文本(raw_text)，則先更新腳本內容。
        # 這能解決在語音辨識等快速操作下，客戶端新增行後，索引更新(update_index)比文本更新(update_script)先抵達伺服器所造成的競爭條件(race condition)問題。
        if 'raw_text' in data:
            manager.update_script(data.get('raw_text', ''))
            
        manager.set_index(data.get('index', 0))
        # [修改] 當推播時，一併更新推播設定，確保狀態同步
        push_settings_from_data = {}
        if 'display_lines' in data: push_settings_from_data['display_lines'] = data['display_lines']
        if 'transition_mode' in data: push_settings_from_data['transition_mode'] = data['transition_mode']
        if push_settings_from_data: manager.update_push_settings(push_settings_from_data)
        
        emit('state_update', manager.get_full_state(), to=room_id)

@socketio.on('update_styles')
def handle_style_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.update_styles(data.get('styles', {}))
        emit('state_update', manager.get_full_state(), to=room_id)

# [新增] 處理推播設定更新的事件
@socketio.on('update_push_settings')
def handle_push_settings_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.update_push_settings(data.get('settings', {}))
        emit('state_update', manager.get_full_state(), to=room_id)

# [新增] 處理協作編輯的事件
@socketio.on('editor_change')
def handle_editor_change(data):
    room_id = data.get('room')
    if room_id in rooms:
        emit('editor_update', data, to=room_id, include_self=False)

@socketio.on('send_content')
def handle_send_content(data):
    room_id = data.get('room')
    text = data.get('text', '')
    if room_id in rooms:
        emit('force_subtitle', {'text': text}, to=room_id)

@socketio.on('ping')
def handle_ping(data):
    room_id = data.get('room')
    if room_id and room_id in rooms: emit('pong', {'timestamp': data.get('timestamp')})

@socketio.on('disconnect')
def handle_disconnect():
    print(f"客戶端已離線: {request.sid}")

# --------------------
# 程式主入口 (無需改動)
# --------------------
if __name__ == '__main__':
    print("伺服器準備在本機啟動于 http://127.0.0.1:5000")
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
