import os
import uuid # 用於產生隨機的房間 ID
from flask import Flask, render_template, request, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room

# --------------------
# 應用程式設定
# --------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_super_secret_key!')
socketio = SocketIO(app, cors_allowed_origins="*")

# --------------------
# 核心狀態管理 (與之前相同，無需改動)
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
        self.style_settings = { 'font_size': 100, 'fg_color': '#FFFF00', 'bg_color': '#000000', 'font_family': '\'Microsoft JhengHei\', \'蘋方-繁\', sans-serif', 'text_align': 'left', 'margin': 15 }
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
        return { 'raw_text': self.raw_text, 'lines': self.lines, 'bookmarks': self.bookmarks, 'current_index': self.current_index, 'style_settings': self.style_settings }

    def update_script(self, new_raw_text): self.raw_text = new_raw_text; self.parse_raw_text()
    def set_index(self, new_index):
        if 0 <= new_index < len(self.lines): self.current_index = new_index
        elif not self.lines: self.current_index = 0
    def update_styles(self, new_styles): self.style_settings.update(new_styles)

# --------------------
# 房間管理
# --------------------
# 全域的「房間管理員」：一個字典，用來存放所有房間的狀態
# 格式: {'房間ID': ScriptManager實例}
rooms = {}

# --------------------
# 網頁路由 (HTTP Routes)
# --------------------
@app.route('/')
def home():
    """主頁，提供一個建立新房間的按鈕。"""
    return """
    <h1>字幕提詞機</h1>
    <p>點選下方連結來建立一個新的、獨立的提詞房間。</p>
    <a href="/new_room" style="font-size: 20px; padding: 10px 20px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px;">
        建立新房間
    </a>
    """

@app.route('/new_room')
def new_room():
    """產生一個獨特的房間ID，並重導向到該房間的導播頁。"""
    room_id = str(uuid.uuid4().hex)[:6] # 產生一個6位數的隨機ID
    rooms[room_id] = ScriptManager() # 為新房間建立一個新的狀態管理器
    print(f"新房間已建立: {room_id}")
    return redirect(url_for('director_room', room_id=room_id))

@app.route('/room/<string:room_id>')
def director_room(room_id):
    """提供特定房間的「導播」頁面。"""
    if room_id not in rooms:
        # 如果有人嘗試進入一個不存在的房間，可以引導他們回首頁
        return "房間不存在！<a href='/'>返回首頁</a>", 404
    return render_template('index.html', room_id=room_id)

@app.route('/viewer/<string:room_id>')
def viewer_room(room_id):
    """提供特定房間的「觀眾」頁面。"""
    if room_id not in rooms:
        return "房間不存在！<a href='/'>返回首頁</a>", 404
    return render_template('viewer.html', room_id=room_id)

# --------------------
# 即時通訊事件 (WebSocket Events)
# --------------------
def get_room_manager(room_id):
    """一個安全的輔助函式，用來取得指定房間的 ScriptManager 實例。"""
    return rooms.get(room_id)

@socketio.on('join')
def on_join(data):
    """當客戶度端進入一個房間頁面時，由前端呼叫此事件。"""
    room_id = data['room']
    if room_id in rooms:
        join_room(room_id) # 這是 Flask-SocketIO 的核心功能，將使用者加入通訊頻道
        print(f"客戶端 {request.sid} 已加入房間 {room_id}")
        # 立即將該房間的最新狀態傳給剛加入的使用者
        emit('state_update', get_room_manager(room_id).get_full_state())
    else:
        print(f"客戶端 {request.sid} 嘗試加入不存在的房間 {room_id}")

# 注意：所有 handle_* 事件現在都需要一個 room_id
@socketio.on('update_script')
def handle_script_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.update_script(data.get('raw_text', ''))
        # 使用 to=room_id 來確保廣播只發送給同一個房間的人
        emit('state_update', manager.get_full_state(), to=room_id)

@socketio.on('update_index')
def handle_index_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.set_index(data.get('index', 0))
        emit('state_update', manager.get_full_state(), to=room_id)

@socketio.on('update_styles')
def handle_style_update(data):
    room_id = data.get('room')
    manager = get_room_manager(room_id)
    if manager:
        manager.update_styles(data.get('styles', {}))
        emit('state_update', manager.get_full_state(), to=room_id)

@socketio.on('send_content')
def handle_send_content(data):
    """處理來自導播端的即時內容傳送請求（例如黑屏）。"""
    room_id = data.get('room')
    text = data.get('text', '')
    if room_id in rooms:
        # 直接將收到的文字內容廣播到房間，但不儲存為永久狀態
        emit('force_subtitle', {'text': text}, to=room_id)


@socketio.on('ping')
def handle_ping(data):
    """處理客戶端的心跳 ping，回傳 pong"""
    room_id = data.get('room')
    if room_id and room_id in rooms:
        emit('pong', {'timestamp': data.get('timestamp')})

@socketio.on('disconnect')
def handle_disconnect():
    # 當使用者離線時，Flask-SocketIO 會自動將他們從所有房間中移除
    print(f"客戶端已離線: {request.sid}")
    # 注意：我們沒有在這裡刪除房間。房間會一直存在直到伺服器重啟。
    # 對於免費方案，伺服器閑置後會自動休眠並清理記憶體，這剛好可以自動清理舊房間。

# --------------------
# 程式主入口
# --------------------
if __name__ == '__main__':
    print("伺服器準備在本機啟動于 http://127.0.0.1:5000")
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
